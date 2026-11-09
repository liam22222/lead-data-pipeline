import pytest
import requests

from leadpipe.config import Settings
from leadpipe.sources.osm import NOMINATIM_URL, PHOTON_URL, find_area_id
from tests.conftest import FakeResponse


class StubHttp:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, **kw):
        return self.routes[url](url)


def test_nominatim_first():
    http = StubHttp({NOMINATIM_URL: lambda u: FakeResponse(u, data=[
        {"osm_type": "relation", "osm_id": 113314, "class": "boundary"}])})
    assert find_area_id("Austin, Texas", http) == 3_600_113_314


def test_falls_back_to_photon_when_nominatim_blocks():
    http = StubHttp({
        NOMINATIM_URL: lambda u: FakeResponse(u, status=403, data=[]),
        PHOTON_URL: lambda u: FakeResponse(u, data={"features": [
            {"properties": {"osm_type": "N", "osm_id": 1, "osm_key": "amenity"}},
            {"properties": {"osm_type": "R", "osm_id": 113314, "osm_key": "place"}}]}),
    })
    assert find_area_id("Austin, Texas", http) == 3_600_113_314


def test_clear_error_when_nothing_found():
    def boom(u):
        raise requests.ConnectionError("down")
    with pytest.raises(ValueError, match="Could not find a city boundary"):
        find_area_id("Nowhere", StubHttp({NOMINATIM_URL: boom, PHOTON_URL: boom}))


def test_user_agent_never_uses_placeholder_email():
    s = Settings(None, "m", None, "you@example.com")
    assert "example.com" not in s.user_agent and "github.com/liam22222" in s.user_agent
    s.contact_email = "liam@real.dev"
    assert "liam@real.dev" in s.user_agent
