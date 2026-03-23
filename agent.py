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
# Agentic loop — generator core
# ---------------------------------------------------------------------------

def run_agent_stream(goal: str):
    """
    Generator that runs the agentic loop and yields typed event dicts:

      {"type": "status",      "message": str}
      {"type": "text",        "content": str}           # streamed token by token
      {"type": "tool_call",   "name": str, "input_preview": str}
      {"type": "tool_result", "name": str, "preview": str}
      {"type": "done",        "turns": int}
      {"type": "error",       "message": str}
    """
    if not ANTHROPIC_API_KEY:
        yield {"type": "error", "message": "ANTHROPIC_API_KEY is not set"}
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages: list[dict] = [{"role": "user", "content": goal}]

    yield {"type": "status", "message": f"Starting | model={AGENT_MODEL} | dry_run={DRY_RUN}"}

    turn = 0
    while True:
        turn += 1
        yield {"type": "status", "message": f"Turn {turn} — calling Claude…"}

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
                            yield {"type": "text", "content": event.delta.text}
                    response = stream.get_final_message()
                break

            except anthropic.RateLimitError as exc:
                if attempt == _MAX_RETRIES:
                    yield {"type": "error", "message": f"Rate limit exceeded after {_MAX_RETRIES + 1} attempts: {exc}"}
                    return
                wait = _retry_after_secs(exc)
                yield {"type": "status", "message": f"Rate limited — sleeping {wait}s (attempt {attempt + 1}/{_MAX_RETRIES + 1})…"}
                log.warning("Agent 429 — sleeping %ds", wait)
                time.sleep(wait)

            except anthropic.APIStatusError as exc:
                if exc.status_code != 529 or attempt == _MAX_RETRIES:
                    yield {"type": "error", "message": f"API error {exc.status_code}: {exc.message}"}
                    return
                delay = min(5 * (2 ** attempt), 60)
                yield {"type": "status", "message": f"API overloaded — sleeping {delay}s (attempt {attempt + 1}/{_MAX_RETRIES + 1})…"}
                log.warning("Agent 529 — sleeping %ds", delay)
                time.sleep(delay)

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            yield {"type": "done", "turns": turn}
            return

        if response.stop_reason != "tool_use":
            yield {"type": "error", "message": f"Unexpected stop_reason: {response.stop_reason!r}"}
            return

        # Execute every tool call, stream results back
        tool_results: list[dict] = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            input_preview = json.dumps(block.input)[:160]
            yield {"type": "tool_call", "name": block.name, "input_preview": input_preview}

            result_str = _execute_tool(block.name, block.input)
            preview = result_str[:300] + ("…" if len(result_str) > 300 else "")
            yield {"type": "tool_result", "name": block.name, "preview": preview}

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})


def run_agent(goal: str) -> None:
    """CLI wrapper: runs run_agent_stream and prints to stdout."""
    print(f"\n{'=' * 60}")
    print(f"GOAL   : {goal}")
    print(f"MODEL  : {AGENT_MODEL}")
    print(f"DRY_RUN: {DRY_RUN}")
    print(f"{'=' * 60}\n")

    for event in run_agent_stream(goal):
        t = event["type"]
        if t == "text":
            print(event["content"], end="", flush=True)
        elif t == "tool_call":
            print(f"\n[TOOL] {event['name']}({event['input_preview']})")
        elif t == "tool_result":
            print(f"       → {event['preview']}")
        elif t == "status":
            log.info(event["message"])
        elif t == "done":
            print(f"\n\n[Agent finished in {event['turns']} turn(s)]")
        elif t == "error":
            print(f"\n[ERROR] {event['message']}", file=sys.stderr)
            sys.exit(1)


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
