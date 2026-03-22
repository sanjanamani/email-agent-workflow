"""
npi_client.py — Fetches specialty practices from the CMS NPI Registry.

No API key required. Returns dicts in the same format as claude_finder.py:
  {"name": str, "phone": str, "website": str, "address": str,
   "city": str, "specialty": str}
"""

import logging
import time

import requests

log = logging.getLogger(__name__)

NPI_API = "https://npiregistry.cms.hhs.gov/api/"
NPI_VERSION = "2.1"
NPI_STATE = "TX"
NPI_LIMIT = 200

# Maps the specialty label used throughout the pipeline to an NPI taxonomy code
TAXONOMY_CODES: dict[str, str] = {
    "endocrinologist": "207RE0101X",
    "orthopedic surgeon": "207X00000X",
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
    """
    addresses: list[dict] = result.get("addresses", [])
    loc = _practice_location(addresses)
    if loc is None:
        return None  # mailing-only — skip

    basic: dict = result.get("basic", {})

    # Prefer organisation name; fall back to provider first+last
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
        "website": "",          # NPI registry doesn't carry website URLs
        "address": address,
        "city": city,
        "specialty": specialty,
    }


def fetch_npi_practices(specialty: str, cities: list[str]) -> list[dict]:
    """
    Query the NPI registry for *specialty* in each of *cities* (state=TX).

    Parameters
    ----------
    specialty : str
        One of the keys in TAXONOMY_CODES, e.g. "endocrinologist".
    cities : list[str]
        City names to iterate over.

    Returns
    -------
    list[dict]
        Practice dicts (same schema as claude_finder.py output).
    """
    taxonomy_code = TAXONOMY_CODES.get(specialty)
    if not taxonomy_code:
        log.warning("npi_client: no taxonomy code for specialty %r — skipping", specialty)
        return []

    all_practices: list[dict] = []

    for city in cities:
        params = {
            "version": NPI_VERSION,
            "taxonomy_description": taxonomy_code,
            "city": city,
            "state": NPI_STATE,
            "limit": NPI_LIMIT,
        }
        try:
            resp = requests.get(NPI_API, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("npi_client: request failed for %s/%s: %s", specialty, city, exc)
            time.sleep(2)
            continue

        results = data.get("results") or []
        log.info(
            "  NPI %s / %s → %d results (result_count=%s)",
            specialty, city, len(results), data.get("result_count", "?"),
        )

        for result in results:
            practice = _parse_result(result, specialty)
            if practice:
                all_practices.append(practice)

        # Be polite — a short pause between cities is plenty (no rate-limit docs,
        # but the registry asks for reasonable use)
        time.sleep(0.25)

    return all_practices
