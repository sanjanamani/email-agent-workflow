"""
test_npi.py — Smoke test for NPI registry fetch.

Fetches "Endocrinology, Diabetes & Metabolism" providers in Dallas TX,
prints the first 5 results, and asserts at least 1 result was returned.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from npi_client import fetch_npi_practices

def test_npi_endocrinology_dallas():
    print("Fetching NPI results for endocrinologist / Dallas TX…")
    results = fetch_npi_practices("endocrinologist", ["Dallas"])

    print(f"\nTotal results returned: {len(results)}")

    if not results:
        print("FAIL — 0 results returned")
        sys.exit(1)

    print("\nFirst 5 results:")
    for r in results[:5]:
        print(f"  {r['name']} | {r['phone']} | {r['city']} | {r['address']}")

    print("\nPASS")

if __name__ == "__main__":
    test_npi_endocrinology_dallas()
