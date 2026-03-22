"""
find_and_enrich.py — Finds independent endocrinology and orthopedic practices
in DFW via Google Maps, scrapes their websites for a contact email, then either
adds the contact to Brevo or appends them to call_list.csv.

Usage:
    python find_and_enrich.py

Required env vars:
    GOOGLE_MAPS_API_KEY
    BREVO_API_KEY
    BREVO_LIST_ID

Optional env vars:
    DRY_RUN          - set to "true" to print actions without calling APIs
    MAX_PRACTICES    - cap total practices processed (default 100)
"""

import csv
import os
import re
import time
import logging
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_LIST_ID = int(os.getenv("BREVO_LIST_ID", "0"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")
MAX_PRACTICES = int(os.getenv("MAX_PRACTICES", "100"))

SPECIALTIES = ["endocrinology", "orthopedic"]
CITIES = ["Dallas TX", "Plano TX", "Frisco TX", "Allen TX", "McKinney TX", "Richardson TX"]

CALL_LIST_CSV = "call_list.csv"
CALL_LIST_HEADERS = ["Practice Name", "Phone", "Website", "Specialty", "City", "Priority"]

SCRAPE_PATHS = ["", "/contact", "/about"]
SCRAPE_TIMEOUT = 10
REQUEST_DELAY = 1  # seconds between practices

# Emails that don't reach a real person
SKIP_PREFIXES = {"noreply", "no-reply", "info", "support", "admin"}

# Hospital / health-system keywords — matched against practice name + address
HOSPITAL_KEYWORDS = [
    "hospital", "health system", "health network", "medical center",
    "ut southwestern", "utsw", "baylor", "hca", "tenet", "methodist",
    "parkland", "children's health", "childrens health", "texas health",
    "university", "academic medical", "veterans affairs", "va clinic",
    "kaiser", "dignity health", "commonspirit", "ascension",
]

# Review keywords that indicate high priority for the call list
HIGH_PRIORITY_KEYWORDS = ["prior authorization", "insurance", "prior auth", "preauthorization"]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ---------------------------------------------------------------------------
# Google Maps helpers
# ---------------------------------------------------------------------------

def search_places(query: str) -> list[dict]:
    """Text-search Google Maps Places API and return raw place results."""
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": query, "key": GOOGLE_MAPS_API_KEY}
    results = []
    while True:
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            log.error("Maps search failed for %r: %s", query, exc)
            break
        results.extend(data.get("results", []))
        token = data.get("next_page_token")
        if not token:
            break
        params = {"pagetoken": token, "key": GOOGLE_MAPS_API_KEY}
        time.sleep(2)  # Maps API requires a short pause before using next_page_token
    return results


def get_place_details(place_id: str) -> dict:
    """Fetch website, phone, and reviews for a place."""
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "website,formatted_phone_number,reviews",
        "key": GOOGLE_MAPS_API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("result", {})
    except requests.RequestException as exc:
        log.warning("Place details fetch failed for %s: %s", place_id, exc)
        return {}


def is_independent_practice(name: str, address: str) -> bool:
    """Return True if this looks like a solo/small-group practice."""
    text = (name + " " + address).lower()
    return not any(kw in text for kw in HOSPITAL_KEYWORDS)


def extract_city(address: str) -> str:
    """Pull the city name from a formatted Maps address string."""
    parts = [p.strip() for p in address.split(",")]
    # Address format: "Street, City, ST ZIP, USA"
    return parts[1] if len(parts) >= 3 else ""


def reviews_suggest_high_priority(reviews: list[dict]) -> bool:
    """Return True if any review mentions prior-auth or insurance friction."""
    for review in reviews:
        text = review.get("text", "").lower()
        if any(kw in text for kw in HIGH_PRIORITY_KEYWORDS):
            return True
    return False


# ---------------------------------------------------------------------------
# Email scraping helpers
# ---------------------------------------------------------------------------

def scrape_emails_from_url(url: str) -> set[str]:
    """Fetch one URL and return all valid email addresses found on the page."""
    try:
        resp = requests.get(
            url, timeout=SCRAPE_TIMEOUT, headers=BROWSER_HEADERS, allow_redirects=True
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.debug("Could not fetch %s: %s", url, exc)
        return set()

    soup = BeautifulSoup(resp.text, "html.parser")
    emails: set[str] = set()

    # mailto: links first (most reliable)
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip().lower()
            if EMAIL_RE.fullmatch(addr):
                emails.add(addr)

    # Plain-text scan
    for match in EMAIL_RE.finditer(soup.get_text(" ")):
        emails.add(match.group().lower())

    # Drop false positives (image paths, etc.)
    emails = {e for e in emails if not e.split("@")[0].endswith((".png", ".jpg", ".gif", ".svg"))}
    return emails


def find_contact_email(website: str) -> str:
    """
    Scrape homepage, /contact, and /about for a usable contact email.
    Returns an empty string if none is found.
    """
    if not website:
        return ""

    # Normalise base URL
    parsed = urlparse(website if website.startswith("http") else f"https://{website}")
    base = f"{parsed.scheme}://{parsed.netloc}"

    for path in SCRAPE_PATHS:
        url = base + path
        emails = scrape_emails_from_url(url)
        # Prefer non-generic emails; fall back to any human-reachable one
        personal = [e for e in emails if e.split("@")[0] not in SKIP_PREFIXES]
        if personal:
            return personal[0]
        if emails:
            # All are generic — save for fallback but keep looking
            fallback = next(iter(emails))

    # If we only found generic addresses across all pages, return the first one
    # (already filtered to exclude noreply/support/admin/info at caller)
    return ""


def pick_email(website: str) -> str:
    """
    Return a usable email from the website, or '' if none found.
    Skips any address whose local part is in SKIP_PREFIXES.
    """
    if not website:
        return ""

    parsed = urlparse(website if website.startswith("http") else f"https://{website}")
    base = f"{parsed.scheme}://{parsed.netloc}"

    for path in SCRAPE_PATHS:
        url = base + path
        emails = scrape_emails_from_url(url)
        for email in sorted(emails):
            local = email.split("@")[0]
            if local not in SKIP_PREFIXES:
                return email

    return ""


# ---------------------------------------------------------------------------
# Brevo helpers
# ---------------------------------------------------------------------------

def add_to_brevo(email: str, practice_name: str, phone: str) -> str:
    """
    POST the contact to Brevo. Returns:
        "added"     — new contact created
        "duplicate" — contact already exists
        "error"     — some other failure
    """
    if DRY_RUN:
        log.info("[DRY RUN] Would add to Brevo: %s <%s>", practice_name, email)
        return "added"

    payload = {
        "email": email,
        "attributes": {
            "FIRSTNAME": "",
            "LASTNAME": practice_name,
            "PHONE": phone,
            "SMS": phone,
        },
        "listIds": [BREVO_LIST_ID],
        "updateEnabled": False,
    }
    headers = {"api-key": BREVO_API_KEY, "Content-Type": "application/json"}
    try:
        resp = requests.post(
            "https://api.brevo.com/v3/contacts",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 201:
            return "added"
        if resp.status_code == 400:
            body = resp.json()
            # Brevo returns code "duplicate_parameter" for existing contacts
            if "duplicate" in body.get("code", "").lower():
                return "duplicate"
        log.warning("Brevo error %d for %s: %s", resp.status_code, email, resp.text[:200])
        return "error"
    except requests.RequestException as exc:
        log.error("Brevo request failed for %s: %s", email, exc)
        return "error"


# ---------------------------------------------------------------------------
# Call list helpers
# ---------------------------------------------------------------------------

def append_to_call_list(row: dict) -> None:
    """Append one row to call_list.csv, creating the file with headers if needed."""
    if DRY_RUN:
        log.info(
            "[DRY RUN] Would add to call list: %s | %s | %s",
            row["Practice Name"], row["Phone"], row["Priority"],
        )
        return

    file_exists = os.path.isfile(CALL_LIST_CSV)
    with open(CALL_LIST_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CALL_LIST_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def collect_practices() -> list[dict]:
    """
    Run all specialty × city queries and return a deduplicated list of
    independent-practice dicts (with place_id, name, address, specialty, city).
    """
    seen_ids: set[str] = set()
    practices: list[dict] = []

    for specialty in SPECIALTIES:
        for city in CITIES:
            query = f"{specialty} clinic {city}"
            log.info("Searching: %s", query)
            results = search_places(query)
            for place in results:
                pid = place.get("place_id", "")
                if pid in seen_ids:
                    continue
                name = place.get("name", "")
                address = place.get("formatted_address", "")
                if not is_independent_practice(name, address):
                    log.debug("Filtered (hospital/system): %s", name)
                    continue
                seen_ids.add(pid)
                practices.append({
                    "place_id": pid,
                    "name": name,
                    "address": address,
                    "specialty": specialty.title(),
                    "city": extract_city(address),
                })

    return practices


def run() -> None:
    log.info("=== find_and_enrich starting (DRY_RUN=%s, MAX=%d) ===", DRY_RUN, MAX_PRACTICES)

    if not GOOGLE_MAPS_API_KEY:
        log.error("GOOGLE_MAPS_API_KEY is not set. Exiting.")
        return
    if not DRY_RUN and not BREVO_API_KEY:
        log.error("BREVO_API_KEY is not set. Exiting.")
        return
    if not DRY_RUN and not BREVO_LIST_ID:
        log.error("BREVO_LIST_ID is not set. Exiting.")
        return

    practices = collect_practices()
    log.info("Found %d independent practices (before cap)", len(practices))
    practices = practices[:MAX_PRACTICES]

    counts = {"added": 0, "call_list": 0, "duplicate": 0, "error": 0}

    for i, practice in enumerate(practices, 1):
        name = practice["name"]
        log.info("[%d/%d] Processing: %s", i, len(practices), name)

        # Fetch details (website, phone, reviews)
        details = get_place_details(practice["place_id"])
        website = details.get("website", "")
        phone = details.get("formatted_phone_number", "")
        reviews = details.get("reviews", [])

        # Scrape for email
        email = pick_email(website)

        if email:
            result = add_to_brevo(email, name, phone)
            if result == "added":
                log.info("  ✓ Added to Brevo: %s <%s>", name, email)
                counts["added"] += 1
            elif result == "duplicate":
                log.info("  — Duplicate (already in Brevo): %s", email)
                counts["duplicate"] += 1
            else:
                log.warning("  ✗ Brevo error for %s", name)
                counts["error"] += 1
        else:
            priority = "HIGH" if reviews_suggest_high_priority(reviews) else "NORMAL"
            append_to_call_list({
                "Practice Name": name,
                "Phone": phone,
                "Website": website,
                "Specialty": practice["specialty"],
                "City": practice["city"],
                "Priority": priority,
            })
            log.info("  → Call list (%s): %s | %s", priority, name, phone)
            counts["call_list"] += 1

        time.sleep(REQUEST_DELAY)

    # Final summary
    log.info("")
    log.info("=== Done ===")
    log.info("  Added to Brevo : %d", counts["added"])
    log.info("  Added to call list : %d", counts["call_list"])
    log.info("  Duplicates skipped : %d", counts["duplicate"])
    log.info("  Errors             : %d", counts["error"])
    if counts["call_list"] and not DRY_RUN:
        log.info("  Call list saved to : %s", CALL_LIST_CSV)


if __name__ == "__main__":
    run()
