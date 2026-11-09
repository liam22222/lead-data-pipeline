"""Visit each lead's website and pull out what a salesperson would look for."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from leadpipe.config import Niche
from leadpipe.http import HttpClient
from leadpipe.models import Lead, now
from leadpipe.normalize import EMAIL_RE, clean_email, clean_phone, domain

log = logging.getLogger(__name__)

SOCIAL_HOSTS = {"facebook.com": "facebook", "instagram.com": "instagram", "linkedin.com": "linkedin",
                "twitter.com": "x", "x.com": "x", "youtube.com": "youtube", "tiktok.com": "tiktok"}
PLATFORMS = {"wp-content": "WordPress", "wix.com": "Wix", "wixstatic": "Wix", "squarespace": "Squarespace",
             "shopify": "Shopify", "webflow": "Webflow", "godaddy": "GoDaddy", "weebly": "Weebly",
             "duda": "Duda", "prosites": "ProSites"}
CONTACT_WORDS = ("contact", "appointment", "request", "schedule", "book")
MAX_HTML = 2_000_000


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html[:MAX_HTML], "lxml")


def analyze_page(html: str, base_url: str, niche: Niche) -> dict:
    """Extract signals from one HTML page. Pure function, easy to test."""
    soup = _soup(html)
    for tag in soup(["script", "style", "noscript"]):
        # keep script src (booking widgets) before dropping the body
        tag.string = ""
    text = soup.get_text(" ", strip=True).lower()
    raw = html.lower()
    links = [(a.get("href") or "").strip() for a in soup.find_all("a")]
    srcs = [s.get("src") or "" for s in soup.find_all(["script", "iframe"])]
    link_blob = " ".join(links + srcs).lower()

    emails = {clean_email(h[7:]) for h in links if h.lower().startswith("mailto:")}
    emails |= {clean_email(m) for m in EMAIL_RE.findall(soup.get_text(" "))}
    phones = {clean_phone(h[4:]) for h in links if h.lower().startswith("tel:")}

    socials: dict[str, str] = {}
    for h in links:
        host = urlparse(h).netloc.lower().removeprefix("www.")
        for social_host, label in SOCIAL_HOSTS.items():
            if host == social_host or host.endswith("." + social_host):
                socials.setdefault(label, h)

    has_form = any(
        f.find("textarea") or f.find("input", attrs={"type": "email"})
        for f in soup.find_all("form")
    )
    # raw html too: booking widgets are often injected by an inline script
    booking = sorted({s for s in niche.online_booking if s in text or s in link_blob or s in raw})
    services = sorted({s for s in niche.premium_services if s in text})
    platform = next((name for marker, name in PLATFORMS.items() if marker in raw), None)
    generator = soup.find("meta", attrs={"name": "generator"})
    if not platform and generator and generator.get("content"):
        platform = generator["content"].split()[0]

    contact_links = []
    own = domain(base_url)
    for h in links:
        full = urljoin(base_url, h)
        if domain(full) == own and any(w in full.lower() for w in CONTACT_WORDS):
            contact_links.append(full.split("#")[0])

    return {
        "title": soup.title.get_text(strip=True)[:200] if soup.title else None,
        "emails": sorted(e for e in emails if e),
        "phones": sorted(p for p in phones if p),
        "socials": socials,
        "has_form": bool(has_form),
        "booking": booking,
        "services": services,
        "platform": platform,
        "contact_links": list(dict.fromkeys(contact_links))[:3],
    }


def is_chain(name: str, niche: Niche) -> bool:
    lower = name.lower()
    return any(k in lower for k in niche.chain_keywords)


def enrich_lead(lead: Lead, niche: Niche, http: HttpClient) -> Lead:
    lead.is_chain = is_chain(lead.name, niche)
    lead.last_updated = now()
    if not lead.website:
        lead.site_status = "no_website"
        return lead
    try:
        resp = http.get(lead.website)
        if resp.status_code >= 400 or "html" not in resp.headers.get("Content-Type", "html"):
            lead.site_status = "unreachable"
            return lead
        pages = [analyze_page(resp.text, resp.url, niche)]
        # One extra hop: the contact/booking page is where emails and booking widgets usually live.
        for link in pages[0]["contact_links"][:1]:
            try:
                sub = http.get(link)
                if sub.ok:
                    pages.append(analyze_page(sub.text, sub.url, niche))
            except Exception as e:  # a broken contact page shouldn't lose the homepage data
                log.debug("contact page %s failed: %s", link, e)
    except Exception as e:
        log.info("%s unreachable: %s", lead.website, e)
        lead.site_status = "unreachable"
        return lead

    lead.site_status = "ok"
    lead.site_title = pages[0]["title"]
    lead.emails_found = sorted({e for p in pages for e in p["emails"]})
    lead.phones_found = sorted({x for p in pages for x in p["phones"]})
    lead.socials = {k: v for p in reversed(pages) for k, v in p["socials"].items()}
    lead.has_contact_form = any(p["has_form"] for p in pages)
    lead.booking_signals = sorted({b for p in pages for b in p["booking"]})
    lead.online_booking = bool(lead.booking_signals)
    lead.premium_services = sorted({s for p in pages for s in p["services"]})
    lead.site_platform = pages[0]["platform"]
    if not lead.email and lead.emails_found:
        # prefer an address on the clinic's own domain
        own = domain(lead.website)
        lead.email = next((e for e in lead.emails_found if own and e.endswith("@" + own)), lead.emails_found[0])
    if not lead.phone and lead.phones_found:
        lead.phone = lead.phones_found[0]
    return lead


def enrich_all(leads: list[Lead], niche: Niche, http: HttpClient, workers: int = 8) -> list[Lead]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda lead: enrich_lead(lead, niche, http), leads))
