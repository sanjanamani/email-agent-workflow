"""
test_end_to_end.py — Full pipeline smoke test.

Runs with DRY_RUN=false and MAX_PRACTICES=3.
Verifies at least 1 practice ends up in either BREVO_LIST_ID or
BREVO_CALL_LIST_ID, then prints a summary.

WARNING: This test makes real API calls to NPI, Claude, and Brevo.
It will add real contacts to your Brevo lists.
"""

import os
import sys

# Force small run
os.environ.setdefault("MAX_PRACTICES", "3")
os.environ.setdefault("DRY_RUN", "false")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import requests
from find_and_enrich import (
    BREVO_API_KEY,
    BREVO_LIST_ID,
    BREVO_CALL_LIST_ID,
    normalize_phone,
    check_brevo_exists,
    run,
)


def _fail(msg: str):
    print(f"FAIL — {msg}")
    sys.exit(1)


def test_end_to_end():
    if not BREVO_API_KEY:
        _fail("BREVO_API_KEY not set")
    if not BREVO_LIST_ID:
        _fail("BREVO_LIST_ID not set")

    print("Running full pipeline with MAX_PRACTICES=3…")
    print("(This will make real API calls and add contacts to Brevo)\n")

    # Snapshot list sizes before
    headers = {"api-key": BREVO_API_KEY}
    def list_count(list_id: int) -> int:
        if not list_id:
            return 0
        resp = requests.get(
            f"https://api.brevo.com/v3/contacts/lists/{list_id}",
            headers=headers, timeout=10,
        )
        return resp.json().get("totalSubscribers", 0) if resp.status_code == 200 else 0

    before_email = list_count(BREVO_LIST_ID)
    before_call = list_count(BREVO_CALL_LIST_ID)
    print(f"Before: email list={before_email}, call list={before_call}")

    run()

    after_email = list_count(BREVO_LIST_ID)
    after_call = list_count(BREVO_CALL_LIST_ID)
    print(f"\nAfter:  email list={after_email}, call list={after_call}")

    added_email = after_email - before_email
    added_call = after_call - before_call
    total_added = added_email + added_call

    print(f"Net added: {added_email} to email list, {added_call} to call list")

    if total_added < 1:
        _fail(f"Expected at least 1 contact added to Brevo, got {total_added}")

    print("\nPASS")


if __name__ == "__main__":
    test_end_to_end()
