"""
snov_client.py — Snov.io API client for email discovery.

Uses the Domain Search endpoint to find email addresses associated with
a practice's website domain. Targets office managers, billing coordinators,
and practice managers specifically.

API docs: https://snov.io/api
"""

import logging
from urllib.parse import urlparse

import requests

import config

logger = logging.getLogger("snov_client")

# Type alias for an optional contact dict
Optional_dict = dict | None

SNOV_BASE_URL = "https://api.snov.io/v1"
SNOV_AUTH_URL = "https://api.snov.io/v1/oauth/access_token"

# Titles we consider relevant for outreach (checked case-insensitively)
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


class SnovClient:
    """Thin wrapper around the Snov.io REST API."""

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
    ):
        self.client_id = client_id or config.SNOV_CLIENT_ID
        self.client_secret = client_secret or config.SNOV_CLIENT_SECRET
        self._access_token: str = ""

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _get_access_token(self) -> str:
        """Fetch a fresh OAuth2 access token from Snov.io."""
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        try:
            resp = requests.post(SNOV_AUTH_URL, data=payload, timeout=15)
            resp.raise_for_status()
            token = resp.json().get("access_token", "")
            if not token:
                logger.error("Snov.io auth returned no access_token")
            return token
        except requests.RequestException as exc:
            logger.error("Snov.io auth request failed: %s", exc)
            return ""

    def _token(self) -> str:
        """Return cached token, refreshing if empty."""
        if not self._access_token:
            self._access_token = self._get_access_token()
        return self._access_token

    # ------------------------------------------------------------------
    # Domain search
    # ------------------------------------------------------------------

    def find_emails_for_domain(self, domain: str) -> list[dict]:
        """
        Search Snov.io for email addresses associated with `domain`.

        Returns a list of contact dicts:
            {email, first_name, last_name, full_name, title, confidence}

        Returns [] on any error or if no emails found.
        """
        domain = _normalize_domain(domain)
        if not domain:
            return []

        token = self._token()
        if not token:
            return []

        params = {
            "access_token": token,
            "domain": domain,
            "type": "all",
            "limit": 20,
            "lastId": 0,
        }
        try:
            resp = requests.get(f"{SNOV_BASE_URL}/get-domain-emails-with-info", params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning("Snov.io domain search failed for %s: %s", domain, exc)
            return []

        emails = data.get("emails", []) or []
        contacts = []
        for entry in emails:
            email = entry.get("email", "")
            if not email:
                continue
            contacts.append({
                "email": email.lower().strip(),
                "first_name": entry.get("firstName", ""),
                "last_name": entry.get("lastName", ""),
                "full_name": f"{entry.get('firstName', '')} {entry.get('lastName', '')}".strip(),
                "title": entry.get("currentJob", [{}])[0].get("title", "") if entry.get("currentJob") else "",
                "confidence": entry.get("confidence", ""),
            })

        logger.info("Snov.io found %d emails for domain %s", len(contacts), domain)
        return contacts

    # ------------------------------------------------------------------
    # Best contact selection
    # ------------------------------------------------------------------

    def best_contact(self, domain: str) -> Optional_dict:
        """
        Find emails for domain and return the single best contact.

        Priority:
        1. Anyone with a TARGET_TITLE (office manager, billing, etc.)
        2. First email found as fallback
        Returns None if no emails found at all.
        """
        contacts = self.find_emails_for_domain(domain)
        if not contacts:
            return None

        # Try to find a target-title match
        for contact in contacts:
            title_lower = contact.get("title", "").lower()
            if any(t in title_lower for t in TARGET_TITLES):
                logger.debug("Selected contact by title: %s <%s>", contact["full_name"], contact["email"])
                return contact

        # Fallback: first contact
        logger.debug("No target-title contact found; using first result for domain %s", domain)
        return contacts[0]



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_domain(website: str) -> str:
    """
    Extract a bare domain from a URL or domain string.

    Examples:
        'https://www.dallasortho.com/about' → 'dallasortho.com'
        'www.dallasortho.com'               → 'dallasortho.com'
        'dallasortho.com'                   → 'dallasortho.com'
    """
    if not website:
        return ""
    if not website.startswith("http"):
        website = f"https://{website}"
    try:
        netloc = urlparse(website).netloc.lower()
        # Strip www. prefix
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:  # noqa: BLE001
        return ""
