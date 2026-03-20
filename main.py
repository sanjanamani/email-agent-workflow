"""
main.py — Inara AI outbound email agent entry point.

Runs the full daily workflow:
1. Discover new practices via Google Maps
2. Filter to independent practices
3. Find contact emails via Snov.io
4. Add new contacts to Google Sheet (dedup enforced)
5. Check which existing contacts are due for Email 1/2/3
6. Generate personalized emails via Claude
7. Send via Gmail
8. Update the Google Sheet

Run daily at 9am CT via Render cron job.
Set DRY_RUN=true to simulate everything without sending emails or writing to the sheet.
"""

import logging
import sys
import time
from datetime import date

import config
from src.claude_client import ClaudeClient
from src.email_sequence import build_contact_record, filter_due_rows, should_close
from src.filters import filter_practices
from src.gmail_client import GmailClient
from src.maps_client import enrich_with_details, search_all_queries
from src.sheets_client import SheetsClient
from src.snov_client import SnovClient

logger = logging.getLogger("main")


def run() -> None:
    """Execute the full daily email agent workflow."""
    logger.info("=" * 60)
    logger.info("Inara AI Email Agent — daily run starting (%s)", date.today().isoformat())
    if config.DRY_RUN:
        logger.info("[DRY RUN] No emails will be sent; no sheet writes will occur.")
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # 1. Initialise clients
    # ------------------------------------------------------------------
    try:
        sheets = _init_sheets()
        gmail = _init_gmail()
        snov = SnovClient()
        claude = ClaudeClient()
    except Exception as exc:  # noqa: BLE001
        logger.critical("Failed to initialise clients: %s", exc)
        sys.exit(1)

    # Ensure the sheet has headers
    if not config.DRY_RUN:
        sheets.ensure_headers()
    else:
        logger.info("[DRY RUN] Skipping ensure_headers()")

    # ------------------------------------------------------------------
    # 2. Discover new practices
    # ------------------------------------------------------------------
    logger.info("--- Step 1: Discovering practices via Google Maps ---")
    try:
        raw_practices = search_all_queries()
    except Exception as exc:  # noqa: BLE001
        logger.error("Maps discovery failed: %s — continuing with 0 new practices.", exc)
        raw_practices = []

    logger.info("Raw practices found: %d", len(raw_practices))

    # ------------------------------------------------------------------
    # 3. Filter to independent practices
    # ------------------------------------------------------------------
    logger.info("--- Step 2: Filtering to independent practices ---")
    independent_practices = filter_practices(raw_practices)

    # ------------------------------------------------------------------
    # 4. Enrich with phone/website details + find emails via Snov
    # ------------------------------------------------------------------
    logger.info("--- Step 3: Enriching practices and finding emails ---")

    # Load existing emails/names from sheet to avoid duplicates
    try:
        known_emails = sheets.get_known_emails() if not config.DRY_RUN else set()
        known_names = sheets.get_known_practice_names() if not config.DRY_RUN else set()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load known contacts from sheet: %s", exc)
        known_emails = set()
        known_names = set()

    new_contacts_added = 0

    for practice in independent_practices:
        practice_name_lower = practice.get("name", "").lower().strip()

        # Skip if we already have this practice by name
        if practice_name_lower in known_names:
            logger.debug("Skipping already-known practice: %s", practice.get("name"))
            continue

        # Enrich with phone + website from Places Details
        try:
            practice = enrich_with_details(practice)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to enrich '%s': %s", practice.get("name"), exc)

        # Skip if no website (can't do Snov lookup)
        website = practice.get("website", "")
        if not website:
            logger.info("No website for '%s' — skipping Snov lookup", practice.get("name"))
            continue

        # Find best contact email via Snov
        try:
            contact = snov.best_contact(website)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Snov lookup failed for '%s': %s", practice.get("name"), exc)
            contact = None

        if not contact or not contact.get("email"):
            logger.info("No email found for '%s' (%s) — skipping", practice.get("name"), website)
            continue

        email = contact["email"].lower().strip()

        # Dedup by email address
        if email in known_emails:
            logger.info("Email %s already in sheet — skipping duplicate", email)
            continue

        # Build record and add to sheet
        record = build_contact_record(practice, contact)

        if config.DRY_RUN:
            logger.info(
                "[DRY RUN] Would add to sheet: '%s' <%s> (%s)",
                record["Practice Name"], email, record["Specialty"],
            )
        else:
            row_idx = sheets.append_contact(record)
            if row_idx > 0:
                known_emails.add(email)
                known_names.add(practice_name_lower)
                new_contacts_added += 1

    logger.info("New contacts added to sheet: %d", new_contacts_added)

    # ------------------------------------------------------------------
    # 5. Process email sequence for existing contacts
    # ------------------------------------------------------------------
    logger.info("--- Step 4: Processing email sequence ---")

    try:
        all_rows = sheets.get_all_rows() if not config.DRY_RUN else []
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to read sheet rows: %s", exc)
        all_rows = []

    # Close out any sequences where email 3 was sent long enough ago
    _close_finished_sequences(sheets, all_rows)

    # Find rows due for email sends today
    due_rows = filter_due_rows(all_rows)
    emails_sent_today = 0

    for row, email_number in due_rows:
        if emails_sent_today >= config.MAX_EMAILS_PER_DAY:
            logger.warning(
                "Daily email cap (%d) reached — stopping sends for today.", config.MAX_EMAILS_PER_DAY
            )
            break

        row_index = row.get("_row_index", -1)
        recipient_email = row.get("Email", "").strip()
        practice_name = row.get("Practice Name", "unknown")

        logger.info(
            "Preparing Email %d for '%s' <%s> (row %d)",
            email_number, practice_name, recipient_email, row_index,
        )

        # Generate personalized email via Claude
        try:
            subject, body = claude.generate_email_for_contact(row, email_number)
        except Exception as exc:  # noqa: BLE001
            logger.error("Claude generation failed for '%s': %s", practice_name, exc)
            if not config.DRY_RUN and row_index > 0:
                sheets.mark_email_error(row_index, f"Claude error: {exc}")
            continue

        # Sign the email
        body = _append_signature(body)

        # Send via Gmail
        sent = gmail.send_email(to=recipient_email, subject=subject, body=body)

        if sent:
            emails_sent_today += 1
            logger.info(
                "Email %d sent to '%s' <%s>", email_number, practice_name, recipient_email
            )
            if not config.DRY_RUN and row_index > 0:
                sheets.mark_email_sent(row_index, email_number)
            # Rate limit between sends
            if emails_sent_today < config.MAX_EMAILS_PER_DAY:
                logger.debug("Waiting %ds before next send…", config.SEND_DELAY_SECONDS)
                time.sleep(config.SEND_DELAY_SECONDS)
        else:
            logger.error("Failed to send email %d to '%s' <%s>", email_number, practice_name, recipient_email)
            if not config.DRY_RUN and row_index > 0:
                sheets.mark_email_error(row_index, f"Send failed for email {email_number}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info(
        "Daily run complete — new contacts: %d, emails sent: %d",
        new_contacts_added, emails_sent_today,
    )
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_sheets() -> SheetsClient:
    """Initialise SheetsClient; raises on missing credentials."""
    if not config.GOOGLE_SHEET_ID and not config.DRY_RUN:
        raise RuntimeError("GOOGLE_SHEET_ID is not set.")
    if config.DRY_RUN:
        # Return a dummy client that won't actually connect
        return _DryRunSheetsClient()
    return SheetsClient()


def _init_gmail() -> GmailClient:
    """Initialise GmailClient; raises on missing credentials."""
    if config.DRY_RUN:
        return _DryRunGmailClient()
    return GmailClient()


def _close_finished_sequences(sheets: SheetsClient, rows: list[dict]) -> None:
    """Transition eligible rows from EMAIL3_SENT → CLOSED."""
    for row in rows:
        if should_close(row):
            row_index = row.get("_row_index", -1)
            practice_name = row.get("Practice Name", "?")
            if config.DRY_RUN:
                logger.info("[DRY RUN] Would close sequence for '%s'", practice_name)
            elif row_index > 0:
                sheets.update_cell(row_index, "Status", config.STATUS_CLOSED)
                logger.info("Closed sequence for '%s' (row %d)", practice_name, row_index)


def _append_signature(body: str) -> str:
    """Ensure the email ends with Sanjana's signature."""
    sig = "\n\n-- \nSanjana Manikandan\nUT Dallas | Independent Researcher"
    # Don't double-append
    if "Sanjana Manikandan" in body and "UT Dallas" in body:
        return body
    return body.rstrip() + sig


# ---------------------------------------------------------------------------
# Dry-run stub clients (avoid any real I/O in dry-run mode)
# ---------------------------------------------------------------------------

class _DryRunSheetsClient(SheetsClient):
    """No-op SheetsClient used in dry-run mode."""

    def __init__(self):  # noqa: D107
        # Skip parent __init__ to avoid credential loading
        self.spreadsheet_id = config.GOOGLE_SHEET_ID or "DRY_RUN_SHEET"
        self._creds = None
        self._service = None

    def ensure_headers(self):
        logger.info("[DRY RUN] Would ensure sheet headers")

    def get_all_rows(self):
        logger.info("[DRY RUN] Would read all sheet rows (returning empty list)")
        return []

    def get_known_emails(self):
        return set()

    def get_known_practice_names(self):
        return set()

    def append_contact(self, contact):
        logger.info("[DRY RUN] Would append contact: %s", contact.get("Practice Name"))
        return 1

    def update_cell(self, row_index, column_name, value):
        logger.info("[DRY RUN] Would update row %d, col %r = %r", row_index, column_name, value)
        return True

    def update_row(self, row_index, updates):
        logger.info("[DRY RUN] Would update row %d: %s", row_index, updates)
        return True

    def mark_email_sent(self, row_index, email_number):
        logger.info("[DRY RUN] Would mark email %d sent for row %d", email_number, row_index)
        return True

    def mark_email_error(self, row_index, note):
        logger.info("[DRY RUN] Would mark error on row %d: %s", row_index, note)
        return True


class _DryRunGmailClient(GmailClient):
    """No-op GmailClient used in dry-run mode."""

    def __init__(self):  # noqa: D107
        self._creds = None
        self._service = None

    def send_email(self, to, subject, body, retry=True):
        logger.info(
            "[DRY RUN] Would send email → to=%s | subject=%r | body_preview=%r",
            to, subject, body[:100],
        )
        return True


if __name__ == "__main__":
    run()
