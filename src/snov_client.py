"""
snov_client.py — Website email scraper using BeautifulSoup and requests.

Scrapes each practice's website URL — checks the homepage, /contact, and /about
pages for email addresses using regex on mailto: links and plain email patterns.

Class and function signatures are preserved from the original Snov.io client
for drop-in compatibility with the rest of the codebase.
"""

import logging
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import config

logger = logging.getLogger("snov_client")

Optional_dict = dict | None

TARGET_TITLES: list[str] = [
    "office manager",
    "practice manager",
    "practice administrator",
    "billing coordinator",
    "billing manager",
    "medical office",
    "admin",
    "administrator",
    "operations",
    "front office",
    "clinic manager",
    "clinic administrator",
]

# Generic inbox prefixes — these reach no specific person and should be
# used only as a last resort.
GENERIC_PREFIXES: tuple[str, ...] = (
    "info", "contact", "hello", "support", "help", "admin",
    "office", "scheduling", "appointments", "billing",
    "reception", "general", "noreply", "no-reply",
)

CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Pages to check on each domain
SCRAPE_PATHS = ["", "/contact", "/about"]
SCRAPE_TIMEOUT = 10  # seconds per request


class SnovClient:
    """Scrapes practice websites to find contact email addresses."""

    def __init__(self, client_id: str = "", client_secret: str = ""):
        # Signature preserved for drop-in compatibility; credentials unused.
        self.client_id = client_id or config.SNOV_CLIENT_ID
        self.client_secret = client_secret or config.SNOV_CLIENT_SECRET

    # ------------------------------------------------------------------
    # Domain search (web scraping)
    # ------------------------------------------------------------------

    def find_emails_for_domain(self, domain: str) -> list[dict]:
        """
        Scrape the practice website for email addresses.

        Checks the homepage, /contact, and /about pages.
        Returns list of {email, first_name, last_name, full_name, title, confidence}.
        Returns [] on any error or no results.
        """
        domain = _normalize_domain(domain)
        if not domain:
            return []

        found_emails: set[str] = set()

        for path in SCRAPE_PATHS:
            url = f"https://{domain}{path}"
            emails = _scrape_emails_from_url(url)
            found_emails.update(emails)
            # Fall back to http on the homepage if https returned nothing
            if not emails and path == "":
                found_emails.update(_scrape_emails_from_url(f"http://{domain}"))

        contacts = [
            {
                "email": email,
                "first_name": "",
                "last_name": "",
                "full_name": "",
                "title": "",
                "confidence": "medium",
            }
            for email in sorted(found_emails)
        ]

        logger.info("Scraped %d emails from %s", len(contacts), domain)
        return contacts

    # ------------------------------------------------------------------
    # Best contact selection
    # ------------------------------------------------------------------

    def best_contact(self, domain: str) -> Optional_dict:
        """
        Return the single best contact for a domain.

        Priority:
        1. Target-title match (office manager, billing, etc.) — highest confidence first
        2. Personal email (non-generic prefix) — highest confidence first
        3. Generic inbox (info@, scheduling@, etc.) — highest confidence first
        4. None if no emails found
        """
        contacts = self.find_emails_for_domain(domain)
        if not contacts:
            return None

        def _is_generic(contact: dict) -> bool:
            local = contact["email"].split("@")[0].lower()
            return local in GENERIC_PREFIXES

        def _conf_rank(contact: dict) -> int:
            return CONFIDENCE_RANK.get(contact.get("confidence", "low"), 2)

        def _has_target_title(contact: dict) -> bool:
            return any(t in contact.get("title", "").lower() for t in TARGET_TITLES)

        # Sort: (no target title, is generic, confidence rank) — lower is better
        ranked = sorted(
            contacts,
            key=lambda c: (not _has_target_title(c), _is_generic(c), _conf_rank(c)),
        )

        best = ranked[0]
        logger.debug(
            "Selected contact %s <%s> title=%r confidence=%s generic=%s",
            best["full_name"], best["email"], best["title"],
            best.get("confidence"), _is_generic(best),
        )
        return best


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scrape_emails_from_url(url: str) -> set[str]:
    """Fetch a URL and extract all email addresses from it."""
    try:
        resp = requests.get(
            url,
            timeout=SCRAPE_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.debug("Could not fetch %s: %s", url, exc)
        return set()

    soup = BeautifulSoup(resp.text, "html.parser")
    emails: set[str] = set()

    # mailto: links
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip().lower()
            if EMAIL_REGEX.fullmatch(addr):
                emails.add(addr)

    # Plain-text regex scan
    for match in EMAIL_REGEX.finditer(soup.get_text(" ")):
        emails.add(match.group().lower())

    # Strip obvious false positives (file-extension lookalikes)
    emails = {
        e for e in emails
        if not any(e.endswith(ext) for ext in (".png", ".jpg", ".gif", ".svg"))
    }

    return emails


def _normalize_domain(website: str) -> str:
    if not website:
        return ""
    if not website.startswith("http"):
        website = f"https://{website}"
    try:
        netloc = urlparse(website).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:  # noqa: BLE001
        return ""
