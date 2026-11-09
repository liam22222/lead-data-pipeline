"""Find businesses with Google Places API (New) Text Search.

Website, phone, rating and review count are "Enterprise" fields: 1,000 free calls a month per billing
account, and each call returns up to 20 places, so one city costs about a dozen calls.
https://developers.google.com/maps/documentation/places/web-service/text-search
"""
from __future__ import annotations

import logging
import time

from leadpipe.config import Niche
from leadpipe.http import HttpClient
from leadpipe.models import Lead

log = logging.getLogger(__name__)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.location",
    "places.websiteUri", "places.nationalPhoneNumber", "places.internationalPhoneNumber",
    "places.rating", "places.userRatingCount", "places.businessStatus", "places.primaryType",
    "nextPageToken",
])
MAX_PAGES = 3  # Google stops at 60 results per query


def parse_places(payload: dict, city: str | None, category: str) -> list[Lead]:
    leads = []
    for p in payload.get("places", []):
        if p.get("businessStatus") == "CLOSED_PERMANENTLY":
            continue
        loc = p.get("location", {})
        leads.append(Lead(
            source="google",
            source_id=f"google/{p['id']}",
            name=(p.get("displayName") or {}).get("text", "").strip() or "Unknown",
            category=category,
            website=p.get("websiteUri"),
            phone=p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber"),
            address=p.get("formattedAddress"),
            city=city,
            lat=loc.get("latitude"),
            lon=loc.get("longitude"),
            rating=p.get("rating"),
            review_count=p.get("userRatingCount"),
            business_status=p.get("businessStatus"),
        ))
    return leads


def fetch_leads(city: str, niche: Niche, http: HttpClient, api_key: str, max_pages: int = MAX_PAGES) -> list[Lead]:
    headers = {"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELDS, "Content-Type": "application/json"}
    leads: list[Lead] = []
    calls = 0
    for term in niche.google_queries:
        token = None
        for _ in range(max_pages):
            body = {"textQuery": f"{term} in {city}", "pageSize": 20}
            if token:
                body["pageToken"] = token
            resp = http.post(SEARCH_URL, json=body, headers=headers, timeout=30)
            calls += 1
            if resp.status_code >= 400:
                raise RuntimeError(f"Google Places error {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            leads += parse_places(data, city, niche.name)
            token = data.get("nextPageToken")
            if not token:
                break
            time.sleep(1)  # the next page token needs a moment to become valid
    log.info("Google Places: %d results from %d calls (%d free Enterprise calls a month)", len(leads), calls, 1000)
    return leads
