"""Tests for src/gmail_client.py"""

import base64
from unittest.mock import MagicMock, patch

import pytest

from src.gmail_client import GmailClient, _build_message


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
        # Verify either the plain text is present, or its base64 encoding is.
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


class TestGmailClientDryRun:
    def test_dry_run_send_returns_true(self):
        with patch("config.DRY_RUN", True):
            client = GmailClient.__new__(GmailClient)
            client._creds = None
            client._service = None
            result = client.send_email(
                to="test@clinic.com",
                subject="Hello",
                body="Test body",
            )
            assert result is True

    def test_dry_run_does_not_call_gmail_api(self):
        with patch("config.DRY_RUN", True):
            client = GmailClient.__new__(GmailClient)
            client._creds = None
            client._service = MagicMock()

            client.send_email(to="test@clinic.com", subject="S", body="B")
            # Service should never be called in dry-run
            client._service.users.assert_not_called()


class TestGmailClientSend:
    def _make_client(self):
        client = GmailClient.__new__(GmailClient)
        client._creds = MagicMock()
        client._creds.expired = False
        client._service = None
        return client

    @patch("src.gmail_client.build")
    def test_send_email_success(self, mock_build):
        with patch("config.DRY_RUN", False):
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_service.users().messages().send().execute.return_value = {"id": "abc"}

            client = self._make_client()
            result = client.send_email(to="test@clinic.com", subject="Hi", body="Body")
            assert result is True

    @patch("src.gmail_client.build")
    def test_send_email_retries_once_on_failure(self, mock_build):
        from googleapiclient.errors import HttpError
        with patch("config.DRY_RUN", False):
            mock_service = MagicMock()
            mock_build.return_value = mock_service

            # First call fails, second succeeds
            mock_service.users().messages().send().execute.side_effect = [
                HttpError(resp=MagicMock(status=500), content=b"error"),
                {"id": "abc"},
            ]

            client = self._make_client()
            with patch("time.sleep"):  # don't actually sleep in tests
                result = client.send_email(to="test@clinic.com", subject="Hi", body="Body")
            assert result is True

    @patch("src.gmail_client.build")
    def test_send_email_returns_false_after_two_failures(self, mock_build):
        from googleapiclient.errors import HttpError
        with patch("config.DRY_RUN", False):
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_service.users().messages().send().execute.side_effect = HttpError(
                resp=MagicMock(status=500), content=b"error"
            )

            client = self._make_client()
            with patch("time.sleep"):
                result = client.send_email(to="test@clinic.com", subject="Hi", body="Body", retry=True)
            assert result is False

    @patch("src.gmail_client.build")
    def test_check_for_replies_returns_list(self, mock_build):
        with patch("config.DRY_RUN", False):
            mock_service = MagicMock()
            mock_build.return_value = mock_service

            mock_service.users().messages().list().execute.return_value = {
                "messages": [{"id": "msg1"}]
            }
            mock_service.users().messages().get().execute.return_value = {
                "threadId": "thread1",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "doctor@clinic.com"},
                        {"name": "Subject", "value": "Re: research"},
                        {"name": "Date", "value": "Mon, 1 Jan 2024"},
                    ]
                },
            }

            client = self._make_client()
            replies = client.check_for_replies(since_date="2024-01-01")
            assert len(replies) == 1
            assert replies[0]["from"] == "doctor@clinic.com"
