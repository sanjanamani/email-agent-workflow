"""
maps_client.py — SerpAPI Google Maps search client.

Uses the SerpAPI Local Results (Google Maps) endpoint to find specialty
medical practices across DFW cities. Returns practice dicts with name,
address, website, phone, and place_id.

SerpAPI free plan: 100 searches/month — plenty for this workflow.
"""

import logging
import time
from typing import Optional

import requests

import config

logger = logging.getLogger("maps_client")

SERPAPI_URL = "https://serpapi.com/search"


def search_practices(query: str, api_key: str = "") -> list[dict]:
    """
    Run a single Google Maps search via SerpAPI and return a list of practice dicts.

    Each dict contains:
        place_id, name, address, phone, website, rating, types, specialty
    """
    key = api_key or config.SERPAPI_KEY
    results: list[dict] = []

    params = {
        "engine": "google_maps",
        "q": query,
        "type": "search",
        "api_key": key,
    }

    try:
        resp = requests.get(SERPAPI_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("SerpAPI request failed (query=%r): %s", query, exc)
        return results

    if "error" in data:
        logger.warning("SerpAPI returned error for query=%r: %s", query, data["error"])
        return results

    for place in data.get("local_results", []):
        practice = _parse_place(place)
        results.append(practice)
        if len(results) >= config.MAPS_MAX_RESULTS_PER_QUERY:
            break

    logger.info("SerpAPI search '%s' → %d results", query, len(results))
    return results


def enrich_with_details(practice: dict, api_key: str = "") -> dict:
    """
    SerpAPI already returns phone and website in the local_results,
    so this is a no-op kept for compatibility with main.py.
    """
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

        # Be polite to the API — small delay between queries
        time.sleep(1)

    logger.info("Total unique practices found across all queries: %d", len(all_practices))
    return all_practices


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_place(place: dict) -> dict:
    """Extract the fields we care about from a SerpAPI local_results entry."""
    name = place.get("title", "")
    specialty = _infer_specialty(name)

    return {
        "place_id": place.get("place_id", ""),
        "name": name,
        "address": place.get("address", ""),
        "phone": place.get("phone", ""),
        "website": place.get("website", ""),
        "rating": place.get("rating"),
        "types": [place.get("type", "")],
        "specialty": specialty,
    }


def _infer_specialty(name: str) -> str:
    """
    Best-effort specialty label based on practice name.
    Returns 'Endocrinology', 'Orthopedics', or 'General'.
    """
    name_lower = name.lower()
    if any(k in name_lower for k in ("endocrin", "diabetes", "thyroid", "hormone")):
        return "Endocrinology"
    if any(k in name_lower for k in ("ortho", "bone", "spine", "joint", "sports med")):
        return "Orthopedics"
    return "General"
