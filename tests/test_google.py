import json

from leadpipe.normalize import normalize
from leadpipe.pipeline import flag_chains
from leadpipe.scoring import rule_score
from leadpipe.sources.google import FIELDS, fetch_leads, parse_places
from tests.conftest import FIX, FakeResponse

PAYLOAD = json.loads((FIX / "google_places.json").read_text())


def test_parse_places_skips_closed_and_maps_fields():
    leads = parse_places(PAYLOAD, "Austin, TX", "Dental clinics")
    assert [l.name for l in leads][:2] == ["Riverbend Family Dental", "Smile Co. Downtown"]
    assert "Closed Teeth" not in [l.name for l in leads]
    r = leads[0]
    assert (r.rating, r.review_count, r.source, r.source_id) == (4.9, 31, "google", "google/g1")
    assert normalize(r).phone == "+15125550101"


def test_shared_website_marks_chain():
    leads = [normalize(l) for l in parse_places(PAYLOAD, "Austin", "Dental clinics")]
    flag_chains(leads, 3)
    chain = {l.name: l.is_chain for l in leads}
    assert chain["Smile Co. North"] and not chain["Riverbend Family Dental"]


def test_few_reviews_adds_points(niche):
    lead = normalize(parse_places(PAYLOAD, "Austin", "x")[0])
    lead.site_status = "ok"
    score, reasons = rule_score(lead, niche)
    assert any("31 Google reviews" in r for r in reasons)


def test_fetch_pages_through_queries(niche, monkeypatch):
    monkeypatch.setattr("leadpipe.sources.google.time.sleep", lambda s: None)
    bodies = []

    class Http:
        def post(self, url, json=None, headers=None, **kw):
            bodies.append(json)
            assert headers["X-Goog-FieldMask"] == FIELDS and headers["X-Goog-Api-Key"] == "k"
            page = dict(PAYLOAD)
            if json.get("pageToken"):
                page.pop("nextPageToken")          # second page is the last one
            return FakeResponse(url, data=page)

    leads = fetch_leads("Austin, TX", niche, Http(), "k")
    assert len(bodies) == 2 * len(niche.google_queries)
    assert bodies[0] == {"textQuery": "dentist in Austin, TX", "pageSize": 20}
    assert bodies[1]["pageToken"] == "page2"
    assert len(leads) == 4 * len(bodies)
