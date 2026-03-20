"""
maps_client.py — Google Maps Places API client.

Uses the Places Text Search endpoint to find specialty medical practices
across DFW cities. Returns raw practice dicts with name, address, website,
phone, and place_id.
"""

import logging
import time
from typing import Optional

import requests

import config

logger = logging.getLogger("maps_client")

PLACES_TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"


def search_practices(query: str, api_key: str = "") -> list[dict]:
    """
    Run a single Places Text Search query and return a list of practice dicts.

    Each dict contains:
        place_id, name, address, phone, website, rating, types

    Handles pagination via next_page_token (up to 3 pages = 60 results max).
    On API error, logs and returns whatever was collected so far.
    """
    key = api_key or config.GOOGLE_MAPS_API_KEY
    results: list[dict] = []
    params: dict = {
        "query": query,
        "type": "doctor",
        "key": key,
    }

    page = 0
    while True:
        page += 1
        try:
            resp = requests.get(PLACES_TEXT_SEARCH_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.error("Maps text search request failed (query=%r page=%d): %s", query, page, exc)
            break

        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            logger.warning("Maps API returned status=%s for query=%r", status, query)
            break

        for place in data.get("results", []):
            practice = _parse_place(place)
            results.append(practice)

        next_token = data.get("next_page_token")
        if not next_token or len(results) >= config.MAPS_MAX_RESULTS_PER_QUERY:
            break

        # Google requires a short delay before next_page_token is usable
        time.sleep(2)
        params = {"pagetoken": next_token, "key": key}

    logger.info("Maps search '%s' → %d results", query, len(results))
    return results


def enrich_with_details(practice: dict, api_key: str = "") -> dict:
    """
    Fetch the Places Details for a single place_id to get phone + website.

    Mutates and returns the practice dict. On error, returns dict unchanged.
    """
    key = api_key or config.GOOGLE_MAPS_API_KEY
    place_id = practice.get("place_id")
    if not place_id:
        return practice

    params = {
        "place_id": place_id,
        "fields": "name,formatted_phone_number,website,formatted_address",
        "key": key,
    }
    try:
        resp = requests.get(PLACES_DETAILS_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("Places Details failed for place_id=%s: %s", place_id, exc)
        return practice

    result = data.get("result", {})
    practice["phone"] = result.get("formatted_phone_number", practice.get("phone", ""))
    practice["website"] = result.get("website", practice.get("website", ""))
    practice["address"] = result.get("formatted_address", practice.get("address", ""))
    return practice


def search_all_queries(api_key: str = "") -> list[dict]:
    """
    Run all configured search queries (SPECIALTIES × DFW_CITIES) and return
    a deduplicated list of practices (deduped by place_id).
    """
    seen_place_ids: set[str] = set()
    all_practices: list[dict] = []

    for query in config.SEARCH_QUERIES:
        try:
            practices = search_practices(query, api_key=api_key)
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error searching '%s': %s", query, exc)
            continue

        for practice in practices:
            pid = practice.get("place_id", "")
            if pid and pid not in seen_place_ids:
                seen_place_ids.add(pid)
                all_practices.append(practice)

    logger.info("Total unique practices found across all queries: %d", len(all_practices))
    return all_practices


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_place(place: dict) -> dict:
    """Extract the fields we care about from a Places API result entry."""
    # Derive specialty from types list
    types = place.get("types", [])
    specialty = _infer_specialty(place.get("name", ""), types)

    return {
        "place_id": place.get("place_id", ""),
        "name": place.get("name", ""),
        "address": place.get("formatted_address", ""),
        "phone": "",       # populated by enrich_with_details
        "website": "",     # populated by enrich_with_details
        "types": types,
        "specialty": specialty,
        "rating": place.get("rating"),
    }


def _infer_specialty(name: str, types: list[str]) -> str:
    """
    Best-effort specialty label based on practice name and Places types.
    Returns 'Endocrinology', 'Orthopedics', or 'General'.
    """
    name_lower = name.lower()
    if any(k in name_lower for k in ("endocrin", "diabetes", "thyroid", "hormone")):
        return "Endocrinology"
    if any(k in name_lower for k in ("ortho", "bone", "spine", "joint", "sports med")):
        return "Orthopedics"
    return "General"
