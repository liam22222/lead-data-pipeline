import json

from leadpipe.enrich import analyze_page, enrich_lead
from leadpipe.models import Lead
from leadpipe.normalize import normalize
from leadpipe.scoring import ClaudeScorer, _parse_json, rule_score, score_all
from tests.conftest import FIX, FakeHttp, FakeResponse


def test_analyze_homepage(niche):
    page = analyze_page((FIX / "riverbend_home.html").read_text(), "https://riverbend-dental.test", niche)
    assert page["title"].startswith("Riverbend Family Dental")
    assert page["phones"] == ["+15125550101"]
    assert page["emails"] == []                       # logo@2x.png is not an email
    assert set(page["socials"]) == {"facebook", "instagram"}
    assert {"implant", "invisalign", "aligner", "emergency"} <= set(page["services"])
    assert page["booking"] == []
    assert page["platform"] == "WordPress"
    assert page["contact_links"] == ["https://riverbend-dental.test/contact-us"]


def test_detects_booking_widget(niche):
    page = analyze_page((FIX / "hillcountry_home.html").read_text(), "https://hillcountrysmiles.test", niche)
    assert "nexhealth" in page["booking"] and "book online" in page["booking"]
    assert page["emails"] == ["hello@hillcountrysmiles.test"]
    assert page["platform"] == "Wix"


def test_enrich_follows_contact_page(niche, site_pages):
    lead = normalize(Lead(name="Riverbend Family Dental", website="riverbend-dental.test"))
    lead = enrich_lead(lead, niche, FakeHttp(site_pages))
    assert lead.site_status == "ok"
    assert lead.email == "frontdesk@riverbend-dental.test"
    assert lead.has_contact_form
    assert not lead.online_booking


def test_enrich_handles_dead_and_missing_sites(niche):
    http = FakeHttp({})
    dead = enrich_lead(normalize(Lead(name="Broken", website="http://broken.test")), niche, http)
    assert dead.site_status == "unreachable"
    none = enrich_lead(normalize(Lead(name="No Site")), niche, http)
    assert none.site_status == "no_website"


def test_rule_score_prefers_independent_without_booking(niche, site_pages):
    http = FakeHttp(site_pages)
    good = enrich_lead(normalize(Lead(name="Riverbend Family Dental", website="riverbend-dental.test",
                                      phone="512-555-0101")), niche, http)
    chain = enrich_lead(normalize(Lead(name="Aspen Dental", website="https://aspen-dental-austin.test")), niche, http)
    g, reasons = rule_score(good, niche)
    c, _ = rule_score(chain, niche)
    assert g >= 9 and c <= 2
    assert any("no online booking" in r for r in reasons)


def test_parse_json_clamps_and_tolerates_fences():
    assert _parse_json('```json\n{"score": 12, "reason": "x", "angle": "y"}\n```')["score"] == 10


def test_claude_scorer_caches(niche, settings, tmp_path):
    settings.anthropic_api_key = "sk-test"
    calls = []

    def handler(url, json=None, **kw):
        calls.append(json)
        reply = '{"score": 8.5, "reason": "Independent, no booking.", "angle": "Add online booking."}'
        return FakeResponse(url, data={"content": [{"type": "text", "text": reply}]})

    http = FakeHttp({}, post_handler=handler)
    leads = [normalize(Lead(name="A Dental", website="https://a.test"))]
    cache = tmp_path / "cache.json"
    score_all(leads, niche, settings, http, cache)
    assert leads[0].ai_score == 8.5 and leads[0].score == 8.5 and leads[0].hot
    assert "Ideal customer profile" in calls[0]["messages"][0]["content"]

    again = [normalize(Lead(name="A Dental", website="https://a.test"))]
    score_all(again, niche, settings, http, cache)
    assert len(calls) == 1                  # second run served from the cache
    assert json.loads(cache.read_text())


def test_ai_failure_falls_back_to_rules(niche, settings, tmp_path):
    settings.anthropic_api_key = "sk-test"
    http = FakeHttp({}, post_handler=lambda url, **kw: FakeResponse(url, status=500, data={}))
    leads = [normalize(Lead(name="A Dental", website="https://a.test"))]
    score_all(leads, niche, settings, http, tmp_path / "c.json")
    assert leads[0].ai_score is None and leads[0].score == leads[0].rule_score
