"""
sheets_client.py — Appends rows to a Google Sheet using the Sheets API.

Reuses the OAuth token already set up for Gmail (GMAIL_CREDENTIALS env var
points to the credentials JSON file; token is cached alongside it).

Sheet layout (two tabs):
  "Call List"   — practices with no email found
  "Brevo Added" — practices successfully pushed to Brevo

Columns (both tabs):
  Practice Name | Phone | Website | Specialty | City | Address | Status | Email | Date Added

On each run only new rows are appended — existing data is never overwritten.
"""

import json
import logging
import os
from datetime import date

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.send",   # keep Gmail scope intact
]

SHEET_COLUMNS = [
    "Practice Name", "Phone", "Website", "Specialty",
    "City", "Address", "Status", "Email", "Date Added",
]


def _get_credentials() -> Credentials:
    """
    Load OAuth credentials from GMAIL_CREDENTIALS env var path.
    Token is cached as token.json next to the credentials file.
    Refreshes automatically; opens browser flow only if no cached token.
    """
    creds_path = os.getenv("GMAIL_CREDENTIALS", "")
    if not creds_path or not os.path.exists(creds_path):
        raise FileNotFoundError(
            "GMAIL_CREDENTIALS env var must point to a valid credentials JSON file"
        )

    token_path = os.path.join(os.path.dirname(creds_path), "token.json")
    creds: Credentials | None = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


def _ensure_tab(service, sheet_id: str, tab_name: str) -> None:
    """Create the tab if it doesn't already exist."""
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if tab_name in existing:
        return

    body = {"requests": [{"addSheet": {"properties": {"title": tab_name}}}]}
    service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=body).execute()
    log.info("sheets: created tab '%s'", tab_name)

    # Write header row
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [SHEET_COLUMNS]},
    ).execute()


def _append_rows(service, sheet_id: str, tab_name: str, rows: list[list]) -> int:
    """Append *rows* to *tab_name* and return the number of rows written."""
    if not rows:
        return 0
    result = service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    return result.get("updates", {}).get("updatedRows", len(rows))


def append_to_sheet(call_rows: list[dict], brevo_rows: list[dict]) -> None:
    """
    Append call_rows to the "Call List" tab and brevo_rows to the
    "Brevo Added" tab of the sheet identified by GOOGLE_SHEET_ID.

    Each dict must contain the keys used in find_and_enrich.py
    (Practice Name, Phone, Website, Specialty, City, Address for call rows;
     same plus Email / Confidence for Brevo rows).

    Skips silently if GOOGLE_SHEET_ID is not set.
    """
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        log.info("sheets: GOOGLE_SHEET_ID not set — skipping Google Sheets export")
        return

    today = date.today().isoformat()

    try:
        creds = _get_credentials()
        service = build("sheets", "v4", credentials=creds)

        # ---- Call List tab ----
        _ensure_tab(service, sheet_id, "Call List")
        call_sheet_rows = [
            [
                r.get("Practice Name", ""),
                r.get("Phone", ""),
                r.get("Website", ""),
                r.get("Specialty", ""),
                r.get("City", ""),
                r.get("Address", ""),
                "Call List",
                "",           # no email
                today,
            ]
            for r in call_rows
        ]
        n_call = _append_rows(service, sheet_id, "Call List", call_sheet_rows)
        log.info("sheets: appended %d rows to 'Call List'", n_call)

        # ---- Brevo Added tab ----
        _ensure_tab(service, sheet_id, "Brevo Added")
        brevo_sheet_rows = [
            [
                r.get("Practice Name", ""),
                r.get("Phone", ""),
                r.get("Website", ""),
                r.get("Specialty", ""),
                r.get("City", ""),
                r.get("Address", ""),
                "Brevo Added",
                r.get("Email", ""),
                today,
            ]
            for r in brevo_rows
        ]
        n_brevo = _append_rows(service, sheet_id, "Brevo Added", brevo_sheet_rows)
        log.info("sheets: appended %d rows to 'Brevo Added'", n_brevo)

    except FileNotFoundError as exc:
        log.warning("sheets: %s — skipping export", exc)
    except HttpError as exc:
        log.error("sheets: Sheets API error — %s", exc)
