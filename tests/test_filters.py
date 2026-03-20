"""Tests for src/filters.py"""

import pytest

from src.filters import extract_city, filter_practices, is_independent_practice


class TestIsIndependentPractice:
    def test_independent_practice_passes(self, sample_practice):
        assert is_independent_practice(sample_practice) is True

    def test_hospital_system_blocked_by_name(self, sample_hospital_practice):
        assert is_independent_practice(sample_hospital_practice) is False

    def test_baylor_blocked(self):
        practice = {
            "name": "Baylor Scott & White Orthopedics",
            "address": "100 Medical Pkwy, Dallas, TX",
            "website": "https://www.bswhealth.com",
        }
        assert is_independent_practice(practice) is False

    def test_hospital_in_address_blocked(self):
        practice = {
            "name": "Dallas Spine Center",
            "address": "456 Hospital Dr, Dallas, TX 75201",
            "website": "https://dallasspine.com",
        }
        assert is_independent_practice(practice) is False

    def test_hospital_domain_blocked(self):
        practice = {
            "name": "Advanced Endocrinology",
            "address": "789 Oak St, Plano, TX",
            "website": "https://utsouthwestern.edu/clinics/endo",
        }
        assert is_independent_practice(practice) is False

    def test_case_insensitive_name_match(self):
        practice = {
            "name": "METHODIST ORTHOPEDIC SPECIALISTS",
            "address": "123 Main St, Dallas, TX",
            "website": "",
        }
        assert is_independent_practice(practice) is False

    def test_empty_name_and_address_passes(self):
        # Minimal practice with no identifying info — should pass filter
        practice = {"name": "", "address": "", "website": ""}
        assert is_independent_practice(practice) is True

    def test_university_in_name_blocked(self):
        practice = {
            "name": "University Endocrine Associates",
            "address": "100 Campus Dr, Dallas, TX",
            "website": "",
        }
        assert is_independent_practice(practice) is False


class TestFilterPractices:
    def test_filters_list_correctly(self, sample_practice, sample_hospital_practice):
        practices = [sample_practice, sample_hospital_practice]
        result = filter_practices(practices)
        assert len(result) == 1
        assert result[0]["name"] == sample_practice["name"]

    def test_empty_list(self):
        assert filter_practices([]) == []

    def test_all_independent(self, sample_practice):
        practices = [sample_practice, {**sample_practice, "place_id": "abc2", "name": "DFW Thyroid Center"}]
        result = filter_practices(practices)
        assert len(result) == 2

    def test_all_hospital(self, sample_hospital_practice):
        result = filter_practices([sample_hospital_practice])
        assert result == []


class TestExtractCity:
    def test_plano_tx(self):
        assert extract_city("1234 Legacy Dr, Plano, TX 75024, USA") == "Plano"

    def test_dallas_tx(self):
        assert extract_city("5323 Harry Hines Blvd, Dallas, TX 75390, USA") == "Dallas"

    def test_frisco_tx(self):
        assert extract_city("789 Preston Rd, Frisco, TX 75034, USA") == "Frisco"

    def test_no_tx_in_address(self):
        assert extract_city("123 Main St, Austin, CA 90210") == ""

    def test_empty_address(self):
        assert extract_city("") == ""

    def test_mckinney_tx(self):
        assert extract_city("100 Elm St, McKinney, TX 75069, USA") == "McKinney"
