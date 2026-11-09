"""A polite HTTP client: identifies itself, retries with backoff, and rate-limits per host."""
from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class HttpClient:
    def __init__(self, user_agent: str, timeout: float = 15, retries: int = 3,
                 backoff: float = 1.5, min_interval: float = 1.0):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.min_interval = min_interval     # seconds between requests to the same host
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()

    def _wait_for_host(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            last = self._last_hit.get(host, 0.0)
            start = max(time.monotonic(), last + self.min_interval)
            self._last_hit[host] = start   # reserve the slot before sleeping
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._wait_for_host(url)
            try:
                resp = self.session.request(method, url, **kwargs)
                if resp.status_code in RETRY_STATUSES and attempt < self.retries:
                    delay = float(resp.headers.get("Retry-After", self.backoff ** attempt))
                    log.warning("%s %s -> %s, retrying in %.1fs", method, url, resp.status_code, delay)
                    time.sleep(min(delay, 60))
                    continue
                return resp
            except requests.RequestException as e:
                last_error = e
                if attempt < self.retries:
                    delay = self.backoff ** attempt
                    log.warning("%s %s failed (%s), retrying in %.1fs", method, url, e, delay)
                    time.sleep(delay)
        raise last_error or RuntimeError(f"{method} {url} failed")

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)
