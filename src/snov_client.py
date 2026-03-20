"""
snov_client.py — Snov.io API client for email discovery.

Uses the v2 Domain Search endpoint (two-step async) to find email
addresses associated with a practice's website domain.

API docs: https://snov.io/api
"""

import logging
import time
from urllib.parse import urlparse

import requests

import config

logger = logging.getLogger("snov_client")

Optional_dict = dict | None

SNOV_AUTH_URL = "https://api.snov.io/v1/oauth/access_token"
SNOV_V2_BASE = "https://api.snov.io/v2"

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


class SnovClient:
    """Thin wrapper around the Snov.io REST API (v2)."""

    def __init__(self, client_id: str = "", client_secret: str = ""):
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
        if not self._access_token:
            self._access_token = self._get_access_token()
        return self._access_token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}"}

    # ------------------------------------------------------------------
    # Domain search (v2 two-step async)
    # ------------------------------------------------------------------

    def find_emails_for_domain(self, domain: str) -> list[dict]:
        """
        Search Snov.io for emails associated with `domain`.

        Step 1 — POST to start the search → get task_hash
        Step 2 — GET result by task_hash (polls up to 3×)

        Returns list of {email, first_name, last_name, full_name, title, confidence}.
        Returns [] on any error or no results.
        """
        domain = _normalize_domain(domain)
        if not domain:
            return []

        if not self._token():
            return []

        # Step 1: start search
        try:
            resp = requests.post(
                f"{SNOV_V2_BASE}/domain-search/domain-emails/start",
                json={"domain": domain},
                headers=self._headers(),
                timeout=20,
            )
            resp.raise_for_status()
            start_data = resp.json()
        except requests.RequestException as exc:
            logger.warning("Snov.io domain search failed for %s: %s", domain, exc)
            return []

        task_hash = start_data.get("meta", {}).get("task_hash", "")
        if not task_hash:
            logger.warning("Snov.io returned no task_hash for %s", domain)
            return []

        # Step 2: fetch result (poll up to 3 times with backoff)
        result_url = f"{SNOV_V2_BASE}/domain-search/domain-emails/result/{task_hash}"
        emails_raw: list[dict] = []
        for attempt in range(3):
            try:
                resp = requests.get(result_url, headers=self._headers(), timeout=20)
                resp.raise_for_status()
                result = resp.json()
            except requests.RequestException as exc:
                logger.warning("Snov.io result fetch failed for %s: %s", domain, exc)
                return []

            emails_raw = result.get("data", [])
            if emails_raw:
                break
            if attempt < 2:
                time.sleep(2)

        contacts = []
        for entry in emails_raw:
            email = entry.get("email", "")
            if not email:
                continue
            first = entry.get("firstName", entry.get("first_name", ""))
            last = entry.get("lastName", entry.get("last_name", ""))
            job = entry.get("currentJob") or []
            title = job[0].get("title", "") if job else ""
            contacts.append({
                "email": email.lower().strip(),
                "first_name": first,
                "last_name": last,
                "full_name": f"{first} {last}".strip(),
                "title": title,
                "confidence": entry.get("confidence", ""),
            })

        logger.info("Snov.io found %d emails for domain %s", len(contacts), domain)
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
        4. None if everything is low-confidence and generic

        Low-confidence emails are only used when nothing better exists.
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
