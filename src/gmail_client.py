"""
gmail_client.py — Gmail API client for sending emails via OAuth2.

Sends plain-text emails from sanjana@emberluna.co using a pre-authorised
token stored in GMAIL_CREDENTIALS (base64-encoded token.json).

One-time local setup:
    python -c "from src.gmail_client import run_oauth_flow; run_oauth_flow()"
Then base64-encode the generated token.json and set it as GMAIL_CREDENTIALS.
"""

import base64
import json
import logging
import os
import time
from email.mime.text import MIMEText
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config
from src.sheets_client import _creds_from_dict

logger = logging.getLogger("gmail_client")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


class GmailClient:
    """Sends and checks email via the Gmail API."""

    def __init__(self, credentials: Optional[Credentials] = None):
        self._creds = credentials or _load_gmail_credentials()
        self._service = None

    def _get_service(self):
        if self._service is None:
            # Refresh token if expired
            if self._creds and self._creds.expired and self._creds.refresh_token:
                try:
                    self._creds.refresh(Request())
                except Exception as exc:  # noqa: BLE001
                    logger.error("Failed to refresh Gmail token: %s", exc)
                    raise
            self._service = build("gmail", "v1", credentials=self._creds, cache_discovery=False)
        return self._service

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        retry: bool = True,
    ) -> bool:
        """
        Send a plain-text email.

        Returns True on success. On failure, retries once if retry=True,
        then returns False.
        """
        if config.DRY_RUN:
            logger.info(
                "[DRY RUN] Would send email to=%s subject=%r body_preview=%r",
                to, subject, body[:120],
            )
            return True

        message = _build_message(
            sender=f"{config.FROM_NAME} <{config.FROM_EMAIL}>",
            to=to,
            subject=subject,
            body=body,
        )

        for attempt in (1, 2):
            try:
                svc = self._get_service()
                svc.users().messages().send(
                    userId="me",
                    body={"raw": message},
                ).execute()
                logger.info("Email sent to %s (subject: %r)", to, subject)
                return True
            except HttpError as exc:
                if attempt == 1 and retry:
                    logger.warning("Gmail send failed (attempt 1), retrying in 5s: %s", exc)
                    time.sleep(5)
                else:
                    logger.error("Gmail send failed (attempt %d): %s", attempt, exc)
                    return False
        return False

    # ------------------------------------------------------------------
    # Check for replies (used for future reply-detection enhancement)
    # ------------------------------------------------------------------

    def check_for_replies(self, since_date: str = "") -> list[dict]:
        """
        Search inbox for replies to our outreach emails.

        Returns list of {from, subject, date, thread_id} for matching messages.
        `since_date` is an ISO date string like '2024-01-01'; defaults to last 30 days.
        """
        if not since_date:
            from datetime import date, timedelta
            since_date = (date.today() - timedelta(days=30)).isoformat()

        query = f"to:{config.FROM_EMAIL} after:{since_date.replace('-', '/')}"
        try:
            svc = self._get_service()
            results = svc.users().messages().list(userId="me", q=query, maxResults=50).execute()
            messages = results.get("messages", [])
        except HttpError as exc:
            logger.error("Failed to check for replies: %s", exc)
            return []

        replies = []
        for msg in messages:
            try:
                detail = svc.users().messages().get(
                    userId="me", id=msg["id"], format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()
                headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
                replies.append({
                    "from": headers.get("From", ""),
                    "subject": headers.get("Subject", ""),
                    "date": headers.get("Date", ""),
                    "thread_id": detail.get("threadId", ""),
                    "message_id": msg["id"],
                })
            except HttpError:
                continue

        logger.info("Found %d potential replies since %s", len(replies), since_date)
        return replies


# ---------------------------------------------------------------------------
# OAuth flow helper (run once locally to generate token.json)
# ---------------------------------------------------------------------------

def run_oauth_flow(credentials_file: str = "credentials.json") -> None:
    """
    Interactive OAuth2 flow — run this ONCE locally to generate token.json.

    Prerequisites:
        1. Download OAuth2 client credentials from Google Cloud Console.
        2. Save as credentials.json in project root.
        3. Run: python -c "from src.gmail_client import run_oauth_flow; run_oauth_flow()"
        4. Authenticate in the browser window that opens.
        5. Base64-encode the resulting token.json:
               base64 -w0 token.json
        6. Set that output as the GMAIL_CREDENTIALS env var on Render.
    """
    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
    creds = flow.run_local_server(port=0)
    token_path = "token.json"
    with open(token_path, "w") as f:
        f.write(creds.to_json())
    print(f"Token saved to {token_path}")
    print("Base64 encode it with:  base64 -w0 token.json")


# ---------------------------------------------------------------------------
# Credentials loader
# ---------------------------------------------------------------------------

def _load_gmail_credentials() -> Credentials:
    """
    Load Gmail OAuth2 credentials from GMAIL_CREDENTIALS env var (base64 JSON)
    or from token.json on disk (local dev).
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

    token_path = os.path.join(os.path.dirname(__file__), "..", "token.json")
    if os.path.exists(token_path):
        with open(token_path) as f:
            token_data = json.load(f)
        return _creds_from_dict(token_data)

    raise RuntimeError(
        "No Gmail credentials found. Set GMAIL_CREDENTIALS env var or provide token.json."
    )


# ---------------------------------------------------------------------------
# Message builder
# ---------------------------------------------------------------------------

def _build_message(sender: str, to: str, subject: str, body: str) -> str:
    """Build a base64url-encoded RFC 2822 message string."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return raw
