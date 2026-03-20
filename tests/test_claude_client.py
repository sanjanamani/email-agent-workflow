"""Tests for src/claude_client.py"""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.claude_client import ClaudeClient, _fallback_email, _load_template, _parse_response


class TestParseResponse:
    def test_parses_subject_and_body(self):
        raw = "SUBJECT: Quick question about prior auth\n\nHi there,\n\nThis is the body."
        subject, body = _parse_response(raw)
        assert subject == "Quick question about prior auth"
        assert "Hi there," in body
        assert "This is the body." in body

    def test_case_insensitive_subject(self):
        raw = "subject: My Subject\n\nBody text here."
        subject, body = _parse_response(raw)
        assert subject == "My Subject"

    def test_handles_missing_subject_line(self):
        raw = "Just a plain body with no subject marker."
        subject, body = _parse_response(raw)
        assert subject == ""
        assert "Just a plain body" in body

    def test_strips_blank_lines_after_subject(self):
        raw = "SUBJECT: Hello\n\n\n\nBody starts here."
        subject, body = _parse_response(raw)
        assert body.startswith("Body starts here.")

    def test_handles_empty_response(self):
        subject, body = _parse_response("")
        assert subject == ""
        assert body == ""


class TestLoadTemplate:
    def test_loads_email1_template(self):
        content = _load_template(1)
        assert content  # not empty
        assert "{practice_name}" in content

    def test_loads_email2_template(self):
        content = _load_template(2)
        assert content
        assert "{practice_name}" in content

    def test_loads_email3_template(self):
        content = _load_template(3)
        assert content
        assert "{practice_name}" in content

    def test_returns_empty_for_missing_template(self):
        content = _load_template(99)
        assert content == ""


class TestFallbackEmail:
    def test_email1_contains_required_elements(self):
        subject, body = _fallback_email(1, "North TX Ortho", "Orthopedics", "Plano", "Jennifer")
        assert "Sanjana" in body
        assert "UT Dallas" in body
        assert "prior authorization" in body.lower() or "prior auth" in body.lower()
        assert subject  # non-empty

    def test_email2_contains_survey_link(self):
        subject, body = _fallback_email(2, "DFW Endocrine", "Endocrinology", "Dallas", "")
        assert "tally.so" in body

    def test_email3_is_short(self):
        subject, body = _fallback_email(3, "Practice", "Specialty", "City", "")
        # Should be 3 sentences or fewer — rough check: not too long
        assert len(body) < 600

    def test_uses_contact_name_in_greeting_when_provided(self):
        _, body = _fallback_email(1, "Any Practice", "Orthopedics", "Frisco", "Dr. Jones")
        assert "Dr. Jones" in body

    def test_uses_team_greeting_when_no_contact(self):
        _, body = _fallback_email(1, "North TX Ortho", "Orthopedics", "Plano", "")
        assert "team" in body.lower() or "there" in body.lower()


class TestClaudeClientDryRun:
    def test_dry_run_returns_fallback(self):
        """In dry-run mode, generate_email should return fallback without calling the API."""
        with patch("config.DRY_RUN", True):
            client = ClaudeClient(api_key="fake")
            subject, body = client.generate_email(
                email_number=1,
                practice_name="Test Practice",
                specialty="Orthopedics",
                city="Plano",
                contact_name="Jane",
            )
            assert subject  # non-empty
            assert "Sanjana" in body

    def test_generate_email_for_contact_uses_row(self, sheet_row_new):
        with patch("config.DRY_RUN", True):
            client = ClaudeClient(api_key="fake")
            subject, body = client.generate_email_for_contact(sheet_row_new, 1)
            assert subject
            assert body

    @patch("src.claude_client.anthropic.Anthropic")
    def test_generate_email_calls_claude_api(self, mock_anthropic_cls):
        """Non-dry-run: verify we call the API with the right model."""
        with patch("config.DRY_RUN", False):
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client

            mock_message = MagicMock()
            mock_message.content = [MagicMock(text="SUBJECT: Test Subject\n\nHello, this is a test body.")]
            mock_client.messages.create.return_value = mock_message

            claude = ClaudeClient(api_key="real_key")
            subject, body = claude.generate_email(
                email_number=1,
                practice_name="DFW Ortho",
                specialty="Orthopedics",
                city="Plano",
                contact_name="",
            )

            assert subject == "Test Subject"
            assert "Hello" in body
            mock_client.messages.create.assert_called_once()
            call_kwargs = mock_client.messages.create.call_args[1]
            assert call_kwargs["model"] == "claude-haiku-4-5-20251001"

    @patch("src.claude_client.anthropic.Anthropic")
    def test_api_error_falls_back_gracefully(self, mock_anthropic_cls):
        """On API error, should return fallback email without raising."""
        import anthropic
        with patch("config.DRY_RUN", False):
            mock_client = MagicMock()
            mock_anthropic_cls.return_value = mock_client
            mock_client.messages.create.side_effect = anthropic.APIError(
                message="rate limit",
                request=MagicMock(),
                body=None,
            )

            claude = ClaudeClient(api_key="real_key")
            subject, body = claude.generate_email(
                email_number=1,
                practice_name="Test Clinic",
                specialty="Endocrinology",
                city="Dallas",
            )
            assert subject  # fallback should still return something
            assert "Sanjana" in body
