"""Tests for src/maps_client.py"""

from unittest.mock import MagicMock, patch

import pytest

from src.maps_client import (
    _infer_specialty,
    _parse_place,
    enrich_with_details,
    search_all_queries,
    search_practices,
)


# ---------------------------------------------------------------------------
# Mock API responses — SerpAPI Google Maps format
# ---------------------------------------------------------------------------

MOCK_SEARCH_RESPONSE = {
    "local_results": [
        {
            "place_id": "ChIJ_abc123",
            "title": "North Texas Orthopedic Center",
            "address": "1234 Legacy Dr, Plano, TX 75024, USA",
            "phone": "(972) 555-0100",
            "website": "https://www.northtexasortho.com",
            "type": "Orthopedist",
            "rating": 4.5,
        }
    ],
}

MOCK_ZERO_RESULTS = {"local_results": []}
MOCK_ERROR_RESPONSE = {"error": "Invalid API key. Your API key is incorrect."}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSearchPractices:
    @patch("src.maps_client.requests.get")
    def test_returns_practices_on_ok(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_SEARCH_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = search_practices("orthopedic clinic Plano TX", api_key="test_key")
        assert len(results) == 1
        assert results[0]["name"] == "North Texas Orthopedic Center"
        assert results[0]["place_id"] == "ChIJ_abc123"

    @patch("src.maps_client.requests.get")
    def test_returns_empty_on_zero_results(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_ZERO_RESULTS
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = search_practices("nonexistent clinic", api_key="test_key")
        assert results == []

    @patch("src.maps_client.requests.get")
    def test_logs_and_returns_empty_on_api_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = MOCK_ERROR_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        results = search_practices("any query", api_key="bad_key")
        assert results == []

    @patch("src.maps_client.requests.get")
    def test_handles_network_error_gracefully(self, mock_get):
        import requests as req
        mock_get.side_effect = req.RequestException("timeout")

        results = search_practices("any query", api_key="test_key")
        assert results == []


class TestEnrichWithDetails:
    def test_returns_practice_unchanged(self, sample_practice):
        # SerpAPI already returns phone/website in the search results,
        # so enrich_with_details is a no-op.
        result = enrich_with_details(sample_practice, api_key="test_key")
        assert result == sample_practice

    def test_returns_unchanged_when_no_place_id(self, sample_practice):
        practice = {**sample_practice, "place_id": ""}
        result = enrich_with_details(practice, api_key="test_key")
        assert result == practice


class TestSearchAllQueries:
    @patch("src.maps_client.search_practices")
    def test_deduplicates_by_place_id(self, mock_search):
        # Both queries return the same place_id
        practice = {
            "place_id": "ChIJ_abc123",
            "name": "DFW Ortho",
            "address": "Plano, TX",
            "phone": "",
            "website": "",
            "specialty": "Orthopedics",
        }
        mock_search.return_value = [practice]

        results = search_all_queries(api_key="test_key")
        # Should only appear once despite multiple queries
        place_ids = [r["place_id"] for r in results]
        assert place_ids.count("ChIJ_abc123") == 1

    @patch("src.maps_client.search_practices")
    def test_continues_on_single_query_error(self, mock_search):
        mock_search.side_effect = Exception("API down")
        results = search_all_queries(api_key="test_key")
        assert results == []


class TestParsePlaceAndInferSpecialty:
    def test_parse_place_returns_expected_fields(self):
        raw = {
            "place_id": "abc",
            "title": "Endocrine Associates of Dallas",
            "address": "100 Main St, Dallas, TX",
            "phone": "(214) 555-0100",
            "website": "https://endoassoc.com",
            "type": "Endocrinologist",
            "rating": 4.0,
        }
        parsed = _parse_place(raw)
        assert parsed["place_id"] == "abc"
        assert parsed["name"] == "Endocrine Associates of Dallas"
        assert parsed["specialty"] == "Endocrinology"
        assert parsed["phone"] == "(214) 555-0100"
        assert parsed["website"] == "https://endoassoc.com"

    def test_infer_specialty_endocrinology(self):
        assert _infer_specialty("Thyroid & Hormone Center", []) == "Endocrinology"
        assert _infer_specialty("Diabetes Clinic of Plano", []) == "Endocrinology"

    def test_infer_specialty_orthopedics(self):
        assert _infer_specialty("DFW Spine and Joint Center", []) == "Orthopedics"
        assert _infer_specialty("Texas Orthopedic Associates", []) == "Orthopedics"

    def test_infer_specialty_general_fallback(self):
        assert _infer_specialty("Premier Medical Group", []) == "General"
