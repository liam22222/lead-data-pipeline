"""source -> normalize/dedupe -> enrich -> score -> store -> deliver"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from leadpipe import outputs
from leadpipe.config import Niche, Settings
from leadpipe.enrich import enrich_all
from leadpipe.http import HttpClient
from leadpipe.models import Lead, now
from leadpipe.normalize import dedupe
from leadpipe.scoring import score_all
from leadpipe.sources import osm
from leadpipe.store import Store

log = logging.getLogger(__name__)


@dataclass
class RunOptions:
    city: str
    niche_path: Path
    out_dir: Path = Path("output")
    db_path: Path = Path("data/leads.db")
    limit: int | None = None
    refresh_days: float = 14          # re-crawl a website only if older than this
    use_ai: bool = True
    send_slack: bool = True
    osm_file: Path | None = None      # replay a saved Overpass response instead of querying


@dataclass
class RunResult:
    leads: list[Lead]
    csv: Path
    report: Path
    stats: dict


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def run(opts: RunOptions, settings: Settings | None = None, http: HttpClient | None = None) -> RunResult:
    settings = settings or Settings.from_env()
    niche = Niche.load(opts.niche_path)
    http = http or HttpClient(settings.user_agent)
    store = Store(opts.db_path)
    run_id = store.start_run(niche.name, opts.city)

    # 1. Source
    if opts.osm_file:
        raw = osm.parse_elements(json.loads(Path(opts.osm_file).read_text()), city=opts.city, category=niche.name)
    else:
        raw = osm.fetch_leads(opts.city, niche, http)

    # 2. Normalize + dedupe
    leads = dedupe(raw)
    log.info("%d raw -> %d unique leads", len(raw), len(leads))
    if opts.limit:
        leads = leads[: opts.limit]

    # 3. Enrich (skip websites crawled recently: incremental runs are cheap)
    to_enrich, reused = [], []
    for lead in leads:
        known = store.get(lead.key)
        if known and store.is_fresh(known, opts.refresh_days):
            reused.append(known)
        else:
            to_enrich.append(lead)
    log.info("Enriching %d websites (%d still fresh from earlier runs)", len(to_enrich), len(reused))
    leads = enrich_all(to_enrich, niche, http) + reused

    # 4. Score
    cache = opts.db_path.parent / "cache" / "ai_scores.json"
    leads = score_all(leads, niche, settings, http, cache, use_ai=opts.use_ai)

    # 5. Store
    for lead in leads:
        store.upsert(lead, niche.name)
    store.commit()

    # 6. Deliver
    base = opts.out_dir / f"{slug(niche.name)}-{slug(opts.city)}"
    csv_path = outputs.write_csv(leads, base.with_suffix(".csv"))
    report = outputs.write_report(leads, base.with_suffix(".html"), niche.name, opts.city, now()[:16].replace("T", " "))
    s = outputs.stats(leads)
    if opts.send_slack and settings.slack_webhook_url:
        if outputs.send_slack(leads, niche.name, opts.city, settings.slack_webhook_url, http):
            log.info("Posted hot leads to Slack")

    errors = sum(1 for l in leads if l.site_status == "unreachable")
    store.finish_run(run_id, found=s["found"], new=s["new"], hot=s["hot"], ai_scored=s["ai_scored"], errors=errors)
    return RunResult(leads=leads, csv=csv_path, report=report, stats=s)
