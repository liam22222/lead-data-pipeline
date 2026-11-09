"""The Lead record that flows through every stage."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Lead(BaseModel):
    # Identity
    key: str = ""                      # dedup key, set by normalize
    source: str = "osm"
    source_id: str = ""                # e.g. "node/123456"
    name: str
    category: str = ""

    # Contact + location (from the source, then enrichment)
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    lat: float | None = None
    lon: float | None = None
    opening_hours: str | None = None

    # Enrichment
    site_status: str = "not_checked"   # ok | unreachable | no_website | not_checked
    site_title: str | None = None
    emails_found: list[str] = Field(default_factory=list)
    phones_found: list[str] = Field(default_factory=list)
    socials: dict[str, str] = Field(default_factory=dict)
    has_contact_form: bool = False
    online_booking: bool = False
    booking_signals: list[str] = Field(default_factory=list)
    premium_services: list[str] = Field(default_factory=list)
    site_platform: str | None = None   # wordpress, wix, squarespace...
    is_chain: bool = False

    # Scoring
    rule_score: float | None = None
    rule_reasons: list[str] = Field(default_factory=list)
    ai_score: float | None = None
    ai_reason: str | None = None
    ai_angle: str | None = None         # suggested sales angle
    score: float | None = None          # final: AI if available, else rules
    hot: bool = False

    # Bookkeeping
    first_seen: str = Field(default_factory=now)
    last_updated: str = Field(default_factory=now)
    new_this_run: bool = False
