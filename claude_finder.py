"""
claude_finder.py — Uses Claude with web search to find real, currently
operating independent specialty clinics in DFW.

The web_search tool is server-side: Anthropic executes searches automatically.
We loop to handle pause_turn (server-side iteration limit) until end_turn.
"""

import json
import logging
import os
import re
import time

import anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_CONTINUATIONS = 5  # guard against runaway pause_turn loops

SYSTEM_PROMPT = (
    "You are a medical practice researcher finding real, currently "
    "operating independent specialty clinics in the Dallas-Fort Worth area. "
    "You have access to web search — use it to verify practices are real. "
    "Return ONLY valid JSON, no explanation, no markdown."
)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    return _client


def _user_prompt(specialty: str, city: str) -> str:
    return (
        f"Search for independent {specialty} clinics in {city}, Texas. "
        "Use web search to verify they exist and are currently operating.\n\n"
        "Exclude: hospitals, Baylor, UT Southwestern, Methodist, Parkland, "
        "Children's Medical, any group with 10+ locations, urgent care, "
        "walk-in clinics.\n\n"
        "Return ONLY this JSON array:\n"
        "[\n"
        "  {\n"
        '    "name": "practice name",\n'
        '    "website": "https://... or empty string",\n'
        '    "phone": "(xxx) xxx-xxxx or empty string",\n'
        '    "address": "full address or empty string",\n'
        f'    "city": "{city}",\n'
        f'    "specialty": "{specialty}"\n'
        "  }\n"
        "]\n"
        "Return max 10 practices. Only include ones you are confident are real "
        "independent practices currently operating."
    )


def _extract_json_array(text: str) -> list[dict]:
    """Pull the first JSON array out of text and return valid practice dicts."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group())
        return [p for p in data if isinstance(p, dict) and p.get("name")]
    except (json.JSONDecodeError, ValueError):
        return []


def find_practices(specialty: str, city: str) -> list[dict]:
    """
    Use Claude + web search to find real independent specialty clinics in city.

    Returns a list of dicts:
      {name, website, phone, address, city, specialty}

    Returns [] on any error or empty result.
    1-second delay on exit (rate limiting).
    """
    client = _get_client()
    user_content = _user_prompt(specialty, city)
    messages: list[dict] = [{"role": "user", "content": user_content}]
    response = None

    try:
        for _ in range(MAX_CONTINUATIONS):
            response = client.messages.create(
                model=MODEL,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
            )

            if response.stop_reason == "end_turn":
                break
            elif response.stop_reason == "pause_turn":
                # Server-side tool hit iteration limit; re-send to let it continue.
                # Do NOT add a new user message — the API detects the trailing
                # server_tool_use block and resumes automatically.
                messages = [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": response.content},
                ]
            else:
                # Unexpected stop reason; use whatever we have
                break

        if response is None:
            return []

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        if not text:
            log.warning("No text in Claude response for %s / %s", specialty, city)
            return []

        practices = _extract_json_array(text)
        log.info(
            "Claude found %d practices for %s / %s", len(practices), specialty, city
        )
        return practices

    except anthropic.APIError as exc:
        log.error("Claude API error for %s / %s: %s", specialty, city, exc)
        return []
    finally:
        time.sleep(1)
