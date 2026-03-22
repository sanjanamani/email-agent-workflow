"""
test_brevo_add.py — Add a test contact to BREVO_CALL_LIST_ID,
verify it was stored, then delete it. Prints PASS or FAIL.
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

TEST_PHONE = "5550000000"
TEST_NAME = "TEST PRACTICE - DELETE ME"


def _fail(msg: str):
    print(f"FAIL — {msg}")
    sys.exit(1)


def test_add_fetch_delete():
    if not BREVO_API_KEY:
        _fail("BREVO_API_KEY not set")
    if not BREVO_CALL_LIST_ID:
        _fail("BREVO_CALL_LIST_ID not set")

    # --- Add ---
    print(f"Adding test contact to list {BREVO_CALL_LIST_ID}…")
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
    resp = requests.post(f"{BASE}/contacts", json=payload, headers=HEADERS, timeout=15)
    if resp.status_code not in (201, 204):
        _fail(f"Add returned {resp.status_code}: {resp.text[:300]}")

    contact_id = resp.json().get("id") if resp.status_code == 201 else None
    print(f"  Added (id={contact_id})")

    # --- Fetch back by phone ---
    print("Fetching contact back by phone number…")
    fetch_resp = requests.get(
        f"{BASE}/contacts/{TEST_PHONE}",
        params={"identifierType": "phone_number"},
        headers=HEADERS,
        timeout=10,
    )
    if fetch_resp.status_code != 200:
        _fail(f"Fetch returned {fetch_resp.status_code}: {fetch_resp.text[:300]}")
    data = fetch_resp.json()
    returned_name = data.get("attributes", {}).get("PRACTICE_NAME", "")
    if returned_name != TEST_NAME:
        _fail(f"Returned PRACTICE_NAME '{returned_name}' does not match '{TEST_NAME}'")
    print(f"  Verified: PRACTICE_NAME = '{returned_name}'")

    # --- Delete ---
    contact_id = contact_id or data.get("id")
    if contact_id:
        print(f"Deleting contact id={contact_id}…")
        del_resp = requests.delete(f"{BASE}/contacts/{contact_id}", headers=HEADERS, timeout=10)
        if del_resp.status_code not in (204, 200):
            print(f"  Warning: delete returned {del_resp.status_code} — clean up manually")
        else:
            print("  Deleted.")

    print("\nPASS")


if __name__ == "__main__":
    test_add_fetch_delete()
