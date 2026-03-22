"""
npi_client.py — Fetches specialty practices from the CMS NPI Registry.

No API key required. Returns dicts in the same format as claude_finder.py:
  {"name": str, "phone": str, "website": str, "address": str,
   "city": str, "specialty": str}

Makes two calls per city per specialty:
  enumeration_type=NPI-1  individual providers (first+last name)
  enumeration_type=NPI-2  organisations (organization_name)
"""

import logging
import time

import requests

log = logging.getLogger(__name__)

NPI_API = "https://npiregistry.cms.hhs.gov/api/"
NPI_VERSION = "2.1"
NPI_STATE = "TX"
NPI_LIMIT = 200

# Maps the specialty label used throughout the pipeline to the exact
# taxonomy_description string accepted by the NPI registry search API.
TAXONOMY_DESCRIPTIONS: dict[str, str] = {
    "endocrinologist": "Endocrinology, Diabetes & Metabolism",
    "orthopedic surgeon": "Orthopaedic Surgery",
}


def _build_address(addr: dict) -> str:
    """Format an NPI address dict into a single readable string."""
    parts = [
        addr.get("address_1", ""),
        addr.get("address_2", ""),
        addr.get("city", ""),
        addr.get("state", ""),
        addr.get("postal_code", ""),
    ]
    return ", ".join(p.strip() for p in parts if p.strip())


def _practice_location(addresses: list[dict]) -> dict | None:
    """
    Return the first practice-location address from the addresses array,
    or None if only a mailing address is present.
    """
    for addr in (addresses or []):
        if addr.get("address_purpose", "").upper() == "LOCATION":
            return addr
    return None


def _parse_result(result: dict, specialty: str) -> dict | None:
    """
    Convert a single NPI registry result to the pipeline practice dict.
    Returns None if the entry has no practice-location address.
    Phone comes from the location address; organization name from basic
    for NPI-2, first+last name for NPI-1.
    """
    addresses: list[dict] = result.get("addresses", [])
    loc = _practice_location(addresses)
    if loc is None:
        return None  # mailing-only — skip

    basic: dict = result.get("basic", {})

    # NPI-2: organization_name present; NPI-1: first_name + last_name
    org_name: str = basic.get("organization_name", "").strip()
    if not org_name:
        first = basic.get("first_name", "").strip()
        last = basic.get("last_name", "").strip()
        credential = basic.get("credential", "").strip()
        org_name = " ".join(filter(None, [first, last, credential]))

    phone: str = loc.get("telephone_number", "").strip()
    city: str = loc.get("city", "").strip().title()
    address: str = _build_address(loc)

    return {
        "name": org_name,
        "phone": phone,
        "website": "",  # NPI registry doesn't carry website URLs
        "address": address,
        "city": city,
        "specialty": specialty,
    }


def _fetch_one(taxonomy_desc: str, enumeration_type: str, city: str, specialty: str) -> list[dict]:
    """
    Single NPI API call for one taxonomy/enumeration_type/city combination.
    Returns a list of parsed practice dicts (empty on error or no results).
    """
    params = {
        "version": NPI_VERSION,
        "taxonomy_description": taxonomy_desc,
        "enumeration_type": enumeration_type,
        "city": city,
        "state": NPI_STATE,
        "limit": NPI_LIMIT,
    }
    try:
        resp = requests.get(NPI_API, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning(
            "npi_client: request failed [%s] %s/%s: %s",
            enumeration_type, specialty, city, exc,
        )
        time.sleep(2)
        return []

    results = data.get("results") or []
    log.info(
        "  NPI [%s] %s / %s → %d results (result_count=%s)",
        enumeration_type, specialty, city, len(results), data.get("result_count", "?"),
    )

    practices = []
    for result in results:
        practice = _parse_result(result, specialty)
        if practice:
            practices.append(practice)
    return practices


def fetch_npi_practices(specialty: str, cities: list[str]) -> list[dict]:
    """
    Query the NPI registry for *specialty* in each of *cities* (state=TX).
    Makes two calls per city: NPI-1 (individuals) and NPI-2 (organisations).

    Parameters
    ----------
    specialty : str
        One of the keys in TAXONOMY_DESCRIPTIONS, e.g. "endocrinologist".
    cities : list[str]
        City names to iterate over.

    Returns
    -------
    list[dict]
        Practice dicts (same schema as claude_finder.py output).
    """
    taxonomy_desc = TAXONOMY_DESCRIPTIONS.get(specialty)
    if not taxonomy_desc:
        log.warning("npi_client: no taxonomy description for specialty %r — skipping", specialty)
        return []

    all_practices: list[dict] = []

    for city in cities:
        for enum_type in ("NPI-1", "NPI-2"):
            all_practices.extend(_fetch_one(taxonomy_desc, enum_type, city, specialty))
            # Polite pause between calls
            time.sleep(0.25)

    return all_practices
