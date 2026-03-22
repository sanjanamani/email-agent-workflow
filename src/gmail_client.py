"""
gmail_client.py — Brevo SMTP email sender.

Sends plain-text emails from sanjana@emberluna.co via smtp-relay.brevo.com
on port 587, authenticated with BREVO_SMTP_LOGIN and BREVO_API_KEY env vars.

The OAuth flow and Gmail API dependency have been removed entirely.
"""

import base64
import logging
import os
import smtplib
import time
from email.mime.text import MIMEText
from typing import Optional

import config

logger = logging.getLogger("gmail_client")

BREVO_SMTP_HOST = "smtp-relay.brevo.com"
BREVO_SMTP_PORT = 587


class GmailClient:
    """Sends email via the Brevo SMTP relay."""

    def __init__(self, credentials: Optional[object] = None):
        # Signature preserved for drop-in compatibility; credentials unused.
        pass

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
        Send a plain-text email via Brevo SMTP.

        Returns True on success. On failure, retries once if retry=True,
        then returns False.
        """
        if config.DRY_RUN:
            logger.info(
                "[DRY RUN] Would send email to=%s subject=%r body_preview=%r",
                to, subject, body[:120],
            )
            return True

        login = os.environ.get("BREVO_SMTP_LOGIN", "")
        api_key = os.environ.get("BREVO_API_KEY", "")
        if not login or not api_key:
            logger.error("BREVO_SMTP_LOGIN or BREVO_API_KEY not set; cannot send email.")
            return False

        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = f"{config.FROM_NAME} <{config.FROM_EMAIL}>"
        msg["To"] = to
        msg["Subject"] = subject

        for attempt in (1, 2):
            try:
                with smtplib.SMTP(BREVO_SMTP_HOST, BREVO_SMTP_PORT) as smtp:
                    smtp.ehlo()
                    smtp.starttls()
                    smtp.login(login, api_key)
                    smtp.sendmail(config.FROM_EMAIL, to, msg.as_string())
                logger.info("Email sent to %s (subject: %r)", to, subject)
                return True
            except smtplib.SMTPException as exc:
                if attempt == 1 and retry:
                    logger.warning(
                        "Brevo SMTP send failed (attempt 1), retrying in 5s: %s", exc
                    )
                    time.sleep(5)
                else:
                    logger.error("Brevo SMTP send failed (attempt %d): %s", attempt, exc)
                    return False

        return False

    # ------------------------------------------------------------------
    # Reply checking (not available via SMTP-only sender)
    # ------------------------------------------------------------------

    def check_for_replies(self, since_date: str = "") -> list[dict]:
        """
        Not available with the Brevo SMTP sender. Returns an empty list.
        """
        logger.warning("check_for_replies is not available with Brevo SMTP sender.")
        return []


# ---------------------------------------------------------------------------
# Message builder (kept for backward compatibility)
# ---------------------------------------------------------------------------

def _build_message(sender: str, to: str, subject: str, body: str) -> str:
    """Build a base64url-encoded RFC 2822 message string."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return raw
