"""
agent.py — Agentic medical practice outreach using Claude's tool use API.

The agent receives a plain-English goal, then autonomously decides which
tools to call and in what order to find practices, check the CRM for
duplicates, scrape emails, and route each contact to the right Brevo list.

Usage:
    python agent.py "Find 20 endocrinologists in Houston TX"
    DRY_RUN=true python agent.py "Find orthopedic surgeons in Austin TX"
    python agent.py "Find endocrinologists and orthopedic surgeons in Dallas"

ENV VARS (same as find_and_enrich.py):
    ANTHROPIC_API_KEY    required
    BREVO_API_KEY        required unless DRY_RUN=true
    BREVO_LIST_ID        email-list ID (default: 7)
    BREVO_CALL_LIST_ID   call-list ID
    SERPER_API_KEY       optional enrichment
    DRY_RUN              "true" → log actions, skip CRM writes
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

import anthropic
from dotenv import load_dotenv

# Reuse all existing business logic — no duplication
from claude_finder import CITIES as DEFAULT_CITIES
from find_and_enrich import (
    add_to_brevo_call_list,
    add_to_brevo_email_list,
    check_brevo_exists,
    check_brevo_exists_by_email,
    scrape_all_emails,
)
from npi_client import TAXONOMY_DESCRIPTIONS, fetch_npi_practices
from serper_enricher import enrich_practice as _serper_enrich

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")
AGENT_MODEL = "claude-opus-4-6"
_MAX_RETRIES = 3


def _retry_after_secs(exc: anthropic.RateLimitError, buffer: int = 5) -> int:
    """Read retry-after seconds from a 429 response header (+ buffer)."""
    try:
        val = exc.response.headers.get("retry-after", "")  # type: ignore[attr-defined]
        if val:
            return int(val) + buffer
    except Exception:
        pass
    return 60 + buffer

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "search_npi",
        "description": (
            "Search the CMS NPI Registry for independent medical practices in Texas "
            "by specialty. Returns practices with name, phone, address, city, and specialty. "
            f"Supported specialties: {list(TAXONOMY_DESCRIPTIONS.keys())}. "
            "Always try this first — it is free and authoritative."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "specialty": {
                    "type": "string",
                    "description": "Medical specialty to search (e.g. 'endocrinologist')",
                },
                "cities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Texas city names to search. "
                        f"Omit to use the default DFW list: {DEFAULT_CITIES}."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max practices to return (default 50, max 200).",
                },
            },
            "required": ["specialty"],
        },
    },
    {
        "name": "enrich_practice",
        "description": (
            "Use Google Search (via Serper) to fill missing practice data: "
            "phone, website, address. "
            "Only call this when a practice is missing website OR phone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name":      {"type": "string", "description": "Practice or doctor name"},
                "city":      {"type": "string"},
                "specialty": {"type": "string"},
                "phone":     {"type": "string", "description": "Current value (may be empty)"},
                "website":   {"type": "string", "description": "Current value (may be empty)"},
                "address":   {"type": "string", "description": "Current value (may be empty)"},
            },
            "required": ["name", "specialty"],
        },
    },
    {
        "name": "scrape_website",
        "description": (
            "Scrape a practice website for email addresses. "
            "Checks the homepage, /contact, /about, /staff, and other common pages. "
            "Returns every email address found. Only call this when there is a website URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full website URL (include https://)"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "check_crm_exists",
        "description": (
            "Check whether a contact is already in the CRM (Brevo) "
            "by phone number and/or email address. "
            "Always call this before adding any contact to avoid duplicates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Phone number to check (optional)"},
                "email": {"type": "string", "description": "Email address to check (optional)"},
            },
        },
    },
    {
        "name": "add_to_email_list",
        "description": (
            "Add a practice with a verified, direct email address to the Brevo "
            "email campaign list. "
            "Reject noreply, no-reply, info@, admin@, support@, webmaster@, "
            "privacy@, press@, media@, and patient-portal addresses. "
            "Use confidence='high' for direct staff/billing emails, 'medium' otherwise."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email":         {"type": "string"},
                "confidence":    {"type": "string", "enum": ["high", "medium"]},
                "first_name":    {"type": "string", "description": "Doctor first name (NPI-1 only)"},
                "last_name":     {"type": "string", "description": "Doctor last name (NPI-1 only)"},
                "practice_name": {"type": "string"},
                "phone":         {"type": "string"},
                "website":       {"type": "string"},
                "address":       {"type": "string"},
                "specialty":     {"type": "string"},
                "city":          {"type": "string"},
            },
            "required": ["email", "confidence", "practice_name", "specialty"],
        },
    },
    {
        "name": "add_to_call_list",
        "description": (
            "Add a practice with no valid email to the Brevo call list for manual outreach. "
            "Use when: no email found, only generic/noreply emails, or email confidence is low. "
            "Must include a clear reason."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason":        {"type": "string", "description": "Why a call is needed"},
                "first_name":    {"type": "string", "description": "Doctor first name (NPI-1 only)"},
                "last_name":     {"type": "string", "description": "Doctor last name (NPI-1 only)"},
                "practice_name": {"type": "string"},
                "phone":         {"type": "string"},
                "website":       {"type": "string"},
                "address":       {"type": "string"},
                "specialty":     {"type": "string"},
                "city":          {"type": "string"},
            },
            "required": ["reason", "practice_name", "specialty"],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool implementations (bridge agent inputs → existing business logic)
# ---------------------------------------------------------------------------

def _tool_search_npi(inp: dict) -> str:
    specialty = inp["specialty"]
    cities = inp.get("cities") or DEFAULT_CITIES
    limit = min(int(inp.get("limit", 50)), 200)
    practices = fetch_npi_practices(specialty, cities)[:limit]
    # Strip internal fields (prefixed _) before giving to the agent
    clean = [{k: v for k, v in p.items() if not k.startswith("_")} for p in practices]
    return json.dumps({"count": len(clean), "practices": clean})


def _tool_enrich_practice(inp: dict) -> str:
    practice = {
        "name":     inp.get("name", ""),
        "city":     inp.get("city", ""),
        "specialty": inp.get("specialty", ""),
        "phone":    inp.get("phone", ""),
        "website":  inp.get("website", ""),
        "address":  inp.get("address", ""),
    }
    enriched = _serper_enrich(practice)
    # Return public fields only
    return json.dumps({k: v for k, v in enriched.items() if not k.startswith("_")})


def _tool_scrape_website(inp: dict) -> str:
    from npi_client import is_aggregator_website, is_chain_website
    url = inp["url"]
    if is_chain_website(url):
        return json.dumps({
            "emails_found": [], "count": 0,
            "skipped": "chain/health-system domain — practice is not independent",
        })
    if is_aggregator_website(url):
        return json.dumps({
            "emails_found": [], "count": 0,
            "skipped": "aggregator/directory domain — not the practice's own website",
        })
    emails = scrape_all_emails(url)
    return json.dumps({"emails_found": emails, "count": len(emails)})


def _tool_check_crm_exists(inp: dict) -> str:
    phone = inp.get("phone", "")
    email = inp.get("email", "")
    by_phone = check_brevo_exists(phone) if phone else False
    by_email = check_brevo_exists_by_email(email) if email else False
    return json.dumps({
        "exists_by_phone": by_phone,
        "exists_by_email": by_email,
        "exists": by_phone or by_email,
    })


def _practice_from_input(inp: dict) -> dict:
    """Build the practice dict format that find_and_enrich.py expects."""
    return {
        "name":         inp.get("practice_name", ""),
        "phone":        inp.get("phone", ""),
        "website":      inp.get("website", ""),
        "address":      inp.get("address", ""),
        "city":         inp.get("city", ""),
        "specialty":    inp.get("specialty", ""),
        "doctor_first": inp.get("first_name", ""),
        "doctor_last":  inp.get("last_name", ""),
    }


def _tool_add_to_email_list(inp: dict) -> str:
    practice = _practice_from_input(inp)
    result = add_to_brevo_email_list(practice, inp["email"], inp["confidence"])
    return json.dumps({"result": result})


def _tool_add_to_call_list(inp: dict) -> str:
    practice = _practice_from_input(inp)
    result = add_to_brevo_call_list(practice, inp["reason"])
    return json.dumps({"result": result})


_TOOL_HANDLERS: dict[str, object] = {
    "search_npi":        _tool_search_npi,
    "enrich_practice":   _tool_enrich_practice,
    "scrape_website":    _tool_scrape_website,
    "check_crm_exists":  _tool_check_crm_exists,
    "add_to_email_list": _tool_add_to_email_list,
    "add_to_call_list":  _tool_add_to_call_list,
}


def _execute_tool(name: str, tool_input: dict) -> str:
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        return handler(tool_input)  # type: ignore[operator]
    except Exception as exc:
        log.error("Tool %s raised: %s", name, exc, exc_info=True)
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = f"""You are a medical practice outreach agent for a Texas-based healthcare company.

Your job is to find independent specialty practices, enrich their contact information,
check for existing CRM entries, and route each practice to the right Brevo list.

## Workflow for each practice:
1. **check_crm_exists** (phone and/or email) — skip the practice entirely if it exists
2. If missing phone or website → **enrich_practice** (Serper/Google)
3. If website is available → **scrape_website** to find email candidates
4. Evaluate the emails yourself — reject: noreply, no-reply, info@, admin@,
   support@, webmaster@, privacy@, press@, media@, patient-portal addresses.
   Also run **check_crm_exists** for any promising email before adding.
5. Good email found → **add_to_email_list** (confidence: "high" for direct
   staff/billing email, "medium" for generic but non-junk office email)
6. No good email → **add_to_call_list** with a clear reason

## Rules:
- Process ALL practices returned by search_npi before finishing
- For NPI-1 (individual doctor): populate first_name and last_name
- For NPI-2 (organization): leave first_name and last_name empty
- Never add the same practice twice (always check CRM first)
- Only use confidence="high" or "medium" — never "low" — for the email list

DRY_RUN: {"ENABLED — actions are logged but nothing is written to Brevo" if DRY_RUN else "DISABLED — writes are live"}

Finish with a brief summary: how many added to email list, call list, and skipped.
"""

# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

def run_agent(goal: str) -> None:
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY is not set")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages: list[dict] = [{"role": "user", "content": goal}]

    print(f"\n{'=' * 60}")
    print(f"GOAL   : {goal}")
    print(f"MODEL  : {AGENT_MODEL}")
    print(f"DRY_RUN: {DRY_RUN}")
    print(f"{'=' * 60}\n")

    turn = 0
    while True:
        turn += 1
        log.info("--- Agent turn %d ---", turn)

        # Stream the response so text appears in real time.
        # Retry on 429 (rate limit, use retry-after header) and 529 (overloaded,
        # exponential backoff 5 → 10 → 20 → 40 s).
        response = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with client.messages.stream(
                    model=AGENT_MODEL,
                    max_tokens=16000,
                    thinking={"type": "adaptive"},
                    system=_SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=messages,
                ) as stream:
                    for event in stream:
                        if (
                            hasattr(event, "type")
                            and event.type == "content_block_delta"
                            and hasattr(event, "delta")
                            and getattr(event.delta, "type", "") == "text_delta"
                        ):
                            print(event.delta.text, end="", flush=True)
                    response = stream.get_final_message()
                break  # success — exit retry loop

            except anthropic.RateLimitError as exc:
                if attempt == _MAX_RETRIES:
                    raise
                wait = _retry_after_secs(exc)
                log.warning("Agent 429 — sleeping %ds (attempt %d/%d)…", wait, attempt + 1, _MAX_RETRIES + 1)
                time.sleep(wait)

            except anthropic.APIStatusError as exc:
                if exc.status_code != 529 or attempt == _MAX_RETRIES:
                    raise
                delay = min(5 * (2 ** attempt), 60)  # 5 → 10 → 20 → 40 s
                log.warning("Agent 529 overloaded — sleeping %ds (attempt %d/%d)…", delay, attempt + 1, _MAX_RETRIES + 1)
                time.sleep(delay)

        # Append the full content block list (preserves tool_use + thinking blocks)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print("\n")
            log.info("Agent finished in %d turn(s)", turn)
            break

        if response.stop_reason != "tool_use":
            log.warning("Unexpected stop_reason=%r — stopping", response.stop_reason)
            break

        # Execute every tool call the agent requested, collect results
        tool_results: list[dict] = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            input_preview = json.dumps(block.input)[:120]
            print(f"\n[TOOL] {block.name}({input_preview})")

            result_str = _execute_tool(block.name, block.input)

            preview = result_str[:200] + ("…" if len(result_str) > 200 else "")
            print(f"       → {preview}")

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent.py \"<goal>\"")
        print()
        print("Examples:")
        print('  python agent.py "Find 20 endocrinologists in Houston TX"')
        print('  python agent.py "Find orthopedic surgeons in Austin and San Antonio TX"')
        print('  DRY_RUN=true python agent.py "Find endocrinologists in Dallas TX"')
        sys.exit(1)

    run_agent(" ".join(sys.argv[1:]))
