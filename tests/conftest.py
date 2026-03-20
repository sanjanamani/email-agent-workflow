"""
conftest.py — Shared fixtures and mock data for the test suite.
"""

import pytest


# ---------------------------------------------------------------------------
# Sample practice dicts (as returned by Maps API / _parse_place)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_practice():
    return {
        "place_id": "ChIJ_abc123",
        "name": "North Texas Orthopedic Center",
        "address": "1234 Legacy Dr, Plano, TX 75024, USA",
        "phone": "(972) 555-0100",
        "website": "https://www.northtexasortho.com",
        "types": ["doctor", "health"],
        "specialty": "Orthopedics",
        "rating": 4.5,
    }


@pytest.fixture
def sample_hospital_practice():
    return {
        "place_id": "ChIJ_xyz999",
        "name": "UT Southwestern Endocrinology Clinic",
        "address": "5323 Harry Hines Blvd, Dallas, TX 75390, USA",
        "phone": "(214) 555-9999",
        "website": "https://www.utsouthwestern.edu/clinics/endocrinology",
        "types": ["hospital", "health"],
        "specialty": "Endocrinology",
        "rating": 4.2,
    }


@pytest.fixture
def sample_contact():
    return {
        "email": "jennifer.smith@northtexasortho.com",
        "first_name": "Jennifer",
        "last_name": "Smith",
        "full_name": "Jennifer Smith",
        "title": "Office Manager",
        "confidence": "high",
    }


# ---------------------------------------------------------------------------
# Sample sheet rows
# ---------------------------------------------------------------------------

@pytest.fixture
def sheet_row_new(sample_practice, sample_contact):
    """A brand-new contact row (Status=New, no emails sent)."""
    return {
        "Practice Name": sample_practice["name"],
        "Specialty": sample_practice["specialty"],
        "Address": sample_practice["address"],
        "Phone": sample_practice["phone"],
        "Website": sample_practice["website"],
        "Email": sample_contact["email"],
        "Contact Name": sample_contact["full_name"],
        "Title": sample_contact["title"],
        "Email Sent Date": "",
        "Email 2 Sent Date": "",
        "Email 3 Sent Date": "",
        "Reply Received": "",
        "Reply Date": "",
        "Status": "New",
        "Notes": "",
        "_row_index": 2,
    }


@pytest.fixture
def sheet_row_email1_sent(sheet_row_new):
    """Row after email 1 has been sent (6 days ago)."""
    from datetime import date, timedelta
    row = dict(sheet_row_new)
    row["Email Sent Date"] = (date.today() - timedelta(days=6)).isoformat()
    row["Status"] = "Email 1 Sent"
    return row


@pytest.fixture
def sheet_row_email2_sent(sheet_row_email1_sent):
    """Row after email 2 has been sent (6 days ago)."""
    from datetime import date, timedelta
    row = dict(sheet_row_email1_sent)
    row["Email 2 Sent Date"] = (date.today() - timedelta(days=6)).isoformat()
    row["Status"] = "Email 2 Sent"
    return row


@pytest.fixture
def sheet_row_email3_sent(sheet_row_email2_sent):
    """Row after email 3 has been sent (6 days ago)."""
    from datetime import date, timedelta
    row = dict(sheet_row_email2_sent)
    row["Email 3 Sent Date"] = (date.today() - timedelta(days=6)).isoformat()
    row["Status"] = "Email 3 Sent"
    return row


@pytest.fixture
def sheet_row_replied(sheet_row_email1_sent):
    """Row where the contact has replied."""
    row = dict(sheet_row_email1_sent)
    row["Reply Received"] = "Y"
    row["Reply Date"] = "2024-02-01"
    row["Status"] = "Replied"
    return row
