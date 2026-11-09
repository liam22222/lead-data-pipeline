"""Score leads 0-10: a transparent rule-based score, plus an optional Claude score against the ICP."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from leadpipe.config import Niche, Settings
from leadpipe.http import HttpClient
from leadpipe.models import Lead

log = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def rule_score(lead: Lead, niche: Niche) -> tuple[float, list[str]]:
    s, w = 0.0, niche.scoring
    reasons: list[str] = []

    def add(points: float, why: str) -> None:
        nonlocal s
        s += points
        reasons.append(f"{points:+g} {why}")

    if lead.website and lead.site_status != "unreachable":
        add(w.has_website, "has a website")
    if lead.site_status == "unreachable":
        add(w.site_unreachable, "website unreachable")
    if lead.email:
        add(w.has_email, "direct email")
    elif lead.has_contact_form:
        add(w.has_email / 2, "contact form (no email)")
    if lead.phone:
        add(w.has_phone, "phone")
    if lead.site_status == "ok" and not lead.online_booking:
        add(w.no_online_booking, "no online booking found")
    if lead.premium_services:
        add(min(len(lead.premium_services) * w.premium_service, 2), "premium services: " + ", ".join(lead.premium_services))
    if lead.is_chain:
        add(w.chain_penalty, "looks like a chain/hospital")
    return round(max(0.0, min(10.0, s)), 1), reasons


def lead_facts(lead: Lead) -> dict:
    """Only what the model needs. Keeps prompts small and the cache key stable."""
    return {
        "name": lead.name,
        "city": lead.city,
        "website": lead.website,
        "website_status": lead.site_status,
        "website_title": lead.site_title,
        "has_email": bool(lead.email),
        "has_phone": bool(lead.phone),
        "has_contact_form": lead.has_contact_form,
        "online_booking_found": lead.online_booking,
        "booking_signals": lead.booking_signals,
        "premium_services": lead.premium_services,
        "site_platform": lead.site_platform,
        "socials": sorted(lead.socials),
        "looks_like_chain": lead.is_chain,
    }


SYSTEM = """You qualify B2B sales leads. Score how good this business is as a lead for the buyer
described in the ideal customer profile. Use only the facts given; never invent details.
Reply with JSON only: {"score": <0-10 number>, "reason": "<one sentence, max 25 words>",
"angle": "<one-sentence opening pitch the buyer could use, max 25 words>"}"""


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON in model reply: {text[:200]}")
    data = json.loads(match.group(0))
    data["score"] = max(0.0, min(10.0, float(data["score"])))
    return data


class ClaudeScorer:
    """Scores with the Anthropic Messages API. Results are cached on disk, so re-runs cost nothing."""

    def __init__(self, settings: Settings, niche: Niche, http: HttpClient, cache_path: Path):
        self.settings, self.niche, self.http = settings, niche, http
        self.cache_path = cache_path
        self.cache: dict[str, dict] = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        self._lock = threading.Lock()

    def _key(self, facts: dict) -> str:
        blob = json.dumps([self.settings.anthropic_model, self.niche.ideal_customer_profile, facts], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def score(self, lead: Lead) -> dict | None:
        facts = lead_facts(lead)
        key = self._key(facts)
        if key in self.cache:
            return self.cache[key]
        body = {
            "model": self.settings.anthropic_model,
            "max_tokens": 200,
            "system": SYSTEM,
            "messages": [{"role": "user", "content":
                f"Ideal customer profile:\n{self.niche.ideal_customer_profile}\n\nLead:\n{json.dumps(facts, indent=1)}"}],
        }
        headers = {"x-api-key": self.settings.anthropic_api_key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        try:
            resp = self.http.post(ANTHROPIC_URL, json=body, headers=headers, timeout=60)
            resp.raise_for_status()
            text = "".join(b.get("text", "") for b in resp.json().get("content", []))
            result = _parse_json(text)
        except Exception as e:
            log.warning("AI scoring failed for %s: %s", lead.name, e)
            return None
        with self._lock:
            self.cache[key] = result
        return result

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, indent=1))


def score_all(leads: list[Lead], niche: Niche, settings: Settings, http: HttpClient,
              cache_path: Path, use_ai: bool = True) -> list[Lead]:
    for lead in leads:
        lead.rule_score, lead.rule_reasons = rule_score(lead, niche)

    scorer = None
    if use_ai and settings.anthropic_api_key:
        scorer = ClaudeScorer(settings, niche, http, cache_path)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(scorer.score, leads))
        scorer.save()
        for lead, r in zip(leads, results):
            if r:
                lead.ai_score, lead.ai_reason, lead.ai_angle = round(r["score"], 1), r.get("reason"), r.get("angle")
    elif use_ai:
        log.info("No ANTHROPIC_API_KEY set: using the rule-based score only")

    for lead in leads:
        lead.score = lead.ai_score if lead.ai_score is not None else lead.rule_score
        lead.hot = (lead.score or 0) >= niche.scoring.hot_threshold
    return leads
