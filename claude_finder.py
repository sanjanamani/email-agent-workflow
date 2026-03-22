"""
claude_finder.py — Uses Claude with web search to find real, currently
operating independent specialty clinics in DFW.

Makes ONE API call per specialty covering all cities at once, avoiding
the per-city rate limiting that occurs with 12 separate calls.
"""

import json
import logging
import os
import re

import anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_CONTINUATIONS = 5  # guard against runaway pause_turn loops

CITIES = ["Dallas", "Plano", "Frisco", "Allen", "McKinney", "Richardson"]

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
        # max_retries=0: disable SDK auto-retries on 429 so our logic controls backoff
        _client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            max_retries=0,
        )
    return _client


def _user_prompt(specialty: str) -> str:
    cities_str = ", ".join(CITIES)
    return (
        f"Find independent {specialty} clinics across these DFW cities: "
        f"{cities_str}, TX. "
        "Use web search to verify they exist and are currently operating.\n\n"
        "Exclude: hospitals, Baylor, UT Southwestern, Methodist, Parkland, "
        "Children's Medical, any group with 10+ locations, urgent care, "
        "walk-in clinics.\n\n"
        "Return ONLY this JSON array (up to 15 practices total across all cities, "
        "no duplicates):\n"
        "[\n"
        "  {\n"
        '    "name": "practice name",\n'
        '    "website": "https://... or empty string",\n'
        '    "phone": "(xxx) xxx-xxxx or empty string",\n'
        '    "address": "full address or empty string",\n'
        '    "city": "one of the cities listed above",\n'
        f'    "specialty": "{specialty}"\n'
        "  }\n"
        "]\n"
        "Only include practices you are confident are real and currently operating."
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


def find_practices(specialty: str) -> list[dict]:
    """
    Use Claude + web search to find real independent specialty clinics across
    all DFW cities in a single API call.

    Returns a list of dicts: {name, website, phone, address, city, specialty}
    Returns [] on any error or empty result.
    """
    client = _get_client()
    user_content = _user_prompt(specialty)
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
                # Server-side tool hit iteration limit; re-send to continue.
                messages = [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": response.content},
                ]
            else:
                break

        if response is None:
            return []

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        if not text:
            log.warning("No text in Claude response for %s", specialty)
            return []

        practices = _extract_json_array(text)
        log.info("Claude found %d practices for %s", len(practices), specialty)
        return practices

    except anthropic.APIError as exc:
        log.error("Claude API error for %s: %s", specialty, exc)
        return []
