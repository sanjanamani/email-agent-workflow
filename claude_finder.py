"""
claude_finder.py — Uses Claude to find real, currently operating
independent specialty clinics in DFW from training knowledge.

Makes ONE API call per specialty covering all cities at once.
No web search tool — Claude reasons from training data only.
Serper handles any follow-up discovery/enrichment.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

CITIES = ["Dallas", "Plano", "Frisco", "Allen", "McKinney", "Richardson"]

SYSTEM_PROMPT = (
    "You are a medical practice researcher with knowledge of real, currently "
    "operating independent specialty clinics in the Dallas-Fort Worth area. "
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
        f"List independent {specialty} clinics across these DFW cities: "
        f"{cities_str}, TX. "
        "Use your training knowledge to identify real practices.\n\n"
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


def _retry_after(exc: anthropic.RateLimitError, buffer: int = 10) -> int:
    """
    Read retry-after seconds from the exception's response headers.
    Falls back to 60s if the header is absent or unparseable.
    """
    fallback = 60
    try:
        headers = exc.response.headers  # type: ignore[attr-defined]
        val = headers.get("retry-after", "")
        if val:
            return int(val) + buffer
        reset = headers.get("anthropic-ratelimit-input-tokens-reset", "")
        if reset:
            reset_dt = datetime.fromisoformat(reset.replace("Z", "+00:00"))
            secs = int((reset_dt - datetime.now(timezone.utc)).total_seconds()) + buffer
            return max(secs, buffer)
    except Exception:
        pass
    return fallback


_MAX_RETRIES = 3


def find_practices(specialty: str) -> list[dict]:
    """
    Use Claude (training knowledge only, no web search) to list real
    independent specialty clinics across all DFW cities.

    Returns a list of dicts: {name, website, phone, address, city, specialty}
    Returns [] on any error or after all retries are exhausted.
    Retries on 429 (rate limit, using retry-after header) and 529 (overloaded,
    exponential backoff 5 → 10 → 20 → 40 s).
    """
    client = _get_client()
    user_content = _user_prompt(specialty)

    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            text = "".join(b.text for b in response.content if b.type == "text")
            if not text:
                log.warning("No text in Claude response for %s", specialty)
                return []
            practices = _extract_json_array(text)
            log.info("Claude found %d practices for %s", len(practices), specialty)
            return practices

        except anthropic.RateLimitError as exc:
            if attempt == _MAX_RETRIES:
                log.error("Claude 429 for %s — giving up after %d attempts", specialty, _MAX_RETRIES + 1)
                return []
            wait = _retry_after(exc)
            log.warning(
                "Claude 429 for %s — sleeping %ds (attempt %d/%d)…",
                specialty, wait, attempt + 1, _MAX_RETRIES + 1,
            )
            time.sleep(wait)

        except anthropic.APIStatusError as exc:
            if exc.status_code != 529 or attempt == _MAX_RETRIES:
                log.error("Claude API error for %s: %s", specialty, exc)
                return []
            delay = min(5 * (2 ** attempt), 60)  # 5 → 10 → 20 → 40 s
            log.warning(
                "Claude 529 overloaded for %s — sleeping %ds (attempt %d/%d)…",
                specialty, delay, attempt + 1, _MAX_RETRIES + 1,
            )
            time.sleep(delay)

        except anthropic.APIError as exc:
            log.error("Claude API error for %s: %s", specialty, exc)
            return []

    return []  # all retries exhausted


def find_all_practices(specialties: list[str]) -> list[dict]:
    """Call find_practices for each specialty and combine results."""
    print(
        "Note: if you just ran this script, wait 3-4 minutes before running "
        "again to let the token bucket refill."
    )
    all_results: list[dict] = []
    for specialty in specialties:
        all_results.extend(find_practices(specialty))
    return all_results
