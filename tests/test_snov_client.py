"""Tests for src/snov_client.py"""

from unittest.mock import MagicMock, patch

import pytest

from src.snov_client import SnovClient, _normalize_domain


# ---------------------------------------------------------------------------
# Mock responses
# ---------------------------------------------------------------------------

MOCK_TOKEN_RESPONSE = {"access_token": "test_token_abc"}

MOCK_START_RESPONSE = {
    "meta": {"task_hash": "abc123"},
    "links": {"result": "https://api.snov.io/v2/domain-search/domain-emails/result/abc123"},
}

MOCK_RESULT_RESPONSE = {
    "data": [
        {
            "email": "jennifer.smith@northtexasortho.com",
            "firstName": "Jennifer",
            "lastName": "Smith",
            "confidence": "high",
            "currentJob": [{"title": "Office Manager"}],
        },
        {
            "email": "admin@northtexasortho.com",
            "firstName": "",
            "lastName": "",
            "confidence": "medium",
            "currentJob": [],
        },
    ]
}

MOCK_EMPTY_RESULT_RESPONSE = {"data": []}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSnovClientAuth:
    @patch("src.snov_client.requests.post")
    def test_fetches_access_token(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_TOKEN_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        client = SnovClient(client_id="id", client_secret="secret")
        token = client._get_access_token()
        assert token == "test_token_abc"

    @patch("src.snov_client.requests.post")
    def test_returns_empty_string_on_auth_error(self, mock_post):
        import requests as req
        mock_post.side_effect = req.RequestException("connection error")

        client = SnovClient(client_id="id", client_secret="secret")
        token = client._get_access_token()
        assert token == ""

    @patch("src.snov_client.requests.post")
    def test_caches_token(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_TOKEN_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        client = SnovClient(client_id="id", client_secret="secret")
        _ = client._token()
        _ = client._token()
        # Auth should only be called once
        assert mock_post.call_count == 1


class TestFindEmailsForDomain:
    @patch("src.snov_client.requests.get")
    @patch("src.snov_client.requests.post")
    def test_returns_contacts_on_success(self, mock_post, mock_get):
        # First POST = auth, second POST = start search
        auth_resp = MagicMock()
        auth_resp.json.return_value = MOCK_TOKEN_RESPONSE
        auth_resp.raise_for_status = MagicMock()

        start_resp = MagicMock()
        start_resp.json.return_value = MOCK_START_RESPONSE
        start_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [auth_resp, start_resp]

        result_resp = MagicMock()
        result_resp.json.return_value = MOCK_RESULT_RESPONSE
        result_resp.raise_for_status = MagicMock()
        mock_get.return_value = result_resp

        client = SnovClient(client_id="id", client_secret="secret")
        contacts = client.find_emails_for_domain("northtexasortho.com")

        assert len(contacts) == 2
        assert contacts[0]["email"] == "jennifer.smith@northtexasortho.com"
        assert contacts[0]["title"] == "Office Manager"
        assert contacts[0]["full_name"] == "Jennifer Smith"

    @patch("src.snov_client.requests.get")
    @patch("src.snov_client.requests.post")
    def test_returns_empty_on_no_emails(self, mock_post, mock_get):
        auth_resp = MagicMock()
        auth_resp.json.return_value = MOCK_TOKEN_RESPONSE
        auth_resp.raise_for_status = MagicMock()

        start_resp = MagicMock()
        start_resp.json.return_value = MOCK_START_RESPONSE
        start_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [auth_resp, start_resp]

        result_resp = MagicMock()
        result_resp.json.return_value = MOCK_EMPTY_RESULT_RESPONSE
        result_resp.raise_for_status = MagicMock()
        mock_get.return_value = result_resp

        client = SnovClient(client_id="id", client_secret="secret")
        contacts = client.find_emails_for_domain("emptydomain.com")
        assert contacts == []

    @patch("src.snov_client.requests.get")
    @patch("src.snov_client.requests.post")
    def test_returns_empty_on_network_error(self, mock_post, mock_get):
        import requests as req
        auth_resp = MagicMock()
        auth_resp.json.return_value = MOCK_TOKEN_RESPONSE
        auth_resp.raise_for_status = MagicMock()

        start_resp = MagicMock()
        start_resp.raise_for_status = MagicMock()
        start_resp.json.side_effect = req.RequestException("timeout")

        mock_post.side_effect = [auth_resp, start_resp]
        mock_get.side_effect = req.RequestException("timeout")

        client = SnovClient(client_id="id", client_secret="secret")
        contacts = client.find_emails_for_domain("errordomain.com")
        assert contacts == []

    def test_returns_empty_for_empty_domain(self):
        client = SnovClient(client_id="id", client_secret="secret")
        contacts = client.find_emails_for_domain("")
        assert contacts == []


class TestBestContact:
    @patch("src.snov_client.requests.get")
    @patch("src.snov_client.requests.post")
    def test_prefers_target_title(self, mock_post, mock_get):
        auth_resp = MagicMock()
        auth_resp.json.return_value = MOCK_TOKEN_RESPONSE
        auth_resp.raise_for_status = MagicMock()

        start_resp = MagicMock()
        start_resp.json.return_value = MOCK_START_RESPONSE
        start_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [auth_resp, start_resp]

        result_resp = MagicMock()
        result_resp.json.return_value = MOCK_RESULT_RESPONSE
        result_resp.raise_for_status = MagicMock()
        mock_get.return_value = result_resp

        client = SnovClient(client_id="id", client_secret="secret")
        best = client.best_contact("northtexasortho.com")
        assert best is not None
        assert best["email"] == "jennifer.smith@northtexasortho.com"

    @patch("src.snov_client.requests.get")
    @patch("src.snov_client.requests.post")
    def test_falls_back_to_first_contact(self, mock_post, mock_get):
        auth_resp = MagicMock()
        auth_resp.json.return_value = MOCK_TOKEN_RESPONSE
        auth_resp.raise_for_status = MagicMock()

        start_resp = MagicMock()
        start_resp.json.return_value = MOCK_START_RESPONSE
        start_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [auth_resp, start_resp]

        no_title_response = {
            "data": [
                {"email": "info@clinic.com", "firstName": "Info", "lastName": "", "currentJob": [], "confidence": "low"},
            ]
        }
        result_resp = MagicMock()
        result_resp.json.return_value = no_title_response
        result_resp.raise_for_status = MagicMock()
        mock_get.return_value = result_resp

        client = SnovClient(client_id="id", client_secret="secret")
        best = client.best_contact("clinic.com")
        assert best is not None
        assert best["email"] == "info@clinic.com"

    @patch("src.snov_client.requests.get")
    @patch("src.snov_client.requests.post")
    def test_returns_none_when_no_emails(self, mock_post, mock_get):
        auth_resp = MagicMock()
        auth_resp.json.return_value = MOCK_TOKEN_RESPONSE
        auth_resp.raise_for_status = MagicMock()

        start_resp = MagicMock()
        start_resp.json.return_value = MOCK_START_RESPONSE
        start_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [auth_resp, start_resp]

        result_resp = MagicMock()
        result_resp.json.return_value = MOCK_EMPTY_RESULT_RESPONSE
        result_resp.raise_for_status = MagicMock()
        mock_get.return_value = result_resp

        client = SnovClient(client_id="id", client_secret="secret")
        best = client.best_contact("emptydomain.com")
        assert best is None


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
