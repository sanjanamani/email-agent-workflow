"""
find_and_enrich.py — Orchestrates practice discovery, email scraping,
Claude validation, and routing to Brevo or call_list.csv.

Pipeline:
  1a. NPI Registry finds authoritative practices (no API key needed)
  1b. Claude finds additional practices via training knowledge
      NPI data is merged first; Claude fills in anything NPI misses.
  2. Serper fills gaps (phone, website, address) where missing
  3. Scrape each practice website for candidate email addresses
  4. Claude validates emails and flags fax numbers (single batch call)
  5. Route: email found → Brevo | no valid email → call_list.csv
  6. Save brevo_added.csv + call_list.csv, print summary

ENV VARS:
  ANTHROPIC_API_KEY   required
  SERPER_API_KEY      optional (enrichment skipped if absent)
  BREVO_API_KEY       required unless DRY_RUN=true
  BREVO_LIST_ID       required unless DRY_RUN=true  (default: 7)
  DRY_RUN             "true" → print actions, skip API writes, still save CSVs
  MAX_PRACTICES       cap on total practices (default: 300)
"""

import csv
import json
import logging
import os
import re
import time
from urllib.parse import urlparse

import anthropic
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from claude_finder import find_all_practices, CITIES
from npi_client import fetch_npi_practices
from serper_enricher import enrich_practice
from sheets_client import append_to_sheet

load_dotenv()

_log_level = logging.DEBUG if os.getenv("DEBUG_SCRAPE", "").lower() in ("1", "true") else logging.INFO
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
BREVO_API_KEY: str = os.getenv("BREVO_API_KEY", "")
BREVO_LIST_ID: int = int(os.getenv("BREVO_LIST_ID", "7"))
DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")
MAX_PRACTICES: int = int(os.getenv("MAX_PRACTICES", "300"))

SPECIALTIES = ["endocrinologist", "orthopedic surgeon"]

VALIDATION_MODEL = "claude-sonnet-4-6"

CALL_LIST_CSV = "call_list.csv"
BREVO_CSV = "brevo_added.csv"

CALL_LIST_HEADERS = [
    "Practice Name", "Phone", "Website", "Specialty", "City", "Address", "Reason",
]
BREVO_HEADERS = [
    "Practice Name", "Email", "Confidence", "City", "Specialty", "Phone", "Website",
]

SCRAPE_PATHS = ["", "/contact", "/contact-us", "/about", "/about-us"]
SCRAPE_PATHS_EXTRA = ["/staff", "/team", "/our-team", "/physicians"]

# Patterns for obfuscated emails
_OBFUSCATED_AT = re.compile(r"[a-zA-Z0-9._%+\-]+\s*(?:\[at\]| at |&#64;|%40)\s*[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
_DATA_EMAIL = re.compile(r'data-email=["\']([^"\']+)["\']', re.IGNORECASE)
_SCHEMA_EMAIL = re.compile(r'"email"\s*:\s*"([^"]+)"', re.IGNORECASE)
SCRAPE_TIMEOUT = 6

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

def normalize_phone(phone: str) -> str:
    """Strip everything except digits."""
    return re.sub(r"\D", "", phone or "")


def extract_domain(website: str) -> str:
    """Return bare domain (no www, no path) or empty string."""
    if not website:
        return ""
    if not website.startswith("http"):
        website = f"https://{website}"
    try:
        netloc = urlparse(website).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def is_duplicate(
    practice: dict, seen_phones: set[str], seen_domains: set[str]
) -> bool:
    phone = normalize_phone(practice.get("phone", ""))
    domain = extract_domain(practice.get("website", ""))
    return (bool(phone) and phone in seen_phones) or (
        bool(domain) and domain in seen_domains
    )


def register(
    practice: dict, seen_phones: set[str], seen_domains: set[str]
) -> None:
    phone = normalize_phone(practice.get("phone", ""))
    domain = extract_domain(practice.get("website", ""))
    if phone:
        seen_phones.add(phone)
    if domain:
        seen_domains.add(domain)


# ---------------------------------------------------------------------------
# Step 3 — Email scraping
# ---------------------------------------------------------------------------

def _extract_emails_from_response(resp: requests.Response) -> set[str]:
    """
    Extract all candidate emails from a single HTTP response using multiple strategies:
    - mailto: href links
    - plain-text regex
    - JSON-LD structured data (<script type="application/ld+json">)
    - schema.org "email" fields in raw HTML
    - obfuscated patterns: [at], " at ", &#64;, %40, data-email attributes
    """
    raw = resp.text
    soup = BeautifulSoup(raw, "html.parser")
    found: set[str] = set()

    # 1. mailto: links
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip().lower()
            if EMAIL_RE.fullmatch(addr):
                found.add(addr)

    # 2. Plain-text regex scan
    for m in EMAIL_RE.finditer(soup.get_text(" ")):
        found.add(m.group().lower())

    # 3. JSON-LD structured data
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
            # Walk the JSON looking for "email" keys
            stack = [data] if isinstance(data, dict) else data if isinstance(data, list) else []
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k.lower() == "email" and isinstance(v, str) and EMAIL_RE.fullmatch(v.strip()):
                            found.add(v.strip().lower())
                        elif isinstance(v, (dict, list)):
                            stack.append(v)
                elif isinstance(node, list):
                    stack.extend(node)
        except (json.JSONDecodeError, TypeError):
            pass

    # 4. schema.org "email" fields in raw HTML
    for m in _SCHEMA_EMAIL.finditer(raw):
        val = m.group(1).strip().lower()
        if EMAIL_RE.fullmatch(val):
            found.add(val)

    # 5. data-email attributes
    for m in _DATA_EMAIL.finditer(raw):
        val = m.group(1).strip().lower()
        if EMAIL_RE.fullmatch(val):
            found.add(val)

    # 6. Obfuscated patterns ([at], " at ", &#64;, %40)
    for m in _OBFUSCATED_AT.finditer(raw):
        normalised = (
            m.group()
            .replace("[at]", "@").replace(" at ", "@").replace("&#64;", "@")
            .replace("%40", "@").replace(" ", "")
        )
        if EMAIL_RE.fullmatch(normalised):
            found.add(normalised.lower())

    return found


def scrape_all_emails(website: str) -> list[str]:
    """
    Fetch standard paths plus extra staff/team pages and extract emails
    using multiple strategies (mailto, regex, JSON-LD, schema.org,
    obfuscation patterns). Falls back to extra paths if no email found
    on primary paths. Returns sorted, deduplicated list.
    """
    if not website:
        return []
    if not website.startswith("http"):
        website = f"https://{website}"

    parsed = urlparse(website)
    base = f"{parsed.scheme}://{parsed.netloc}"

    found: set[str] = set()

    def _fetch_and_extract(paths: list[str]) -> None:
        for path in paths:
            url = base + path
            try:
                resp = requests.get(
                    url,
                    timeout=SCRAPE_TIMEOUT,
                    headers=BROWSER_HEADERS,
                    allow_redirects=True,
                )
                resp.raise_for_status()
            except Exception as exc:
                log.debug("    scrape %s → FAILED: %s", url, exc)
                continue

            log.debug("    scrape %s → %d bytes, status %d", url, len(resp.content), resp.status_code)
            page_emails = _extract_emails_from_response(resp)
            if page_emails:
                log.debug("      raw emails on page: %s", sorted(page_emails))
            found.update(page_emails)

    _fetch_and_extract(SCRAPE_PATHS)

    # If primary paths yielded nothing, try staff/team pages
    if not found:
        log.debug("    no emails from primary paths, trying staff/team pages…")
        _fetch_and_extract(SCRAPE_PATHS_EXTRA)

    # Drop file-extension false positives
    before_filter = set(found)
    found = {
        e for e in found
        if not any(e.endswith(ext) for ext in (".png", ".jpg", ".gif", ".svg"))
    }
    rejected = before_filter - found
    if rejected:
        log.debug("    rejected (bad extension): %s", sorted(rejected))
    return sorted(found)


# ---------------------------------------------------------------------------
# Step 4 — Claude validates email + phone (single batch call)
# ---------------------------------------------------------------------------

_validation_client: anthropic.Anthropic | None = None


def _get_validation_client() -> anthropic.Anthropic:
    global _validation_client
    if _validation_client is None:
        _validation_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _validation_client


def _null_validation(reason: str) -> dict:
    return {
        "best_email": None,
        "email_confidence": "low",
        "phone_is_fax": False,
        "reason": reason,
    }


def validate_contacts_batch(practices: list[dict]) -> list[dict]:
    """
    Send all practices to Claude in one call. Returns a list of validation
    dicts (same order as input):
      {best_email, email_confidence, phone_is_fax, reason}
    Falls back to _null_validation for any practice that can't be parsed.
    """
    if not practices:
        return []

    entries = [
        {
            "index": i,
            "name": p.get("name", ""),
            "specialty": p.get("specialty", ""),
            "city": p.get("city", ""),
            "website": p.get("website", ""),
            "phone": p.get("phone", ""),
            "candidate_emails": p.get("_candidate_emails", []),
        }
        for i, p in enumerate(practices)
    ]

    prompt = (
        "Validate the following contact list for a medical outreach campaign.\n"
        "For each entry, pick the best email if multiple exist, flag if the phone "
        "looks like a fax, and rate confidence high/medium/low.\n\n"
        "Rules for best_email: reject noreply, no-reply, info@, admin@, support@, "
        "webmaster@, privacy@, press@, media@, patient-portal addresses. "
        "Prefer a direct staff or billing email. Set to null if none qualify.\n\n"
        f"Practices:\n{json.dumps(entries, indent=2)}\n\n"
        "Return ONLY a JSON array in the SAME ORDER (one object per practice) with fields:\n"
        '  "best_email": "email or null",\n'
        '  "email_confidence": "high/medium/low",\n'
        '  "phone_is_fax": true/false,\n'
        '  "reason": "one sentence"\n'
    )

    try:
        client = _get_validation_client()
        response = client.messages.create(
            model=VALIDATION_MODEL,
            max_tokens=150 * len(practices),
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            log.error("Batch validation: could not find JSON array in response")
            return [_null_validation("could not parse batch response")] * len(practices)

        results = json.loads(match.group())

        # Normalise and pad to match input length
        validated = []
        for i, p in enumerate(practices):
            r = results[i] if i < len(results) else {}
            if not isinstance(r, dict):
                r = {}
            # Normalise JSON null / string "null" → Python None
            email = r.get("best_email")
            if isinstance(email, str) and email.lower() in ("null", "none", ""):
                email = None
            validated.append({
                "best_email": email,
                "email_confidence": r.get("email_confidence", "low"),
                "phone_is_fax": bool(r.get("phone_is_fax", False)),
                "reason": r.get("reason", ""),
            })
        return validated

    except (anthropic.APIError, json.JSONDecodeError) as exc:
        log.error("Batch validation error: %s", exc)
        return [_null_validation(str(exc))] * len(practices)


# ---------------------------------------------------------------------------
# Step 5 — Brevo
# ---------------------------------------------------------------------------

def add_to_brevo(practice: dict, email: str, confidence: str) -> str:
    """
    POST the contact to Brevo.
    Returns "added", "duplicate", or "error".
    In DRY_RUN mode returns "added" without calling the API.
    """
    name = practice.get("name", "")
    phone = practice.get("phone", "")  # already cleared if fax

    if DRY_RUN:
        log.info("[DRY RUN] Would add to Brevo: %s <%s> [%s]", name, email, confidence)
        return "added"

    payload = {
        "email": email,
        "listIds": [BREVO_LIST_ID],
        "attributes": {
            "FIRSTNAME": "",
            "LASTNAME": name,
            "PHONE": phone,
            "PRACTICE_NAME": name,
            "SPECIALTY": practice.get("specialty", ""),
            "CITY": practice.get("city", ""),
        },
        "updateEnabled": False,
    }
    try:
        resp = requests.post(
            "https://api.brevo.com/v3/contacts",
            json=payload,
            headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
            timeout=15,
        )
        if resp.status_code == 201:
            return "added"
        if resp.status_code == 400:
            code = resp.json().get("code", "")
            if "duplicate" in code.lower():
                return "duplicate"
        log.warning(
            "Brevo returned %d for <%s>: %s", resp.status_code, email, resp.text[:200]
        )
        return "error"
    except requests.RequestException as exc:
        log.error("Brevo request failed for <%s>: %s", email, exc)
        return "error"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run() -> None:
    log.info(
        "=== find_and_enrich starting | DRY_RUN=%s | MAX_PRACTICES=%d ===",
        DRY_RUN, MAX_PRACTICES,
    )

    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY is not set. Exiting.")
        return
    if not DRY_RUN:
        if not BREVO_API_KEY:
            log.error("BREVO_API_KEY is not set. Exiting.")
            return
        if not BREVO_LIST_ID:
            log.error("BREVO_LIST_ID is not set. Exiting.")
            return

    brevo_rows: list[dict] = []
    call_rows: list[dict] = []
    counts = {"added": 0, "call_list": 0, "duplicate": 0, "error": 0}

    # -------------------------------------------------------------------
    # STEP 1a — NPI Registry (authoritative, no API key needed)
    # -------------------------------------------------------------------
    log.info("\n--- STEP 1a: NPI Registry ---")
    all_practices: list[dict] = []
    seen_phones: set[str] = set()
    seen_domains: set[str] = set()

    for specialty in SPECIALTIES:
        for p in fetch_npi_practices(specialty, CITIES):
            if len(all_practices) >= MAX_PRACTICES:
                log.info("MAX_PRACTICES=%d reached, stopping NPI fetch", MAX_PRACTICES)
                break
            if is_duplicate(p, seen_phones, seen_domains):
                log.debug("  skip duplicate (NPI): %s", p.get("name"))
                continue
            register(p, seen_phones, seen_domains)
            all_practices.append(p)
            log.info(
                "  [NPI] + %s (%s, %s) — total: %d",
                p.get("name"), p.get("specialty"), p.get("city"), len(all_practices),
            )

    log.info("NPI found %d unique practices", len(all_practices))

    # -------------------------------------------------------------------
    # STEP 1b — Claude finds additional practices
    # NPI data already registered in seen_phones/seen_domains so Claude
    # results that duplicate NPI entries are dropped automatically.
    # -------------------------------------------------------------------
    log.info("\n--- STEP 1b: Claude finding additional practices ---")

    for p in find_all_practices(SPECIALTIES):
        if len(all_practices) >= MAX_PRACTICES:
            log.info("MAX_PRACTICES=%d reached, stopping Claude search", MAX_PRACTICES)
            break
        if is_duplicate(p, seen_phones, seen_domains):
            log.debug("  skip duplicate (Claude): %s", p.get("name"))
            continue
        register(p, seen_phones, seen_domains)
        all_practices.append(p)
        log.info(
            "  [Claude] + %s (%s, %s) — total: %d",
            p.get("name"), p.get("specialty"), p.get("city"), len(all_practices),
        )

    log.info("Combined total after NPI + Claude: %d unique practices", len(all_practices))
    log.info("Waiting 180s for token bucket to refill before validation…")
    time.sleep(180)

    # -------------------------------------------------------------------
    # STEP 2 — Serper fills gaps
    # -------------------------------------------------------------------
    log.info("\n--- STEP 2: Serper enriching gaps ---")
    serper_key = os.getenv("SERPER_API_KEY", "")
    enriched_count = 0

    if not serper_key:
        log.info("SERPER_API_KEY not set — skipping enrichment")
    else:
        for p in all_practices:
            if p.get("website") and p.get("phone"):
                continue  # nothing to fill
            before_website = p.get("website", "")
            before_phone = p.get("phone", "")
            enriched = enrich_practice(p)
            p.update(enriched)
            if p.get("website") != before_website or p.get("phone") != before_phone:
                enriched_count += 1
                log.debug("  enriched: %s", p.get("name"))

    log.info("Serper enriched %d practices", enriched_count)

    # -------------------------------------------------------------------
    # STEP 3 — Scrape websites for candidate emails
    # -------------------------------------------------------------------
    log.info("\n--- STEP 3: Scraping websites for emails ---")

    for i, p in enumerate(all_practices):
        if i > 0:
            time.sleep(0.5)
        website = p.get("website", "")
        if website:
            emails = scrape_all_emails(website)
            p["_candidate_emails"] = emails
            log.info(
                "  %s → %d email(s) found  [%s]",
                p.get("name"), len(emails), website,
            )
        else:
            log.info("  %s → no website, skipping scrape", p.get("name"))
            p["_candidate_emails"] = []

    # -------------------------------------------------------------------
    # STEP 4 — Claude validates email + phone (single batch call)
    # -------------------------------------------------------------------
    log.info("\n--- STEP 4: Claude validating contacts ---")

    # Separate practices with contact info from those without
    to_validate = [p for p in all_practices if p.get("_candidate_emails") or p.get("phone")]
    no_info = [p for p in all_practices if not p.get("_candidate_emails") and not p.get("phone")]

    for p in no_info:
        p["_validation"] = _null_validation("no contact info found")

    if to_validate:
        validations = validate_contacts_batch(to_validate)
        for p, v in zip(to_validate, validations):
            p["_validation"] = v
            if v.get("phone_is_fax"):
                log.debug("  %s: phone flagged as fax, clearing", p.get("name"))
                p["phone"] = ""

    # -------------------------------------------------------------------
    # STEP 5 — Route to Brevo or call list
    # -------------------------------------------------------------------
    log.info("\n--- STEP 5: Routing ---")

    for p in all_practices:
        name = p.get("name", "Unknown")
        v = p.get("_validation", {})
        best_email: str | None = v.get("best_email")
        confidence: str = v.get("email_confidence", "low")
        phone: str = p.get("phone", "")

        if best_email and confidence in ("high", "medium"):
            result = add_to_brevo(p, best_email, confidence)

            if result == "added":
                print(f"✓  {name} → Brevo ({best_email}) [{confidence}]")
                counts["added"] += 1
                brevo_rows.append({
                    "Practice Name": name,
                    "Email": best_email,
                    "Confidence": confidence,
                    "City": p.get("city", ""),
                    "Specialty": p.get("specialty", ""),
                    "Phone": phone,
                    "Website": p.get("website", ""),
                })
            elif result == "duplicate":
                print(f"⟳  {name} → duplicate, skipped")
                counts["duplicate"] += 1
            else:
                print(f"✗  {name} → Brevo error")
                counts["error"] += 1

        else:
            # Determine a human-readable reason for the call list
            if not p.get("website") and not phone:
                reason = "no contact info found"
            elif v.get("phone_is_fax") and not best_email:
                reason = "fax number only"
            elif not best_email:
                reason = "no email found"
            elif confidence == "low":
                reason = f"low confidence email — {v.get('reason', '')}".rstrip(" —")
            else:
                reason = v.get("reason") or "no valid email"

            print(f"📞  {name} → call list ({reason})")
            counts["call_list"] += 1
            call_rows.append({
                "Practice Name": name,
                "Phone": phone,
                "Website": p.get("website", ""),
                "Specialty": p.get("specialty", ""),
                "City": p.get("city", ""),
                "Address": p.get("address", ""),
                "Reason": reason,
            })

    # -------------------------------------------------------------------
    # STEP 6 — Save CSVs and print summary
    # -------------------------------------------------------------------
    log.info("\n--- STEP 6: Saving output ---")

    if brevo_rows:
        with open(BREVO_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=BREVO_HEADERS)
            writer.writeheader()
            writer.writerows(brevo_rows)

    if call_rows:
        with open(CALL_LIST_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CALL_LIST_HEADERS)
            writer.writeheader()
            writer.writerows(call_rows)

    # Brevo rows lack an Address field — backfill with empty string so
    # sheets_client always receives a consistent dict shape.
    for r in brevo_rows:
        r.setdefault("Address", "")

    log.info("\n--- STEP 6b: Appending to Google Sheet ---")
    append_to_sheet(call_rows, brevo_rows)

    print()
    print(
        f"Done: {counts['added']} → Brevo | "
        f"{counts['call_list']} → call list | "
        f"{counts['duplicate']} duplicates | "
        f"{counts['error']} errors"
    )
    if call_rows:
        print(f"Call list saved to {CALL_LIST_CSV}")
    if brevo_rows:
        print(f"Brevo contacts saved to {BREVO_CSV}")


if __name__ == "__main__":
    run()
