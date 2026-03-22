"""Tests for src/snov_client.py (web-scraper implementation)."""

from unittest.mock import MagicMock, patch

import pytest

from src.snov_client import SnovClient, _normalize_domain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_html(emails: list[str], use_mailto: bool = False) -> str:
    """Build a minimal HTML page containing email addresses."""
    links = "".join(
        f'<a href="mailto:{e}">{e}</a>' if use_mailto else f"<p>{e}</p>"
        for e in emails
    )
    return f"<html><body>{links}</body></html>"


def _mock_get_response(html: str, status: int = 200):
    resp = MagicMock()
    resp.text = html
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    return resp


def _mock_get_error():
    import requests as req
    raise req.RequestException("connection error")


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestSnovClientInit:
    def test_accepts_client_id_and_secret(self):
        client = SnovClient(client_id="id", client_secret="secret")
        assert client.client_id == "id"
        assert client.client_secret == "secret"

    def test_defaults_to_config(self):
        with patch("config.SNOV_CLIENT_ID", "cfg_id"), \
             patch("config.SNOV_CLIENT_SECRET", "cfg_sec"):
            client = SnovClient()
            assert client.client_id == "cfg_id"


# ---------------------------------------------------------------------------
# find_emails_for_domain
# ---------------------------------------------------------------------------

class TestFindEmailsForDomain:
    @patch("src.snov_client.requests.get")
    def test_finds_mailto_links(self, mock_get):
        html = _make_html(["office@clinic.com", "billing@clinic.com"], use_mailto=True)
        mock_get.return_value = _mock_get_response(html)

        client = SnovClient(client_id="id", client_secret="secret")
        contacts = client.find_emails_for_domain("clinic.com")

        emails = {c["email"] for c in contacts}
        assert "office@clinic.com" in emails
        assert "billing@clinic.com" in emails

    @patch("src.snov_client.requests.get")
    def test_finds_plain_text_emails(self, mock_get):
        html = _make_html(["info@example.com"], use_mailto=False)
        mock_get.return_value = _mock_get_response(html)

        client = SnovClient(client_id="id", client_secret="secret")
        contacts = client.find_emails_for_domain("example.com")

        assert any(c["email"] == "info@example.com" for c in contacts)

    @patch("src.snov_client.requests.get")
    def test_returns_empty_on_no_emails(self, mock_get):
        mock_get.return_value = _mock_get_response("<html><body>No email here.</body></html>")

        client = SnovClient(client_id="id", client_secret="secret")
        contacts = client.find_emails_for_domain("noemails.com")
        assert contacts == []

    @patch("src.snov_client.requests.get")
    def test_returns_empty_on_network_error(self, mock_get):
        import requests as req
        mock_get.side_effect = req.RequestException("timeout")

        client = SnovClient(client_id="id", client_secret="secret")
        contacts = client.find_emails_for_domain("errordomain.com")
        assert contacts == []

    def test_returns_empty_for_empty_domain(self):
        client = SnovClient(client_id="id", client_secret="secret")
        contacts = client.find_emails_for_domain("")
        assert contacts == []

    @patch("src.snov_client.requests.get")
    def test_contact_dict_shape(self, mock_get):
        html = _make_html(["contact@clinic.com"], use_mailto=True)
        mock_get.return_value = _mock_get_response(html)

        client = SnovClient(client_id="id", client_secret="secret")
        contacts = client.find_emails_for_domain("clinic.com")

        assert len(contacts) >= 1
        c = next(c for c in contacts if c["email"] == "contact@clinic.com")
        assert "first_name" in c
        assert "last_name" in c
        assert "full_name" in c
        assert "title" in c
        assert "confidence" in c

    @patch("src.snov_client.requests.get")
    def test_deduplicates_emails(self, mock_get):
        # Same email appears on multiple pages → only one contact returned
        html = _make_html(["info@clinic.com"], use_mailto=True)
        mock_get.return_value = _mock_get_response(html)

        client = SnovClient(client_id="id", client_secret="secret")
        contacts = client.find_emails_for_domain("clinic.com")

        emails = [c["email"] for c in contacts]
        assert len(emails) == len(set(emails))


# ---------------------------------------------------------------------------
# best_contact
# ---------------------------------------------------------------------------

class TestBestContact:
    def _client_with_contacts(self, contacts: list[dict]) -> SnovClient:
        client = SnovClient(client_id="id", client_secret="secret")
        client.find_emails_for_domain = MagicMock(return_value=contacts)
        return client

    def test_prefers_personal_over_generic(self):
        contacts = [
            {"email": "info@clinic.com", "first_name": "", "last_name": "",
             "full_name": "", "title": "", "confidence": "medium"},
            {"email": "jdoe@clinic.com", "first_name": "Jane", "last_name": "Doe",
             "full_name": "Jane Doe", "title": "", "confidence": "medium"},
        ]
        client = self._client_with_contacts(contacts)
        best = client.best_contact("clinic.com")
        assert best is not None
        assert best["email"] == "jdoe@clinic.com"

    def test_prefers_target_title_over_generic(self):
        contacts = [
            {"email": "info@clinic.com", "first_name": "", "last_name": "",
             "full_name": "", "title": "", "confidence": "medium"},
            {"email": "jennifer@clinic.com", "first_name": "Jennifer", "last_name": "Smith",
             "full_name": "Jennifer Smith", "title": "Office Manager", "confidence": "medium"},
        ]
        client = self._client_with_contacts(contacts)
        best = client.best_contact("clinic.com")
        assert best["email"] == "jennifer@clinic.com"

    def test_prefers_higher_confidence_among_same_tier(self):
        contacts = [
            {"email": "asmith@clinic.com", "first_name": "A", "last_name": "Smith",
             "full_name": "A Smith", "title": "", "confidence": "low"},
            {"email": "bjones@clinic.com", "first_name": "B", "last_name": "Jones",
             "full_name": "B Jones", "title": "", "confidence": "high"},
        ]
        client = self._client_with_contacts(contacts)
        best = client.best_contact("clinic.com")
        assert best["email"] == "bjones@clinic.com"

    def test_falls_back_to_generic_when_only_option(self):
        contacts = [
            {"email": "info@clinic.com", "first_name": "", "last_name": "",
             "full_name": "", "title": "", "confidence": "medium"},
        ]
        client = self._client_with_contacts(contacts)
        best = client.best_contact("clinic.com")
        assert best is not None
        assert best["email"] == "info@clinic.com"

    def test_returns_none_when_no_emails(self):
        client = self._client_with_contacts([])
        best = client.best_contact("emptydomain.com")
        assert best is None


# ---------------------------------------------------------------------------
# _normalize_domain
# ---------------------------------------------------------------------------

class TestNormalizeDomain:
    def test_full_url(self):
        assert _normalize_domain("https://www.northtexasortho.com/about") == "northtexasortho.com"

    def test_www_prefix_stripped(self):
        assert _normalize_domain("www.northtexasortho.com") == "northtexasortho.com"

    def test_bare_domain(self):
        assert _normalize_domain("northtexasortho.com") == "northtexasortho.com"

    def test_http_url(self):
        assert _normalize_domain("http://clinic.com/page") == "clinic.com"

    def test_empty_string(self):
        assert _normalize_domain("") == ""
