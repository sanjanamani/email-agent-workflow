"""
npi_client.py — Fetches specialty practices from the CMS NPI Registry.

No API key required. Returns dicts in the same format as claude_finder.py:
  {"name": str, "phone": str, "website": str, "address": str,
   "city": str, "specialty": str}

Makes two calls per city per specialty:
  enumeration_type=NPI-1  individual providers
  enumeration_type=NPI-2  organisations

After parsing, results are filtered through is_independent_practice()
which checks both the practice/org name AND the registered address to
drop hospital systems, large chains, and affiliated groups.
"""

import logging
import time
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

NPI_API = "https://npiregistry.cms.hhs.gov/api/"
NPI_VERSION = "2.1"
NPI_STATE = "TX"
NPI_LIMIT = 200

TAXONOMY_DESCRIPTIONS: dict[str, str] = {
    "endocrinologist": "Endocrinology, Diabetes & Metabolism",
    "orthopedic surgeon": "Orthopaedic Surgery",
}

# ---------------------------------------------------------------------------
# Affiliation filter
# ---------------------------------------------------------------------------

# Names / address fragments that indicate hospital or chain affiliation.
# Checked against: NPI-2 org_name, NPI-1 organization_name, and address lines.
_AFFILIATED_NAME_KEYWORDS: list[str] = [
    "hospital",
    "health system",
    "health network",
    "medical center",
    "medical group",
    "regional medical",
    "university",
    "college",
    "academic",
    "ut southwestern",
    "utsw",
    "baylor",
    "methodist",
    "parkland",
    "children's",
    "childrens",
    "pediatric",
    "hca",
    "tenet",
    "ascension",
    "commonspirit",
    "sutter",
    "providence",
    "kaiser",
    " va ",
    "veterans",
    "memorial",
    "presbyterian",
    "trinity",
    "christus",
    "cook children",
    "texas health resources",
    "texas health physicians",
    "thpg",
    "community health",
    "privia",
    "bsw health",       # Baylor Scott & White (without "baylor" in name)
    "bswhealth",
    "scott & white",
    "scott and white",
    "optum",
    "unitedhealth",
    "humana medical",
    "cigna medical",
    "aetna medical",
    "chenmed",
    "oak street",
    "one medical",
    "carbon health",
    "concentra",
    "minuteclinic",
    "cvs health",
    "walgreens",
    "walmart health",
    "amazon health",
]

# Address-line fragments that indicate a hospital campus or training program.
_AFFILIATED_ADDRESS_KEYWORDS: list[str] = [
    "house staff",
    "gme",                   # Graduate Medical Education
    "resident",
    "fellow",
    "harry hines",           # UT Southwestern main campus address
    "inwood rd",             # Parkland / Zale-Lipshy campus
]


# ---------------------------------------------------------------------------
# Website-domain affiliation filter
# Applied AFTER Serper enriches the website field (NPI data has no website).
# ---------------------------------------------------------------------------

# Practices whose Serper-enriched website resolves to one of these domains are
# employed by / contracted to a health-system chain → skip them entirely.
_CHAIN_WEBSITE_DOMAINS: frozenset[str] = frozenset({
    "bswhealth.com",            # Baylor Scott & White Health
    "baylorhealth.com",
    "utswmed.org",              # UT Southwestern Medical Center
    "utsouthwestern.edu",
    "texashealth.org",          # Texas Health Resources
    "hcahealthcare.com",        # HCA Healthcare
    "christushealth.org",
    "methodisthealthsystem.org",
    "privia.com",               # Privia Medical Group (contracted)
    "optum.com",
})

# Serper sometimes returns a directory/listing page as a practice's "website".
# Clear the URL (so we don't scrape it) but keep the practice in the pipeline.
_AGGREGATOR_WEBSITE_DOMAINS: frozenset[str] = frozenset({
    "healthgrades.com",
    "zocdoc.com",
    "vitals.com",
    "webmd.com",
    "ratemds.com",
    "doximity.com",
    "yelp.com",
    "yellowpages.com",
    "psychologytoday.com",
    "google.com",
    "facebook.com",
    "linkedin.com",
    "instagram.com",
})


def _website_domain(url: str) -> str:
    """Return the bare registrable domain of *url* (strips www. prefix)."""
    if not url:
        return ""
    if not url.startswith("http"):
        url = f"https://{url}"
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def is_chain_website(url: str) -> bool:
    """Return True if *url* belongs to a known health-system chain/network."""
    domain = _website_domain(url)
    if not domain:
        return False
    return any(domain == cd or domain.endswith("." + cd) for cd in _CHAIN_WEBSITE_DOMAINS)


def is_aggregator_website(url: str) -> bool:
    """Return True if *url* is a directory/aggregator, not the practice's own site."""
    domain = _website_domain(url)
    if not domain:
        return False
    return any(domain == ad or domain.endswith("." + ad) for ad in _AGGREGATOR_WEBSITE_DOMAINS)


def _contains_affiliate(text: str, keywords: list[str]) -> bool:
    padded = " " + text.lower() + " "
    return any(kw in padded for kw in keywords)


def is_independent_practice(org_name: str, address: str = "") -> bool:
    """
    Return True if this practice appears to be independent (not hospital /
    chain / affiliate).  Checks both the organisation/provider name and the
    registered practice-location address.
    PLLC and LLC are explicitly kept — most independent practices use them.
    """
    if _contains_affiliate(org_name, _AFFILIATED_NAME_KEYWORDS):
        return False
    if address and _contains_affiliate(address, _AFFILIATED_ADDRESS_KEYWORDS):
        return False
    return True


# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------

def _build_address(addr: dict) -> str:
    parts = [
        addr.get("address_1", ""),
        addr.get("address_2", ""),
        addr.get("city", ""),
        addr.get("state", ""),
        addr.get("postal_code", ""),
    ]
    return ", ".join(p.strip() for p in parts if p.strip())


def _practice_location(addresses: list[dict]) -> dict | None:
    for addr in (addresses or []):
        if addr.get("address_purpose", "").upper() == "LOCATION":
            return addr
    return None


# ---------------------------------------------------------------------------
# Result parser
# ---------------------------------------------------------------------------

def _parse_result(result: dict, specialty: str, enum_type: str) -> dict | None:
    """
    Convert a single NPI registry result to the pipeline practice dict.
    Returns None if the entry has no practice-location address.

    NPI-2 (organizations): display name = basic.organization_name
    NPI-1 (individuals):   display name = basic.organization_name if set,
                           otherwise first_name + last_name + MD/DO
    Filter name for NPI-1: always the organization_name from basic (the
    employer), so hospital-employed doctors are caught even when their
    personal name is used as the display name.
    """
    addresses: list[dict] = result.get("addresses", [])
    loc = _practice_location(addresses)
    if loc is None:
        return None

    basic: dict = result.get("basic", {})
    raw_org: str = basic.get("organization_name", "").strip()

    if enum_type == "NPI-2":
        display_name = raw_org
        filter_name = raw_org
    else:
        # NPI-1 — individual provider
        first = basic.get("first_name", "").strip()
        last = basic.get("last_name", "").strip()
        credential = basic.get("credential", "").strip().upper()
        suffix = "DO" if "DO" in credential else "MD"
        display_name = raw_org if raw_org else " ".join(filter(None, [first, last, suffix]))
        # Filter on the employer (organization_name), not the doctor's personal name
        filter_name = raw_org  # empty string = solo practice → passes filter

    # Only keep practices whose practice-location state is TX
    if loc.get("state", "").upper() != NPI_STATE:
        return None

    phone: str = loc.get("telephone_number", "").strip()
    city: str = loc.get("city", "").strip().title()
    address: str = _build_address(loc)

    practice: dict = {
        "name": display_name,
        "phone": phone,
        "website": "",
        "address": address,
        "city": city,
        "specialty": specialty,
        "_filter_name": filter_name,   # used by filter, stripped before returning
    }

    # For individual providers (NPI-1), store the doctor's name separately
    # so both the practice name and the contact person name reach Brevo.
    if enum_type == "NPI-1":
        practice["doctor_first"] = basic.get("first_name", "").strip().title()
        practice["doctor_last"] = basic.get("last_name", "").strip().title()

    return practice


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

def _fetch_one(
    taxonomy_desc: str,
    enumeration_type: str,
    city: str,
    specialty: str,
) -> tuple[list[dict], int, int]:
    """Single NPI API call. Returns (kept_practices, total_parsed, filtered_count)."""
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
        log.warning("npi_client: request failed [%s] %s/%s: %s", enumeration_type, specialty, city, exc)
        time.sleep(2)
        return [], 0, 0

    results = data.get("results") or []
    log.info(
        "  NPI [%s] %s / %s → %d results (result_count=%s)",
        enumeration_type, specialty, city, len(results), data.get("result_count", "?"),
    )

    kept: list[dict] = []
    filtered = 0

    for result in results:
        practice = _parse_result(result, specialty, enumeration_type)
        if practice is None:
            continue

        # NPI city param is not a strict match — drop results outside the queried city
        if practice["city"].lower() != city.lower():
            log.debug("  [filtered] %s — city mismatch (%s)", practice["name"], practice["city"])
            filtered += 1
            continue

        filter_name = practice.pop("_filter_name")
        address = practice["address"]

        if is_independent_practice(filter_name, address):
            kept.append(practice)
        else:
            log.debug("  [filtered] %s — affiliated/chain (filter_name=%r)", practice["name"], filter_name)
            filtered += 1

    return kept, len(results), filtered


def fetch_npi_practices(specialty: str, cities: list[str]) -> list[dict]:
    """
    Query the NPI registry for *specialty* in each of *cities* (state=TX).
    Filters out hospital/chain affiliates. Prints a summary line.
    """
    taxonomy_desc = TAXONOMY_DESCRIPTIONS.get(specialty)
    if not taxonomy_desc:
        log.warning("npi_client: no taxonomy description for specialty %r — skipping", specialty)
        return []

    all_practices: list[dict] = []
    total_parsed = 0
    total_filtered = 0

    for city in cities:
        for enum_type in ("NPI-1", "NPI-2"):
            kept, parsed, filtered = _fetch_one(taxonomy_desc, enum_type, city, specialty)
            all_practices.extend(kept)
            total_parsed += parsed
            total_filtered += filtered
            time.sleep(0.25)

    log.info(
        "NPI %s: %d total results, %d kept (independent), %d filtered out (affiliated)",
        specialty, total_parsed, len(all_practices), total_filtered,
    )
    return all_practices
