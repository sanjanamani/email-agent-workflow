"""Tests for src/gmail_client.py (Brevo SMTP implementation)."""

import base64
import smtplib
from unittest.mock import MagicMock, patch

import pytest

from src.gmail_client import GmailClient, _build_message


# ---------------------------------------------------------------------------
# _build_message helper (kept for compatibility)
# ---------------------------------------------------------------------------

class TestBuildMessage:
    def test_returns_base64url_encoded_string(self):
        raw = _build_message(
            sender="Sanjana M <sanjana@emberluna.co>",
            to="office@clinic.com",
            subject="Test Subject",
            body="Hello there.",
        )
        # Decode the outer base64url wrapping
        decoded = base64.urlsafe_b64decode(raw + "==").decode("utf-8")
        assert "Subject: Test Subject" in decoded
        assert "To: office@clinic.com" in decoded
        # The MIME body itself may be base64-encoded (utf-8 content transfer encoding).
        body_b64 = base64.b64encode(b"Hello there.").decode()
        assert "Hello there." in decoded or body_b64 in decoded

    def test_from_header_included(self):
        raw = _build_message(
            sender="Sanjana M <sanjana@emberluna.co>",
            to="office@clinic.com",
            subject="Hi",
            body="Body",
        )
        decoded = base64.urlsafe_b64decode(raw + "==")
        assert b"sanjana@emberluna.co" in decoded


# ---------------------------------------------------------------------------
# Dry-run behaviour
# ---------------------------------------------------------------------------

class TestGmailClientDryRun:
    def test_dry_run_send_returns_true(self):
        with patch("config.DRY_RUN", True):
            client = GmailClient()
            result = client.send_email(
                to="test@clinic.com",
                subject="Hello",
                body="Test body",
            )
            assert result is True

    def test_dry_run_does_not_open_smtp(self):
        with patch("config.DRY_RUN", True), \
             patch("smtplib.SMTP") as mock_smtp:
            client = GmailClient()
            client.send_email(to="test@clinic.com", subject="S", body="B")
            mock_smtp.assert_not_called()


# ---------------------------------------------------------------------------
# SMTP send behaviour
# ---------------------------------------------------------------------------

class TestGmailClientSend:
    _env = {"BREVO_SMTP_LOGIN": "user@example.com", "BREVO_API_KEY": "key123"}

    def test_send_email_success(self):
        with patch("config.DRY_RUN", False), \
             patch.dict("os.environ", self._env), \
             patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

            client = GmailClient()
            result = client.send_email(to="test@clinic.com", subject="Hi", body="Body")
            assert result is True

    def test_send_email_retries_once_on_failure(self):
        with patch("config.DRY_RUN", False), \
             patch.dict("os.environ", self._env), \
             patch("smtplib.SMTP") as mock_smtp_cls, \
             patch("time.sleep"):
            # First context manager raises, second succeeds
            ctx1 = MagicMock()
            ctx1.__enter__ = MagicMock(side_effect=smtplib.SMTPException("fail"))
            ctx1.__exit__ = MagicMock(return_value=False)

            ctx2 = MagicMock()
            ctx2.__enter__ = MagicMock(return_value=MagicMock())
            ctx2.__exit__ = MagicMock(return_value=False)

            mock_smtp_cls.side_effect = [ctx1, ctx2]

            client = GmailClient()
            result = client.send_email(to="test@clinic.com", subject="Hi", body="Body")
            assert result is True

    def test_send_email_returns_false_after_two_failures(self):
        with patch("config.DRY_RUN", False), \
             patch.dict("os.environ", self._env), \
             patch("smtplib.SMTP") as mock_smtp_cls, \
             patch("time.sleep"):
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(side_effect=smtplib.SMTPException("fail"))
            ctx.__exit__ = MagicMock(return_value=False)
            mock_smtp_cls.return_value = ctx

            client = GmailClient()
            result = client.send_email(
                to="test@clinic.com", subject="Hi", body="Body", retry=True
            )
            assert result is False

    def test_returns_false_when_credentials_missing(self):
        with patch("config.DRY_RUN", False), \
             patch.dict("os.environ", {}, clear=True):
            client = GmailClient()
            result = client.send_email(to="test@clinic.com", subject="Hi", body="Body")
            assert result is False


# ---------------------------------------------------------------------------
# check_for_replies — always returns [] with SMTP sender
# ---------------------------------------------------------------------------

class TestCheckForReplies:
    def test_returns_empty_list(self):
        client = GmailClient()
        replies = client.check_for_replies(since_date="2024-01-01")
        assert replies == []

    def test_returns_empty_list_without_date(self):
        client = GmailClient()
        assert client.check_for_replies() == []
