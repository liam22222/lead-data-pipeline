"""SQLite state, so runs are incremental: new leads are flagged, fresh ones aren't re-crawled."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from leadpipe.models import Lead, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    key TEXT PRIMARY KEY,
    niche TEXT NOT NULL,
    city TEXT,
    score REAL,
    hot INTEGER,
    first_seen TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT NOT NULL,
    finished TEXT,
    niche TEXT,
    city TEXT,
    found INTEGER,
    new INTEGER,
    hot INTEGER,
    ai_scored INTEGER,
    errors INTEGER
);
"""


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def get(self, key: str) -> Lead | None:
        row = self.db.execute("SELECT data FROM leads WHERE key = ?", (key,)).fetchone()
        return Lead(**json.loads(row[0])) if row else None

    def is_fresh(self, lead: Lead, max_age_days: float) -> bool:
        try:
            updated = datetime.fromisoformat(lead.last_updated)
        except ValueError:
            return False
        return datetime.now(timezone.utc) - updated < timedelta(days=max_age_days)

    def upsert(self, lead: Lead, niche: str) -> None:
        existing = self.get(lead.key)
        lead.first_seen = existing.first_seen if existing else lead.first_seen
        lead.new_this_run = existing is None
        self.db.execute(
            """INSERT INTO leads (key, niche, city, score, hot, first_seen, last_updated, data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET score=excluded.score, hot=excluded.hot,
                   last_updated=excluded.last_updated, data=excluded.data""",
            (lead.key, niche, lead.city, lead.score, int(lead.hot), lead.first_seen, lead.last_updated,
             lead.model_dump_json()),
        )

    def start_run(self, niche: str, city: str) -> int:
        cur = self.db.execute("INSERT INTO runs (started, niche, city) VALUES (?, ?, ?)", (now(), niche, city))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, **stats: int) -> None:
        self.db.execute(
            "UPDATE runs SET finished=?, found=?, new=?, hot=?, ai_scored=?, errors=? WHERE id=?",
            (now(), stats.get("found"), stats.get("new"), stats.get("hot"), stats.get("ai_scored"),
             stats.get("errors"), run_id),
        )
        self.db.commit()

    def commit(self) -> None:
        self.db.commit()
