"""
claude_client.py — Anthropic Claude client for email personalization.

Reads prompt templates from the templates/ directory, fills in practice
details, and calls Claude to generate a personalized email subject + body.
"""

import logging
import os
import re
from typing import Optional

import anthropic

import config
from src.filters import extract_city

logger = logging.getLogger("claude_client")

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")


class ClaudeClient:
    """Generates personalized emails using Claude Haiku."""

    def __init__(self, api_key: str = "", model: str = ""):
        self._api_key = api_key or config.ANTHROPIC_API_KEY
        self._model = model or config.CLAUDE_MODEL
        self._client: Optional[anthropic.Anthropic] = None

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------
    # Main public interface
    # ------------------------------------------------------------------

    def generate_email(
        self,
        email_number: int,
        practice_name: str,
        specialty: str,
        city: str,
        contact_name: str = "",
    ) -> tuple[str, str]:
        """
        Generate a personalized email using Claude.

        Returns (subject, body) tuple.
        On any error, returns sensible fallback strings so the agent can still
        log without crashing.

        Args:
            email_number:   1, 2, or 3
            practice_name:  e.g. "North Texas Orthopedic Center"
            specialty:      e.g. "Orthopedics"
            city:           e.g. "Plano"
            contact_name:   e.g. "Jennifer Smith" (optional)
        """
        template = _load_template(email_number)
        if not template:
            return _fallback_email(email_number, practice_name, specialty, city, contact_name)

        prompt = template.format(
            practice_name=practice_name,
            specialty=specialty,
            city=city,
            contact_name=contact_name or "",
            survey_link=config.SURVEY_LINK,
        )

        if config.DRY_RUN:
            logger.info(
                "[DRY RUN] Would call Claude for email %d to '%s'", email_number, practice_name
            )
            return _fallback_email(email_number, practice_name, specialty, city, contact_name)

        try:
            client = self._get_client()
            message = client.messages.create(
                model=self._model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text if message.content else ""
            subject, body = _parse_response(raw)
            logger.info(
                "Claude generated email %d for '%s' — subject: %r",
                email_number, practice_name, subject,
            )
            return subject, body
        except anthropic.APIError as exc:
            logger.error("Claude API error generating email %d for '%s': %s", email_number, practice_name, exc)
            return _fallback_email(email_number, practice_name, specialty, city, contact_name)
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error from Claude: %s", exc)
            return _fallback_email(email_number, practice_name, specialty, city, contact_name)

    def generate_email_for_contact(self, row: dict, email_number: int) -> tuple[str, str]:
        """
        Convenience wrapper that takes a sheet row dict and returns (subject, body).
        """
        city = extract_city(row.get("Address", "")) or "the DFW area"
        return self.generate_email(
            email_number=email_number,
            practice_name=row.get("Practice Name", ""),
            specialty=row.get("Specialty", ""),
            city=city,
            contact_name=row.get("Contact Name", ""),
        )


# ---------------------------------------------------------------------------
# Template loader
# ---------------------------------------------------------------------------

def _load_template(email_number: int) -> str:
    """Load the prompt template for email N. Returns empty string on error."""
    path = os.path.join(TEMPLATES_DIR, f"email{email_number}.txt")
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        logger.error("Template file not found: %s", path)
        return ""


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> tuple[str, str]:
    """
    Parse Claude's response into (subject, body).

    Expected format:
        SUBJECT: <subject line>
        <blank line>
        <email body...>

    Falls back gracefully if the format is slightly off.
    """
    raw = raw.strip()
    subject = ""
    body = raw

    # Look for "SUBJECT: ..." on the first line
    lines = raw.split("\n")
    if lines and lines[0].upper().startswith("SUBJECT:"):
        subject_line = lines[0]
        subject = re.sub(r"(?i)^subject:\s*", "", subject_line).strip()
        # Body is everything after the subject line (skip leading blank lines)
        body_lines = lines[1:]
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        body = "\n".join(body_lines).strip()

    return subject, body


# ---------------------------------------------------------------------------
# Fallback emails (used in dry-run mode and on API error)
# ---------------------------------------------------------------------------

def _fallback_email(
    email_number: int,
    practice_name: str,
    specialty: str,
    city: str,
    contact_name: str,
) -> tuple[str, str]:
    """Return a hardcoded fallback (subject, body) when Claude is unavailable."""
    greeting = f"Hi {contact_name}," if contact_name else f"Hi {practice_name} team,"

    if email_number == 1:
        subject = "Quick question about prior auth — UT Dallas research"
        body = (
            f"{greeting}\n\n"
            f"My name is Sanjana Manikandan — I'm a recent UT Dallas grad doing independent "
            f"research on how {specialty.lower()} practices in {city} handle prior authorizations "
            f"for specialty medications and procedures.\n\n"
            f"I'm not selling anything — I'm just trying to understand the real workflow from "
            f"the people who live it every day. Would you be willing to share 15 minutes for a "
            f"quick call? Or if a call doesn't work, there's a 2-minute survey here: "
            f"{config.SURVEY_LINK}\n\n"
            f"Either way, I really appreciate your time.\n\nBest,\nSanjana"
        )
    elif email_number == 2:
        subject = "Re: Quick question about prior auth — UT Dallas research"
        body = (
            f"{greeting}\n\n"
            f"Just following up on my note from last week. I know inboxes get busy — totally "
            f"understand! If a call isn't feasible, the survey takes about 2 minutes and would "
            f"be incredibly helpful: {config.SURVEY_LINK}\n\n"
            f"No pressure at all — thanks for your time.\n\nBest,\nSanjana"
        )
    else:
        subject = "Last note from Sanjana — prior auth research"
        body = (
            f"{greeting}\n\n"
            f"This is my last note so I don't clog your inbox! If you ever have a few minutes "
            f"to share your experience with prior authorizations, the survey is always open: "
            f"{config.SURVEY_LINK}\n\n"
            f"Wishing you a great rest of your week!\n\nBest,\nSanjana"
        )

    return subject, body
