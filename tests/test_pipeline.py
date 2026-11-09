import csv

from leadpipe.outputs import slack_message
from leadpipe.pipeline import RunOptions, run
from leadpipe.store import Store
from tests.conftest import FIX, NICHE, FakeHttp


def opts(tmp_path, **kw):
    return RunOptions(city="Austin, TX", niche_path=NICHE, out_dir=tmp_path / "out",
                      db_path=tmp_path / "leads.db", osm_file=FIX / "overpass.json", **kw)


def test_full_run_offline(tmp_path, settings, site_pages):
    http = FakeHttp(site_pages)
    result = run(opts(tmp_path), settings, http)

    assert result.stats["found"] == 5
    top = result.leads and sorted(result.leads, key=lambda l: -l.score)[0]
    assert top.name == "Riverbend Family Dental" and top.hot
    rows = list(csv.DictReader(result.csv.open()))
    assert rows[0]["name"] == "Riverbend Family Dental"
    assert rows[0]["email"] == "frontdesk@riverbend-dental.test"
    html = result.report.read_text()
    assert "Riverbend Family Dental" in html and "Score distribution" in html
    assert all(l.new_this_run for l in result.leads)


def test_second_run_is_incremental(tmp_path, settings, site_pages):
    run(opts(tmp_path), settings, FakeHttp(site_pages))
    http = FakeHttp(site_pages)
    result = run(opts(tmp_path), settings, http)
    assert http.calls == []                       # every site still fresh: nothing re-crawled
    assert not any(l.new_this_run for l in result.leads)
    runs = Store(tmp_path / "leads.db").db.execute("SELECT count(*), sum(new) FROM runs").fetchone()
    assert runs == (2, 5)


def test_slack_only_new_hot_leads(tmp_path, settings, site_pages):
    result = run(opts(tmp_path), settings, FakeHttp(site_pages))
    msg = slack_message(result.leads, "Dental clinics", "Austin, TX")
    assert msg and "Riverbend Family Dental" in msg["text"] and "Aspen" not in msg["text"]
    for l in result.leads:
        l.new_this_run = False
    assert slack_message(result.leads, "Dental clinics", "Austin, TX") is None
