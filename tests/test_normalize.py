import json

from leadpipe.models import Lead
from leadpipe.normalize import clean_email, clean_phone, clean_url, dedupe, domain
from leadpipe.sources.osm import build_query, parse_elements
from tests.conftest import FIX


def test_clean_url():
    assert clean_url("riverbend-dental.test") == "https://riverbend-dental.test"
    assert clean_url("https://WWW.Example.test/path/") == "https://www.example.test/path"
    assert clean_url("a.test; b.test") == "https://a.test"
    assert clean_url("not a url") is None
    assert clean_url(None) is None


def test_domain_ignores_social_hosts():
    assert domain("https://www.riverbend-dental.test/x") == "riverbend-dental.test"
    assert domain("https://facebook.com/somedentist") is None


def test_clean_phone():
    assert clean_phone("(512) 555-0101") == "+15125550101"
    assert clean_phone("+1 512 555 0303") == "+15125550303"
    assert clean_phone("1-512-555-0505") == "+15125550505"
    assert clean_phone("+351 21 123 4567; +351 21 765 4321") == "+351211234567"
    assert clean_phone("555") is None


def test_clean_email_rejects_junk():
    assert clean_email("mailto:FrontDesk@Clinic.test?subject=x") == "frontdesk@clinic.test"
    assert clean_email("logo@2x.png") is None
    assert clean_email("user@example.com") is None
    assert clean_email("nope") is None


def test_parse_and_dedupe_osm():
    raw = parse_elements(json.loads((FIX / "overpass.json").read_text()), city="Austin, TX")
    assert len(raw) == 6                      # the unnamed element is dropped
    leads = dedupe(raw)
    names = [l.name for l in leads]
    assert names.count("Riverbend Family Dental") == 1   # node + way merged by domain
    riverbend = next(l for l in leads if l.name == "Riverbend Family Dental")
    assert riverbend.phone == "+15125550101"
    assert riverbend.opening_hours == "Mo-Fr 08:00-17:00"   # filled from the duplicate
    assert riverbend.key == "web:riverbend-dental.test"
    no_site = next(l for l in leads if l.name == "Dr. Lee Dentistry")
    assert no_site.key == "tel:+15125550505"


def test_build_query_has_every_tag(niche):
    q = build_query(3600113314, niche)
    assert 'nwr["amenity"="dentist"](area.a);' in q
    assert 'nwr["healthcare"="dentist"](area.a);' in q
    assert "out center tags;" in q


def test_merge_keeps_first_values():
    from leadpipe.normalize import merge
    a = Lead(name="A", website="https://a.test", phone=None)
    b = Lead(name="A2", website="https://other.test", phone="+1")
    m = merge(a, b)
    assert (m.name, m.website, m.phone) == ("A", "https://a.test", "+1")
