"""Tests for src/sheets_client.py"""

from unittest.mock import MagicMock, patch

import pytest

import config
from src.sheets_client import SheetsClient, _col_letter, _parse_row_from_range


# ---------------------------------------------------------------------------
# Helper to build a mock SheetsClient without real credentials
# ---------------------------------------------------------------------------

def make_mock_client(spreadsheet_id="test_sheet_id"):
    """Build a SheetsClient with all credential loading bypassed."""
    client = SheetsClient.__new__(SheetsClient)
    client.spreadsheet_id = spreadsheet_id
    client._creds = MagicMock()
    client._service = None
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetAllRows:
    def test_returns_empty_list_on_empty_sheet(self):
        client = make_mock_client()
        with patch.object(client, "_read_range", return_value=[]):
            rows = client.get_all_rows()
        assert rows == []

    def test_parses_rows_into_dicts(self):
        client = make_mock_client()
        raw_rows = [
            ["North TX Ortho", "Orthopedics", "123 Main St, Plano, TX", "(972) 555-0100",
             "https://northtxortho.com", "jen@northtxortho.com", "Jennifer Smith", "Office Manager",
             "2024-01-15", "", "", "", "", "Email 1 Sent", ""]
        ]
        with patch.object(client, "_read_range", return_value=raw_rows):
            rows = client.get_all_rows()

        assert len(rows) == 1
        assert rows[0]["Practice Name"] == "North TX Ortho"
        assert rows[0]["Email"] == "jen@northtxortho.com"
        assert rows[0]["Status"] == "Email 1 Sent"
        assert rows[0]["_row_index"] == 2  # DATA_START_ROW + 0

    def test_pads_short_rows(self):
        """Rows with fewer columns than SHEET_COLUMNS should be padded with empty strings."""
        client = make_mock_client()
        raw_rows = [["North TX Ortho", "Orthopedics"]]  # Only 2 of 15 columns
        with patch.object(client, "_read_range", return_value=raw_rows):
            rows = client.get_all_rows()
        assert rows[0]["Email"] == ""
        assert rows[0]["Status"] == ""


class TestGetKnownEmails:
    def test_returns_set_of_emails(self):
        client = make_mock_client()
        mock_rows = [
            {**{col: "" for col in config.SHEET_COLUMNS}, "Email": "a@b.com", "_row_index": 2},
            {**{col: "" for col in config.SHEET_COLUMNS}, "Email": "c@d.com", "_row_index": 3},
        ]
        with patch.object(client, "get_all_rows", return_value=mock_rows):
            emails = client.get_known_emails()
        assert emails == {"a@b.com", "c@d.com"}

    def test_lowercases_emails(self):
        client = make_mock_client()
        mock_rows = [
            {**{col: "" for col in config.SHEET_COLUMNS}, "Email": "UPPER@CLINIC.COM", "_row_index": 2},
        ]
        with patch.object(client, "get_all_rows", return_value=mock_rows):
            emails = client.get_known_emails()
        assert "upper@clinic.com" in emails

    def test_excludes_empty_emails(self):
        client = make_mock_client()
        mock_rows = [
            {**{col: "" for col in config.SHEET_COLUMNS}, "Email": "", "_row_index": 2},
        ]
        with patch.object(client, "get_all_rows", return_value=mock_rows):
            emails = client.get_known_emails()
        assert emails == set()


class TestGetKnownPracticeNames:
    def test_returns_lowercase_names(self):
        client = make_mock_client()
        mock_rows = [
            {**{col: "" for col in config.SHEET_COLUMNS}, "Practice Name": "North TX Ortho", "_row_index": 2},
        ]
        with patch.object(client, "get_all_rows", return_value=mock_rows):
            names = client.get_known_practice_names()
        assert "north tx ortho" in names


class TestMarkEmailSent:
    def test_marks_email1_sent(self):
        client = make_mock_client()
        with patch.object(client, "update_row", return_value=True) as mock_update:
            result = client.mark_email_sent(row_index=2, email_number=1)
        assert result is True
        mock_update.assert_called_once()
        call_args = mock_update.call_args[0]
        updates = call_args[1]
        assert "Email Sent Date" in updates
        assert updates["Status"] == config.STATUS_EMAIL1_SENT

    def test_marks_email2_sent(self):
        client = make_mock_client()
        with patch.object(client, "update_row", return_value=True) as mock_update:
            result = client.mark_email_sent(row_index=3, email_number=2)
        assert result is True
        updates = mock_update.call_args[0][1]
        assert "Email 2 Sent Date" in updates
        assert updates["Status"] == config.STATUS_EMAIL2_SENT

    def test_marks_email3_sent(self):
        client = make_mock_client()
        with patch.object(client, "update_row", return_value=True) as mock_update:
            result = client.mark_email_sent(row_index=4, email_number=3)
        assert result is True
        updates = mock_update.call_args[0][1]
        assert "Email 3 Sent Date" in updates

    def test_invalid_email_number_returns_false(self):
        client = make_mock_client()
        result = client.mark_email_sent(row_index=2, email_number=5)
        assert result is False


class TestMarkEmailError:
    def test_marks_error_status_and_note(self):
        client = make_mock_client()
        with patch.object(client, "update_row", return_value=True) as mock_update:
            result = client.mark_email_error(row_index=2, note="Send failed")
        assert result is True
        updates = mock_update.call_args[0][1]
        assert updates["Status"] == config.STATUS_ERROR
        assert updates["Notes"] == "Send failed"


class TestHelpers:
    def test_col_letter_a(self):
        assert _col_letter(1) == "A"

    def test_col_letter_z(self):
        assert _col_letter(26) == "Z"

    def test_col_letter_aa(self):
        assert _col_letter(27) == "AA"

    def test_col_letter_o(self):
        assert _col_letter(15) == "O"

    def test_parse_row_from_range_sheet_name(self):
        assert _parse_row_from_range("Sheet1!A5:O5") == 5

    def test_parse_row_from_range_no_sheet_name(self):
        assert _parse_row_from_range("A12:O12") == 12

    def test_parse_row_from_range_invalid(self):
        assert _parse_row_from_range("") == -1

    def test_parse_row_from_range_single_cell(self):
        assert _parse_row_from_range("B3") == 3
