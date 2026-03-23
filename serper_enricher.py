"""
serper_enricher.py — Uses Serper (Google Search API) to fill gaps in
practice data: phone, website, address.

Only called when a practice is missing website OR phone.
Never overwrites fields that Claude already populated.
0.5-second delay on exit (rate limiting).
"""

import logging
import os
import re
import time

import requests
from dotenv import load_dotenv

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

load_dotenv()

log = logging.getLogger(__name__)

SERPER_URL = "https://google.serper.dev/search"


def enrich_practice(practice: dict) -> dict:
    """
    Search for a practice on Google (via Serper) and fill any empty fields.

    Checks, in priority order: knowledgeGraph → places → organic[0].
    Only updates fields that are currently empty/falsy.
    Returns the (possibly enriched) practice dict.
    """
    serper_api_key = os.getenv("SERPER_API_KEY", "")
    if not serper_api_key:
        log.debug("SERPER_API_KEY not set, skipping enrichment")
        return practice

    p = dict(practice)
    name = p.get("name", "")
    city = p.get("city", "")
    specialty = p.get("specialty", "")

    query = f"{name} {city} TX {specialty} phone"

    try:
        resp = requests.post(
            SERPER_URL,
            headers={
                "X-API-KEY": serper_api_key,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": 3},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        log.warning("Serper search failed for %r: %s", name, exc)
        return p
    finally:
        time.sleep(0.5)

    # --- Knowledge Graph (most reliable structured data) ---
    kg = data.get("knowledgeGraph", {})
    if not p.get("phone") and kg.get("phone"):
        p["phone"] = kg["phone"]
    if not p.get("website") and kg.get("website"):
        p["website"] = kg["website"]
    if not p.get("address") and kg.get("address"):
        p["address"] = kg["address"]

    # --- Places results ---
    for place in data.get("places", []):
        if not p.get("phone") and place.get("phone"):
            p["phone"] = place["phone"]
        if not p.get("address") and place.get("address"):
            p["address"] = place["address"]
        if not p.get("website") and place.get("website"):
            p["website"] = place["website"]

    # --- First organic result (website fallback) ---
    organic = data.get("organic", [])
    if organic and not p.get("website"):
        p["website"] = organic[0].get("link", "")

    # --- Extract emails from organic snippets (no extra API call) ---
    serper_emails: list[str] = []
    _JUNK_DOMAINS = {"sentry.io", "example.com", "yourdomain.com", "domain.com"}
    for result in organic:
        for field in (result.get("snippet", ""), result.get("title", "")):
            for m in EMAIL_RE.finditer(field):
                addr = m.group().lower()
                domain = addr.split("@")[-1]
                if domain not in _JUNK_DOMAINS and not addr.endswith((".png", ".jpg")):
                    serper_emails.append(addr)
    if serper_emails:
        p["_serper_emails"] = serper_emails

    return p
