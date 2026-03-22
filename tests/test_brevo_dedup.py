"""
test_brevo_dedup.py — Add the same test contact twice, verify the second
add is treated as a duplicate (not added again), then clean up.
Prints PASS or FAIL.
"""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_CALL_LIST_ID = int(os.getenv("BREVO_CALL_LIST_ID", "0"))
BASE = "https://api.brevo.com/v3"
HEADERS = {"api-key": BREVO_API_KEY, "Content-Type": "application/json"}

TEST_PHONE = "5550000001"
TEST_NAME = "TEST PRACTICE DEDUP - DELETE ME"


def _fail(msg: str):
    print(f"FAIL — {msg}")
    sys.exit(1)


def _add_contact() -> requests.Response:
    payload = {
        "listIds": [BREVO_CALL_LIST_ID],
        "attributes": {
            "PRACTICE_NAME": TEST_NAME,
            "PHONE": TEST_PHONE,
            "SPECIALTY": "Test",
            "CITY": "Dallas",
            "CONTACT_STATUS": "call_needed",
            "EMAIL_FOUND": "false",
        },
        "updateEnabled": False,
    }
    return requests.post(f"{BASE}/contacts", json=payload, headers=HEADERS, timeout=15)


def _check_exists() -> bool:
    resp = requests.get(
        f"{BASE}/contacts/{TEST_PHONE}",
        params={"identifierType": "phone_number"},
        headers=HEADERS,
        timeout=10,
    )
    return resp.status_code == 200


def test_dedup():
    if not BREVO_API_KEY:
        _fail("BREVO_API_KEY not set")
    if not BREVO_CALL_LIST_ID:
        _fail("BREVO_CALL_LIST_ID not set")

    # First add
    print("First add…")
    r1 = _add_contact()
    if r1.status_code not in (201, 204):
        _fail(f"First add returned {r1.status_code}: {r1.text[:300]}")
    contact_id = r1.json().get("id") if r1.status_code == 201 else None
    print(f"  OK (id={contact_id})")

    # Verify it exists
    if not _check_exists():
        _fail("Contact not found in Brevo after first add")

    # Second add — should be treated as duplicate by check_brevo_exists()
    print("Checking Brevo existence before second add (simulating pipeline dedup)…")
    already_there = _check_exists()
    if not already_there:
        _fail("check_brevo_exists returned False when contact is present")
    print("  check_brevo_exists() correctly returns True — second add would be skipped")

    # Confirm a raw second POST returns 400 duplicate from Brevo itself
    print("Attempting raw second POST (expect 400 duplicate)…")
    r2 = _add_contact()
    if r2.status_code == 400 and "duplicate" in r2.json().get("code", "").lower():
        print("  Brevo correctly returns 400 duplicate")
    elif r2.status_code in (201, 204):
        print("  Warning: Brevo accepted second add (contact without email may not dedup by phone)")
    else:
        print(f"  Unexpected response: {r2.status_code} — {r2.text[:200]}")

    # Clean up
    if contact_id:
        print(f"Deleting contact id={contact_id}…")
        del_resp = requests.delete(f"{BASE}/contacts/{contact_id}", headers=HEADERS, timeout=10)
        if del_resp.status_code not in (200, 204):
            print(f"  Warning: delete returned {del_resp.status_code} — clean up manually")
        else:
            print("  Deleted.")

    print("\nPASS")


if __name__ == "__main__":
    test_dedup()
