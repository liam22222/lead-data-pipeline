"""Niche config (TOML) and environment settings."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from dotenv import load_dotenv


@dataclass
class Scoring:
    has_website: float = 2
    has_email: float = 2
    has_phone: float = 1
    no_online_booking: float = 3
    premium_service: float = 0.5
    chain_penalty: float = -4
    site_unreachable: float = -1
    hot_threshold: float = 7


@dataclass
class Niche:
    name: str
    osm_tags: list[tuple[str, str]]
    ideal_customer_profile: str
    chain_keywords: list[str] = field(default_factory=list)
    online_booking: list[str] = field(default_factory=list)
    premium_services: list[str] = field(default_factory=list)
    scoring: Scoring = field(default_factory=Scoring)

    @classmethod
    def load(cls, path: str | Path) -> "Niche":
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        signals = data.get("signals", {})
        return cls(
            name=data["name"],
            osm_tags=[(k, v) for k, v in data["osm_tags"]],
            ideal_customer_profile=data["ideal_customer_profile"].strip(),
            chain_keywords=[s.lower() for s in data.get("chain_keywords", [])],
            online_booking=[s.lower() for s in signals.get("online_booking", [])],
            premium_services=[s.lower() for s in signals.get("premium_services", [])],
            scoring=Scoring(**data.get("scoring", {})),
        )


@dataclass
class Settings:
    anthropic_api_key: str | None
    anthropic_model: str
    slack_webhook_url: str | None
    contact_email: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            anthropic_model=os.getenv("ANTHROPIC_MODEL") or "claude-haiku-5-5",
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL") or None,
            contact_email=os.getenv("CONTACT_EMAIL") or "",
        )

    @property
    def user_agent(self) -> str:
        # OSM services reject generic agents; identify the app and, if set, a real contact.
        contact = self.contact_email if self.contact_email and "example." not in self.contact_email else ""
        return f"leadpipe/0.1 (+https://github.com/liam22222/lead-data-pipeline{'; ' + contact if contact else ''})"
