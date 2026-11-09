"""Find businesses in a city with OpenStreetMap (Nominatim to locate the city, Overpass to query it).

Both services are free and need no key, but have usage policies: identify yourself (User-Agent with
a contact email), keep it to ~1 request/second, and cache results. See
https://operations.osmfoundation.org/policies/nominatim/ and https://wiki.openstreetmap.org/wiki/Overpass_API
"""
from __future__ import annotations

import logging

from leadpipe.config import Niche
from leadpipe.http import HttpClient
from leadpipe.models import Lead

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
AREA_OFFSET = {"relation": 3_600_000_000, "way": 2_400_000_000}
PHOTON_TYPES = {"R": "relation", "W": "way", "N": "node"}


def _area_from_nominatim(city: str, http: HttpClient) -> int | None:
    resp = http.get(NOMINATIM_URL, params={"q": city, "format": "jsonv2", "limit": 5})
    resp.raise_for_status()
    for place in resp.json():
        offset = AREA_OFFSET.get(place.get("osm_type"))
        if offset and place.get("class") in {"boundary", "place"}:
            return offset + int(place["osm_id"])
    return None


def _area_from_photon(city: str, http: HttpClient) -> int | None:
    resp = http.get(PHOTON_URL, params={"q": city, "limit": 5})
    resp.raise_for_status()
    for feature in resp.json().get("features", []):
        p = feature.get("properties", {})
        offset = AREA_OFFSET.get(PHOTON_TYPES.get(p.get("osm_type"), ""))
        if offset and p.get("osm_key") in {"boundary", "place"}:
            return offset + int(p["osm_id"])
    return None


def find_area_id(city: str, http: HttpClient) -> int:
    """Turn "Austin, TX" into an Overpass area id. Nominatim first, Photon (also OSM data) as backup."""
    for name, lookup in (("Nominatim", _area_from_nominatim), ("Photon", _area_from_photon)):
        try:
            area = lookup(city, http)
            if area:
                return area
            log.info("%s found no city boundary for %r", name, city)
        except Exception as e:
            log.warning("%s lookup failed (%s), trying the next geocoder", name, e)
    raise ValueError(f"Could not find a city boundary for {city!r}. Try a fuller name, e.g. 'Austin, Texas, USA'.")


def build_query(area_id: int, niche: Niche, timeout: int = 90) -> str:
    selectors = "\n".join(f'  nwr["{k}"="{v}"](area.a);' for k, v in niche.osm_tags)
    return f"[out:json][timeout:{timeout}];\narea({area_id})->.a;\n(\n{selectors}\n);\nout center tags;"


def parse_elements(payload: dict, city: str | None = None, category: str = "") -> list[Lead]:
    leads: list[Lead] = []
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("brand")
        if not name:
            continue  # unnamed points can't be contacted
        center = el.get("center", {})
        street = " ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")]))
        address = ", ".join(filter(None, [street, tags.get("addr:city"), tags.get("addr:state"),
                                          tags.get("addr:postcode")])) or None
        leads.append(Lead(
            source="osm",
            source_id=f"{el['type']}/{el['id']}",
            name=name.strip(),
            category=category,
            website=tags.get("website") or tags.get("contact:website") or tags.get("url"),
            phone=tags.get("phone") or tags.get("contact:phone"),
            email=tags.get("email") or tags.get("contact:email"),
            address=address,
            city=tags.get("addr:city") or city,
            lat=el.get("lat", center.get("lat")),
            lon=el.get("lon", center.get("lon")),
            opening_hours=tags.get("opening_hours"),
        ))
    return leads


def fetch_leads(city: str, niche: Niche, http: HttpClient) -> list[Lead]:
    area_id = find_area_id(city, http)
    query = build_query(area_id, niche)
    log.info("Querying Overpass for %s in %s (area %s)", niche.name, city, area_id)
    resp = http.post(OVERPASS_URL, data={"data": query}, timeout=120)
    resp.raise_for_status()
    leads = parse_elements(resp.json(), city=city, category=niche.name)
    log.info("OpenStreetMap returned %d named %s", len(leads), niche.name.lower())
    return leads
