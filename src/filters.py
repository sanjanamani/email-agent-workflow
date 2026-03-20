"""
filters.py — Filter practices to independent/small practices only.

We want solo or small-group practices that handle their own admin, NOT
large hospital systems, academic medical centers, or national chains.
"""

import logging
import re
from urllib.parse import urlparse

import config

logger = logging.getLogger("filters")


def is_independent_practice(practice: dict) -> bool:
    """
    Return True if the practice looks like an independent/small-group practice.

    Checks:
    1. Practice name doesn't contain hospital-system keywords.
    2. Website domain (if present) doesn't belong to a known health system.
    3. Address doesn't reference a known hospital campus keyword.
    """
    name = practice.get("name", "").lower()
    address = practice.get("address", "").lower()
    website = practice.get("website", "").lower()

    # Check name and address against hospital keyword list
    for keyword in config.HOSPITAL_SYSTEM_KEYWORDS:
        if keyword in name:
            logger.debug("Filtered out '%s' — name matches keyword '%s'", practice.get("name"), keyword)
            return False
        if keyword in address:
            logger.debug("Filtered out '%s' — address matches keyword '%s'", practice.get("name"), keyword)
            return False

    # Check website domain against known hospital domains
    if website:
        domain = _extract_domain(website)
        for keyword in config.HOSPITAL_SYSTEM_KEYWORDS:
            if keyword.replace(" ", "") in domain.replace("-", "").replace(".", ""):
                logger.debug(
                    "Filtered out '%s' — website domain '%s' matches keyword '%s'",
                    practice.get("name"), domain, keyword,
                )
                return False

    return True


def filter_practices(practices: list[dict]) -> list[dict]:
    """
    Apply is_independent_practice to a list of practices.

    Returns only the ones that pass, with logging for totals.
    """
    before = len(practices)
    kept = [p for p in practices if is_independent_practice(p)]
    logger.info(
        "Practice filter: %d total → %d independent (removed %d hospital/system practices)",
        before, len(kept), before - len(kept),
    )
    return kept


def extract_city(address: str) -> str:
    """
    Best-effort city extraction from a formatted address string like:
    '123 Main St, Plano, TX 75024, USA'
    Returns the city portion, or empty string if not found.
    """
    # Standard US address: ..., City, STATE ZIP, USA
    match = re.search(r",\s*([^,]+),\s*TX\b", address)
    if match:
        return match.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_domain(url: str) -> str:
    """Return the netloc (domain) portion of a URL, lowercased."""
    try:
        parsed = urlparse(url if url.startswith("http") else f"https://{url}")
        return parsed.netloc.lower()
    except Exception:  # noqa: BLE001
        return url.lower()
