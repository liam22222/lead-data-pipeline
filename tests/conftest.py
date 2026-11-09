import json
from pathlib import Path

import pytest
import requests

from leadpipe.config import Niche, Settings

FIX = Path(__file__).parent / "fixtures"
NICHE = Path(__file__).parent.parent / "leadpipe" / "niches" / "dentists.toml"


class FakeResponse:
    def __init__(self, url, text="", status=200, ctype="text/html", data=None):
        self.url, self.text, self.status_code = url, text, status
        self.headers = {"Content-Type": ctype}
        self._data = data
        self.ok = status < 400

    def json(self):
        return self._data if self._data is not None else json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeHttp:
    """Maps URLs to fixture pages; anything unknown fails like a dead site."""

    def __init__(self, pages: dict[str, str], post_handler=None):
        self.pages, self.post_handler, self.calls = pages, post_handler, []

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        if url not in self.pages:
            raise requests.ConnectionError(f"no route to {url}")
        return FakeResponse(url, self.pages[url])

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        return self.post_handler(url, **kw)


@pytest.fixture
def niche():
    return Niche.load(NICHE)


@pytest.fixture
def settings():
    return Settings(anthropic_api_key=None, anthropic_model="test-model", slack_webhook_url=None,
                    contact_email="test@example.com")


@pytest.fixture
def site_pages():
    read = lambda name: (FIX / name).read_text()
    return {
        "https://riverbend-dental.test": read("riverbend_home.html"),
        "https://riverbend-dental.test/contact-us": read("riverbend_contact.html"),
        "https://hillcountrysmiles.test": read("hillcountry_home.html"),
        "https://aspen-dental-austin.test": read("aspen_home.html"),
    }
