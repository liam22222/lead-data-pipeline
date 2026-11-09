"""Clean contact fields and merge duplicates (the same clinic often appears as a node and a building)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from leadpipe.models import Lead

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
JUNK_EMAIL_PARTS = ("example.", "sentry", "wixpress", "domain.com", "email.com", "yourdomain",
                    "@2x", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", "godaddy", "schema.org")
# Shared site builders / directories: a link to these is not the clinic's own domain.
SHARED_HOSTS = ("facebook.com", "instagram.com", "linkedin.com", "yelp.com", "google.com",
                "goo.gl", "linktr.ee", "business.site")


def clean_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip().split(";")[0].strip()
    if not url:
        return None
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    if not host or any(host == s or host.endswith("." + s) for s in SHARED_HOSTS):
        return None
    return host


def clean_phone(phone: str | None, default_country: str = "1") -> str | None:
    """Best-effort E.164: '+1 (512) 555-0100' -> '+15125550100'. Keeps the first number if several."""
    if not phone:
        return None
    first = re.split(r"[;,/]", phone)[0]
    has_plus = first.strip().startswith("+")
    digits = re.sub(r"\D", "", first)
    if len(digits) < 7:
        return None
    if has_plus:
        return "+" + digits
    if default_country == "1" and len(digits) == 10:
        return "+1" + digits
    if default_country == "1" and len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits if len(digits) > 10 else digits


def clean_email(email: str | None) -> str | None:
    if not email:
        return None
    email = email.strip().lower().removeprefix("mailto:").split("?")[0]
    if not EMAIL_RE.fullmatch(email) or any(p in email for p in JUNK_EMAIL_PARTS):
        return None
    return email


def dedup_key(lead: Lead) -> str:
    if d := domain(lead.website):
        return f"web:{d}"
    if lead.phone:
        return f"tel:{lead.phone}"
    name = re.sub(r"[^a-z0-9]", "", lead.name.lower())
    if lead.lat is not None and lead.lon is not None:
        return f"geo:{name}:{round(lead.lat, 3)}:{round(lead.lon, 3)}"
    return f"name:{name}"


def normalize(lead: Lead) -> Lead:
    lead.website = clean_url(lead.website)
    lead.phone = clean_phone(lead.phone)
    lead.email = clean_email(lead.email)
    lead.name = re.sub(r"\s+", " ", lead.name).strip()
    lead.key = dedup_key(lead)
    return lead


def merge(a: Lead, b: Lead) -> Lead:
    """Keep a, fill its gaps from b."""
    data = a.model_dump()
    for field, value in b.model_dump().items():
        if data.get(field) in (None, "", [], {}) and value not in (None, "", [], {}):
            data[field] = value
    return Lead(**data)


def dedupe(leads: list[Lead]) -> list[Lead]:
    by_key: dict[str, Lead] = {}
    for lead in map(normalize, leads):
        by_key[lead.key] = merge(by_key[lead.key], lead) if lead.key in by_key else lead
    return list(by_key.values())
