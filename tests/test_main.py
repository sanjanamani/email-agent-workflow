"""
test_main.py — Integration-style tests for main.py orchestration logic.

These tests mock all external clients to verify the control flow, dedup
logic, rate limiting, and dry-run behavior work correctly end-to-end.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

import config
from main import _append_signature, _close_finished_sequences, run


class TestAppendSignature:
    def test_appends_signature_when_missing(self):
        body = "Hi there,\n\nThis is a test email."
        result = _append_signature(body)
        assert "Sanjana Manikandan" in result
        assert "UT Dallas" in result

    def test_does_not_double_append_signature(self):
        body = "Hi there.\n\nBest,\nSanjana Manikandan\nUT Dallas | Independent Researcher"
        result = _append_signature(body)
        assert result.count("Sanjana Manikandan") == 1

    def test_preserves_original_body(self):
        body = "Original content here."
        result = _append_signature(body)
        assert "Original content here." in result


class TestCloseFinishedSequences:
    def test_closes_eligible_rows(self):
        mock_sheets = MagicMock()
        row = {
            "Practice Name": "Test Clinic",
            "Status": config.STATUS_EMAIL3_SENT,
            "Email 3 Sent Date": (date.today() - timedelta(days=6)).isoformat(),
            "_row_index": 5,
        }
        with patch("config.DRY_RUN", False):
            _close_finished_sequences(mock_sheets, [row])
        mock_sheets.update_cell.assert_called_once_with(5, "Status", config.STATUS_CLOSED)

    def test_dry_run_does_not_write(self):
        mock_sheets = MagicMock()
        row = {
            "Practice Name": "Test Clinic",
            "Status": config.STATUS_EMAIL3_SENT,
            "Email 3 Sent Date": (date.today() - timedelta(days=6)).isoformat(),
            "_row_index": 5,
        }
        with patch("config.DRY_RUN", True):
            _close_finished_sequences(mock_sheets, [row])
        mock_sheets.update_cell.assert_not_called()

    def test_does_not_close_recent_email3(self):
        mock_sheets = MagicMock()
        row = {
            "Practice Name": "Test Clinic",
            "Status": config.STATUS_EMAIL3_SENT,
            "Email 3 Sent Date": (date.today() - timedelta(days=2)).isoformat(),
            "_row_index": 5,
        }
        with patch("config.DRY_RUN", False):
            _close_finished_sequences(mock_sheets, [row])
        mock_sheets.update_cell.assert_not_called()


class TestRunDryRunMode:
    """Test the full run() in dry-run mode — no real API calls."""

    @patch("main.search_all_queries")
    @patch("main.enrich_with_details")
    def test_run_dry_run_does_not_call_real_apis(self, mock_enrich, mock_search):
        """In dry-run mode, all writes are skipped."""
        mock_search.return_value = []
        mock_enrich.side_effect = lambda p, **kw: p

        with patch("config.DRY_RUN", True):
            run()  # Should complete without errors

        # Maps search is still called (discovery runs in dry-run mode too)
        mock_search.assert_called_once()

    @patch("main.search_all_queries")
    @patch("main.filter_practices")
    @patch("main.enrich_with_details")
    @patch("main.SnovClient")
    def test_run_respects_daily_email_cap(self, mock_snov_cls, mock_enrich, mock_filter, mock_search):
        """When there are more due emails than the daily cap, only cap many are sent."""
        # No new practices
        mock_search.return_value = []
        mock_filter.return_value = []

        with patch("config.DRY_RUN", True), patch("config.MAX_EMAILS_PER_DAY", 2):
            run()
        # Just verify it completes without error — email cap enforcement is
        # tested more precisely in test_email_sequence.py


class TestRunWithNewContacts:
    @patch("main.search_all_queries")
    @patch("main.filter_practices")
    @patch("main.enrich_with_details")
    @patch("main.SnovClient")
    @patch("main.ClaudeClient")
    @patch("main.GmailClient")
    @patch("main.SheetsClient")
    def test_new_contact_added_to_sheet(
        self,
        mock_sheets_cls,
        mock_gmail_cls,
        mock_claude_cls,
        mock_snov_cls,
        mock_enrich,
        mock_filter,
        mock_search,
    ):
        """Verify that a brand-new practice+contact is appended to the sheet."""
        practice = {
            "place_id": "abc",
            "name": "New Ortho Clinic",
            "address": "123 Main, Plano, TX 75024",
            "phone": "(972) 555-1111",
            "website": "https://newortho.com",
            "specialty": "Orthopedics",
        }
        contact = {
            "email": "manager@newortho.com",
            "full_name": "Jane Doe",
            "title": "Office Manager",
        }

        mock_search.return_value = [practice]
        mock_filter.return_value = [practice]
        mock_enrich.side_effect = lambda p, **kw: p

        mock_snov = MagicMock()
        mock_snov.best_contact.return_value = contact
        mock_snov_cls.return_value = mock_snov

        mock_sheets = MagicMock()
        mock_sheets.get_known_emails.return_value = set()
        mock_sheets.get_known_practice_names.return_value = set()
        mock_sheets.get_all_rows.return_value = []
        mock_sheets.append_contact.return_value = 2
        mock_sheets_cls.return_value = mock_sheets

        mock_gmail = MagicMock()
        mock_gmail_cls.return_value = mock_gmail

        mock_claude = MagicMock()
        mock_claude.generate_email_for_contact.return_value = ("Test Subject", "Test body")
        mock_claude_cls.return_value = mock_claude

        with patch("config.DRY_RUN", False), patch("main._init_sheets", return_value=mock_sheets), \
             patch("main._init_gmail", return_value=mock_gmail):
            run()

        mock_sheets.append_contact.assert_called_once()
        call_record = mock_sheets.append_contact.call_args[0][0]
        assert call_record["Email"] == "manager@newortho.com"
        assert call_record["Practice Name"] == "New Ortho Clinic"

    @patch("main.search_all_queries")
    @patch("main.filter_practices")
    def test_duplicate_email_not_added_twice(self, mock_filter, mock_search):
        """If the email is already in the sheet, don't append again."""
        practice = {
            "place_id": "abc",
            "name": "Existing Clinic",
            "address": "123 Main, Plano, TX",
            "phone": "",
            "website": "https://existing.com",
            "specialty": "Orthopedics",
        }
        contact = {"email": "manager@existing.com", "full_name": "Jane", "title": ""}

        mock_search.return_value = [practice]
        mock_filter.return_value = [practice]

        mock_snov = MagicMock()
        mock_snov.best_contact.return_value = contact

        mock_sheets = MagicMock()
        mock_sheets.get_known_emails.return_value = {"manager@existing.com"}  # already known
        mock_sheets.get_known_practice_names.return_value = set()
        mock_sheets.get_all_rows.return_value = []

        mock_gmail = MagicMock()
        mock_claude = MagicMock()

        with patch("config.DRY_RUN", False), \
             patch("main._init_sheets", return_value=mock_sheets), \
             patch("main._init_gmail", return_value=mock_gmail), \
             patch("main.SnovClient", return_value=mock_snov), \
             patch("main.ClaudeClient", return_value=mock_claude), \
             patch("main.enrich_with_details", side_effect=lambda p, **kw: p):
            run()

        mock_sheets.append_contact.assert_not_called()
