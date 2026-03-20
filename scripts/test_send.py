"""
scripts/test_send.py — End-to-end email pipeline test with real addresses.

Bypasses Maps discovery and Snov lookup entirely. Uses your supplied test
contacts, calls Claude to generate real personalized emails, prints the
full output, then (optionally) sends them via Gmail.

Usage:
    python scripts/test_send.py              # preview only
    python scripts/test_send.py --send       # preview + send
    python scripts/test_send.py --email 2   # test email #2 instead of #1
    python scripts/test_send.py --verify    # SMTP-verify addresses first

Contacts are defined in TEST_CONTACTS below — edit freely.
"""

import argparse
import sys
import os

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from src.claude_client import ClaudeClient
from src.gmail_client import GmailClient
from src.smtp_verify import verify_email, VerifyResult

# ---------------------------------------------------------------------------
# Test contacts — edit these as needed
# ---------------------------------------------------------------------------

TEST_CONTACTS = [
    {
        "Practice Name": "Opus Health",
        "Specialty": "Orthopedics",
        "Address": "123 Main St, Dallas, TX 75201",
        "Phone": "",
        "Website": "https://opushealth.io",
        "Email": "sanjana@opushealth.io",
        "Contact Name": "Sanjana",
        "Title": "",
        "Status": config.STATUS_NEW,
    },
    {
        "Practice Name": "DFW Angels Orthopaedic Center",
        "Specialty": "Orthopedics",
        "Address": "456 Preston Rd, Frisco, TX 75034",
        "Phone": "",
        "Website": "https://dfw-angels.com",
        "Email": "sanjana.m@dfw-angels.com",
        "Contact Name": "Sanjana M.",
        "Title": "Office Manager",
        "Status": config.STATUS_NEW,
    },
    {
        "Practice Name": "North Texas Endocrine Center",
        "Specialty": "Endocrinology",
        "Address": "789 Legacy Dr, Plano, TX 75024",
        "Phone": "",
        "Website": "",
        "Email": "sanjanamanikandan2002@gmail.com",
        "Contact Name": "",
        "Title": "",
        "Status": config.STATUS_NEW,
    },
]

DIVIDER = "=" * 70


def main():
    parser = argparse.ArgumentParser(description="Test the email pipeline end-to-end.")
    parser.add_argument("--send", action="store_true", help="Actually send emails via Gmail")
    parser.add_argument("--email", type=int, default=1, choices=[1, 2, 3],
                        help="Which email in the sequence to generate (default: 1)")
    parser.add_argument("--verify", action="store_true",
                        help="SMTP-verify each address before generating emails")
    args = parser.parse_args()

    # Claude generates real emails regardless of DRY_RUN env var
    # We temporarily override for generation only
    _original_dry_run = config.DRY_RUN
    config.DRY_RUN = False

    claude = ClaudeClient()

    if args.send:
        try:
            gmail = GmailClient()
        except Exception as exc:
            print(f"[ERROR] Could not initialise Gmail: {exc}")
            print("Run with --send only after setting up Gmail credentials.")
            sys.exit(1)
    else:
        gmail = None

    config.DRY_RUN = _original_dry_run  # restore for Gmail (won't matter without --send)

    print(f"\n{DIVIDER}")
    print(f"  Inara AI — End-to-End Test   (Email #{args.email})")
    print(DIVIDER)

    for i, contact in enumerate(TEST_CONTACTS, 1):
        email_addr = contact["Email"]
        practice = contact["Practice Name"]

        print(f"\n[{i}/{len(TEST_CONTACTS)}] {practice} → {email_addr}")

        # Optional SMTP verification
        if args.verify:
            result = verify_email(email_addr)
            status_label = {
                VerifyResult.VALID:     "✓ valid",
                VerifyResult.INVALID:   "✗ invalid (mailbox rejected)",
                VerifyResult.CATCH_ALL: "~ catch-all domain (can't verify individual mailbox)",
                VerifyResult.UNKNOWN:   "? unknown (server blocked check or port 25 filtered)",
            }.get(result, "? unknown")
            print(f"  SMTP verify: {status_label}")
            if result == VerifyResult.INVALID:
                print("  Skipping — mailbox does not exist.")
                continue

        # Generate email via Claude
        print("  Generating via Claude...", end="", flush=True)
        subject, body = claude.generate_email_for_contact(contact, args.email)
        body = _append_signature(body)
        print(" done.")

        # Print full preview
        print(f"\n{'-' * 70}")
        print(f"  TO:      {email_addr}")
        print(f"  SUBJECT: {subject}")
        print(f"{'-' * 70}")
        print(body)
        print(f"{'-' * 70}")

        # Send if requested
        if args.send:
            confirm = input(f"\n  Send this to {email_addr}? [y/N] ").strip().lower()
            if confirm == "y":
                config.DRY_RUN = False
                sent = gmail.send_email(to=email_addr, subject=subject, body=body)
                if sent:
                    print(f"  → Sent to {email_addr}")
                else:
                    print(f"  → Send FAILED for {email_addr}")
            else:
                print("  → Skipped.")

    print(f"\n{DIVIDER}")
    print("  Done.")
    print(DIVIDER)


def _append_signature(body: str) -> str:
    sig = "\n\n-- \nSanjana Manikandan\nUT Dallas | Independent Researcher"
    if "Sanjana Manikandan" in body and "UT Dallas" in body:
        return body
    return body.rstrip() + sig


if __name__ == "__main__":
    main()
