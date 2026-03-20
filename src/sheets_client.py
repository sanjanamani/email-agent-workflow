"""
sheets_client.py — Google Sheets API client for contact tracking.

The sheet is the single source of truth for:
- Which practices we've already found
- Which emails have been sent and when
- Reply status

Authentication uses service-account credentials embedded in GMAIL_CREDENTIALS
(same base64-encoded JSON). If you're using a separate service account for
Sheets, set GOOGLE_SHEETS_CREDENTIALS instead.

Sheet layout (see config.SHEET_COLUMNS for the full list):
    A: Practice Name    F: Email            K: Email 3 Sent Date
    B: Specialty        G: Contact Name     L: Reply Received
    C: Address          H: Title            M: Reply Date
    D: Phone            I: Email Sent Date  N: Status
    E: Website          J: Email 2 Sent Date O: Notes
"""

import base64
import json
import logging
import os
from datetime import date
from typing import Optional

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

logger = logging.getLogger("sheets_client")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Row 1 is the header; data starts at row 2
HEADER_ROW = 1
DATA_START_ROW = 2


class SheetsClient:
    """Read/write wrapper around the Google Sheets API v4."""

    def __init__(self, spreadsheet_id: str = "", credentials: Optional[Credentials] = None):
        self.spreadsheet_id = spreadsheet_id or config.GOOGLE_SHEET_ID
        self._creds = credentials or _load_credentials()
        self._service = None

    def _get_service(self):
        if self._service is None:
            self._service = build("sheets", "v4", credentials=self._creds, cache_discovery=False)
        return self._service

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def ensure_headers(self) -> None:
        """Write column headers to row 1 if they're not already there."""
        try:
            existing = self._read_range("A1:O1")
            if existing and existing[0] == config.SHEET_COLUMNS:
                return  # Headers already correct
            self._write_range("A1", [config.SHEET_COLUMNS])
            logger.info("Sheet headers written.")
        except HttpError as exc:
            logger.error("Failed to write sheet headers: %s", exc)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_all_rows(self) -> list[dict]:
        """
        Return all data rows as a list of dicts keyed by SHEET_COLUMNS names.
        Row index (1-based, including header) is stored as '_row_index'.
        """
        try:
            rows = self._read_range(f"A{DATA_START_ROW}:O")
        except HttpError as exc:
            logger.error("Failed to read sheet: %s", exc)
            return []

        result = []
        for i, row in enumerate(rows or []):
            # Pad short rows to full column width
            padded = row + [""] * (len(config.SHEET_COLUMNS) - len(row))
            record = dict(zip(config.SHEET_COLUMNS, padded))
            record["_row_index"] = DATA_START_ROW + i
            result.append(record)
        return result

    def get_known_emails(self) -> set[str]:
        """Return the set of all email addresses already in the sheet."""
        rows = self.get_all_rows()
        return {r["Email"].lower().strip() for r in rows if r.get("Email")}

    def get_known_practice_names(self) -> set[str]:
        """Return the set of all practice names already in the sheet (lowercase)."""
        rows = self.get_all_rows()
        return {r["Practice Name"].lower().strip() for r in rows if r.get("Practice Name")}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append_contact(self, contact: dict) -> int:
        """
        Append a new contact row to the sheet.

        `contact` should have keys matching SHEET_COLUMNS (missing keys default to "").
        Returns the row index of the newly appended row (1-based).
        """
        row_values = [contact.get(col, "") for col in config.SHEET_COLUMNS]
        try:
            svc = self._get_service()
            result = (
                svc.spreadsheets()
                .values()
                .append(
                    spreadsheetId=self.spreadsheet_id,
                    range="A1",
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [row_values]},
                )
                .execute()
            )
            updated_range = result.get("updates", {}).get("updatedRange", "")
            row_idx = _parse_row_from_range(updated_range)
            logger.info("Appended contact '%s' at row %d", contact.get("Practice Name"), row_idx)
            return row_idx
        except HttpError as exc:
            logger.error("Failed to append contact '%s': %s", contact.get("Practice Name"), exc)
            return -1

    def update_cell(self, row_index: int, column_name: str, value: str) -> bool:
        """
        Update a single cell identified by row_index (1-based) and column name.
        Returns True on success.
        """
        col_num = config.COL.get(column_name)
        if col_num is None:
            logger.error("Unknown column name: %s", column_name)
            return False

        cell = f"{_col_letter(col_num)}{row_index}"
        try:
            self._write_range(cell, [[value]])
            logger.debug("Updated %s = %r", cell, value)
            return True
        except HttpError as exc:
            logger.error("Failed to update cell %s: %s", cell, exc)
            return False

    def update_row(self, row_index: int, updates: dict) -> bool:
        """
        Apply multiple column updates to a single row.

        `updates` is a dict of {column_name: value}.
        Returns True if all updates succeeded.
        """
        success = True
        for col, value in updates.items():
            if not self.update_cell(row_index, col, value):
                success = False
        return success

    def mark_email_sent(self, row_index: int, email_number: int) -> bool:
        """
        Record that email N (1, 2, or 3) was sent today.
        Updates the corresponding date column and Status column.
        """
        today = date.today().isoformat()
        col_map = {
            1: ("Email Sent Date", config.STATUS_EMAIL1_SENT),
            2: ("Email 2 Sent Date", config.STATUS_EMAIL2_SENT),
            3: ("Email 3 Sent Date", config.STATUS_EMAIL3_SENT),
        }
        if email_number not in col_map:
            logger.error("Invalid email number: %d", email_number)
            return False

        date_col, status = col_map[email_number]
        return self.update_row(row_index, {date_col: today, "Status": status})

    def mark_email_error(self, row_index: int, note: str) -> bool:
        """Mark a row as errored with a note."""
        return self.update_row(row_index, {"Status": config.STATUS_ERROR, "Notes": note})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_range(self, range_notation: str) -> list[list]:
        svc = self._get_service()
        result = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_notation)
            .execute()
        )
        return result.get("values", [])

    def _write_range(self, start_cell: str, values: list[list]) -> None:
        svc = self._get_service()
        svc.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=start_cell,
            valueInputOption="RAW",
            body={"values": values},
        ).execute()


# ---------------------------------------------------------------------------
# Credentials loader
# ---------------------------------------------------------------------------

def _load_credentials() -> Credentials:
    """
    Load Google OAuth2 credentials from the GMAIL_CREDENTIALS env var
    (base64-encoded token.json contents).

    Falls back to reading token.json from disk for local development.
    """
    b64 = config.GMAIL_CREDENTIALS_B64
    if b64:
        try:
            token_json = base64.b64decode(b64).decode("utf-8")
            token_data = json.loads(token_json)
            return _creds_from_dict(token_data)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to decode GMAIL_CREDENTIALS: %s", exc)
            raise

    # Local fallback
    token_path = os.path.join(os.path.dirname(__file__), "..", "token.json")
    if os.path.exists(token_path):
        with open(token_path) as f:
            token_data = json.load(f)
        return _creds_from_dict(token_data)

    raise RuntimeError(
        "No Google credentials found. Set GMAIL_CREDENTIALS env var or provide token.json."
    )


def _creds_from_dict(data: dict) -> Credentials:
    """Build a google.oauth2.credentials.Credentials from a token dict."""
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes", SCOPES),
    )


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _col_letter(col_num: int) -> str:
    """Convert 1-based column number to letter (1=A, 26=Z, 27=AA, …)."""
    result = ""
    while col_num > 0:
        col_num, remainder = divmod(col_num - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _parse_row_from_range(range_str: str) -> int:
    """
    Extract the row number from a range string like "Sheet1!A5:O5".
    Returns -1 if parsing fails.
    """
    try:
        # Strip sheet name if present
        cell_part = range_str.split("!")[-1]
        # Take the first cell reference
        first_cell = cell_part.split(":")[0]
        # Strip letters to get row number
        row_str = "".join(c for c in first_cell if c.isdigit())
        return int(row_str) if row_str else -1
    except Exception:  # noqa: BLE001
        return -1
