"""Tests for src/email_sequence.py"""

from datetime import date, timedelta

import pytest

import config
from src.email_sequence import (
    _days_since,
    _parse_date,
    build_contact_record,
    filter_due_rows,
    get_due_email_number,
    should_close,
)


class TestGetDueEmailNumber:
    def test_new_contact_gets_email_1(self, sheet_row_new):
        assert get_due_email_number(sheet_row_new) == 1

    def test_status_new_gets_email_1(self, sheet_row_new):
        sheet_row_new["Status"] = "New"
        assert get_due_email_number(sheet_row_new) == 1

    def test_email1_sent_recently_gets_none(self, sheet_row_new):
        """Email 1 sent only 2 days ago — not yet time for email 2."""
        row = dict(sheet_row_new)
        row["Email Sent Date"] = (date.today() - timedelta(days=2)).isoformat()
        row["Status"] = config.STATUS_EMAIL1_SENT
        assert get_due_email_number(row) is None

    def test_email1_sent_5_days_ago_gets_email_2(self, sheet_row_email1_sent):
        assert get_due_email_number(sheet_row_email1_sent) == 2

    def test_email2_sent_recently_gets_none(self, sheet_row_email1_sent):
        row = dict(sheet_row_email1_sent)
        row["Email 2 Sent Date"] = (date.today() - timedelta(days=2)).isoformat()
        row["Status"] = config.STATUS_EMAIL2_SENT
        assert get_due_email_number(row) is None

    def test_email2_sent_5_days_ago_gets_email_3(self, sheet_row_email2_sent):
        assert get_due_email_number(sheet_row_email2_sent) == 3

    def test_email3_sent_gets_none(self, sheet_row_email3_sent):
        assert get_due_email_number(sheet_row_email3_sent) is None

    def test_replied_gets_none(self, sheet_row_replied):
        assert get_due_email_number(sheet_row_replied) is None

    def test_closed_gets_none(self, sheet_row_new):
        row = dict(sheet_row_new)
        row["Status"] = config.STATUS_CLOSED
        assert get_due_email_number(row) is None

    def test_exactly_on_wait_day_gets_email_2(self, sheet_row_new):
        """Exactly EMAIL_2_WAIT_DAYS days ago should trigger email 2."""
        row = dict(sheet_row_new)
        row["Email Sent Date"] = (date.today() - timedelta(days=config.EMAIL_2_WAIT_DAYS)).isoformat()
        row["Status"] = config.STATUS_EMAIL1_SENT
        assert get_due_email_number(row) == 2

    def test_invalid_date_returns_none(self, sheet_row_new):
        row = dict(sheet_row_new)
        row["Email Sent Date"] = "not-a-date"
        row["Status"] = config.STATUS_EMAIL1_SENT
        assert get_due_email_number(row) is None


class TestShouldClose:
    def test_email3_sent_long_ago_should_close(self, sheet_row_email3_sent):
        assert should_close(sheet_row_email3_sent) is True

    def test_email3_sent_recently_should_not_close(self, sheet_row_email2_sent):
        row = dict(sheet_row_email2_sent)
        row["Email 3 Sent Date"] = (date.today() - timedelta(days=2)).isoformat()
        row["Status"] = config.STATUS_EMAIL3_SENT
        assert should_close(row) is False

    def test_email2_sent_should_not_close(self, sheet_row_email2_sent):
        assert should_close(sheet_row_email2_sent) is False

    def test_replied_should_not_close(self, sheet_row_replied):
        assert should_close(sheet_row_replied) is False

    def test_new_row_should_not_close(self, sheet_row_new):
        assert should_close(sheet_row_new) is False


class TestFilterDueRows:
    def test_returns_only_due_rows(self, sheet_row_new, sheet_row_email1_sent, sheet_row_replied):
        rows = [sheet_row_new, sheet_row_email1_sent, sheet_row_replied]
        # Email 1 sent 6 days ago (fixture) = due for email 2
        result = filter_due_rows(rows)
        assert len(result) == 2
        emails_due = [n for _, n in result]
        assert 1 in emails_due
        assert 2 in emails_due

    def test_skips_rows_without_email(self, sheet_row_new):
        row = dict(sheet_row_new)
        row["Email"] = ""
        result = filter_due_rows([row])
        assert result == []

    def test_sorts_email_1_first(self, sheet_row_new, sheet_row_email1_sent):
        # Put email_1_sent first in list — should be reordered
        result = filter_due_rows([sheet_row_email1_sent, sheet_row_new])
        assert result[0][1] == 1  # email 1 should come first
        assert result[1][1] == 2

    def test_empty_rows(self):
        assert filter_due_rows([]) == []


class TestBuildContactRecord:
    def test_merges_practice_and_contact(self, sample_practice, sample_contact):
        record = build_contact_record(sample_practice, sample_contact)
        assert record["Practice Name"] == sample_practice["name"]
        assert record["Specialty"] == sample_practice["specialty"]
        assert record["Email"] == sample_contact["email"]
        assert record["Contact Name"] == sample_contact["full_name"]
        assert record["Title"] == sample_contact["title"]
        assert record["Status"] == config.STATUS_NEW
        assert record["Email Sent Date"] == ""

    def test_all_sheet_columns_present(self, sample_practice, sample_contact):
        record = build_contact_record(sample_practice, sample_contact)
        for col in config.SHEET_COLUMNS:
            assert col in record, f"Missing column: {col}"


class TestHelpers:
    def test_parse_date_valid(self):
        d = _parse_date("2024-03-15")
        assert d == date(2024, 3, 15)

    def test_parse_date_invalid(self):
        assert _parse_date("not-a-date") is None
        assert _parse_date("") is None
        assert _parse_date(None) is None

    def test_days_since_today(self):
        assert _days_since(date.today()) == 0

    def test_days_since_yesterday(self):
        assert _days_since(date.today() - timedelta(days=1)) == 1

    def test_days_since_past(self):
        assert _days_since(date.today() - timedelta(days=10)) == 10
