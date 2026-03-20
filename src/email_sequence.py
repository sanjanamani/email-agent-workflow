"""
email_sequence.py — Determines which email (if any) is due for each contact.

Business rules:
    - Email 1: Send as soon as contact is added (Status = "New")
    - Email 2: Send 5 days after Email 1, if no reply
    - Email 3: Send 5 days after Email 2, if no reply
    - After Email 3: mark Status = "Closed"
    - Never email a contact with Status = "Replied" or "Closed"
    - Never exceed MAX_EMAILS_PER_DAY across the entire run
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import config

logger = logging.getLogger("email_sequence")


def get_due_email_number(row: dict) -> Optional[int]:
    """
    Examine a sheet row and return which email number (1, 2, or 3) is due today,
    or None if no email is due.

    Args:
        row: dict with keys from config.SHEET_COLUMNS plus '_row_index'

    Returns:
        1, 2, 3, or None
    """
    status = row.get("Status", "").strip()

    # Don't touch replied or closed contacts
    if status in (config.STATUS_REPLIED, config.STATUS_CLOSED):
        return None

    # Email 1: contact is new and has no email sent yet
    if status == config.STATUS_NEW or not row.get("Email Sent Date"):
        return 1

    # Email 2: email 1 sent, no email 2 yet, enough time has passed
    if status == config.STATUS_EMAIL1_SENT and not row.get("Email 2 Sent Date"):
        sent_date = _parse_date(row.get("Email Sent Date", ""))
        if sent_date and _days_since(sent_date) >= config.EMAIL_2_WAIT_DAYS:
            return 2
        return None

    # Email 3: email 2 sent, no email 3 yet, enough time has passed
    if status == config.STATUS_EMAIL2_SENT and not row.get("Email 3 Sent Date"):
        sent_date = _parse_date(row.get("Email 2 Sent Date", ""))
        if sent_date and _days_since(sent_date) >= config.EMAIL_2_WAIT_DAYS:
            return 3
        return None

    # Email 3 was sent — mark closed on next run (handled in main.py)
    if status == config.STATUS_EMAIL3_SENT:
        return None

    return None


def should_close(row: dict) -> bool:
    """
    Return True if this row should be transitioned to 'Closed' status.
    (Email 3 was already sent and enough time has passed.)
    """
    status = row.get("Status", "").strip()
    if status != config.STATUS_EMAIL3_SENT:
        return False
    sent_date = _parse_date(row.get("Email 3 Sent Date", ""))
    if sent_date and _days_since(sent_date) >= config.EMAIL_2_WAIT_DAYS:
        return True
    return False


def filter_due_rows(rows: list[dict]) -> list[tuple[dict, int]]:
    """
    Given all sheet rows, return [(row, email_number)] for rows that are due
    for an email today, sorted so that Email 1s come first.

    Excludes rows with missing email addresses.
    """
    due = []
    for row in rows:
        if not row.get("Email", "").strip():
            continue  # No email address — skip
        n = get_due_email_number(row)
        if n is not None:
            due.append((row, n))

    # Prioritise lower email numbers
    due.sort(key=lambda x: x[1])
    logger.info("Found %d rows due for email sends today", len(due))
    return due


def build_contact_record(practice: dict, contact: dict) -> dict:
    """
    Merge a practice dict (from Maps) and a contact dict (from Snov)
    into a sheet row dict ready for SheetsClient.append_contact().
    """
    return {
        "Practice Name": practice.get("name", ""),
        "Specialty": practice.get("specialty", ""),
        "Address": practice.get("address", ""),
        "Phone": practice.get("phone", ""),
        "Website": practice.get("website", ""),
        "Email": contact.get("email", ""),
        "Contact Name": contact.get("full_name", ""),
        "Title": contact.get("title", ""),
        "Email Sent Date": "",
        "Email 2 Sent Date": "",
        "Email 3 Sent Date": "",
        "Reply Received": "",
        "Reply Date": "",
        "Status": config.STATUS_NEW,
        "Notes": "",
    }


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _parse_date(date_str: str) -> Optional[date]:
    """Parse an ISO date string (YYYY-MM-DD). Returns None on failure."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        logger.warning("Could not parse date: %r", date_str)
        return None


def _days_since(d: date) -> int:
    """Return the number of days between d and today."""
    return (date.today() - d).days
