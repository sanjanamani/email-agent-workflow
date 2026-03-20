"""
config.py — Central configuration for the Inara AI email agent.

All environment variables are read here. Nothing else in the codebase
should call os.getenv() directly.
"""

import os
import logging

# ---------------------------------------------------------------------------
# Logging — stdout so Render captures it
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("config")

# ---------------------------------------------------------------------------
# Mode flags
# ---------------------------------------------------------------------------
# Set DRY_RUN=true to log every action without sending emails or writing sheets.
DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

if DRY_RUN:
    logger.info("[DRY RUN] Dry-run mode active — no emails will be sent, no sheet writes.")

# ---------------------------------------------------------------------------
# API credentials
# ---------------------------------------------------------------------------
GOOGLE_MAPS_API_KEY: str = os.getenv("GOOGLE_MAPS_API_KEY", "")
SNOV_CLIENT_ID: str = os.getenv("SNOV_CLIENT_ID", "")
SNOV_CLIENT_SECRET: str = os.getenv("SNOV_CLIENT_SECRET", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# Base64-encoded contents of the Gmail OAuth token JSON (token.json).
# Generate once locally, then: base64 -w0 token.json → paste as env var.
GMAIL_CREDENTIALS_B64: str = os.getenv("GMAIL_CREDENTIALS", "")

# Google Sheets spreadsheet ID (from the URL: /spreadsheets/d/<ID>/edit)
GOOGLE_SHEET_ID: str = os.getenv("GOOGLE_SHEET_ID", "")

# ---------------------------------------------------------------------------
# Email identity
# ---------------------------------------------------------------------------
FROM_NAME: str = "Sanjana Manikandan"
FROM_EMAIL: str = "sanjana@emberluna.co"
SURVEY_LINK: str = "https://tally.so/r/VLllxl"

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
MAX_EMAILS_PER_DAY: int = int(os.getenv("MAX_EMAILS_PER_DAY", "20"))
SEND_DELAY_SECONDS: int = int(os.getenv("SEND_DELAY_SECONDS", "30"))

# ---------------------------------------------------------------------------
# Email sequence timing (days between emails)
# ---------------------------------------------------------------------------
EMAIL_2_WAIT_DAYS: int = 5
EMAIL_3_WAIT_DAYS: int = 10  # 5 days after email 2

# ---------------------------------------------------------------------------
# Google Maps search configuration
# ---------------------------------------------------------------------------
SPECIALTIES = ["endocrinology", "orthopedic surgery"]

DFW_CITIES = [
    "Dallas, TX",
    "Plano, TX",
    "Frisco, TX",
    "Allen, TX",
    "McKinney, TX",
]

# Built dynamically from SPECIALTIES × DFW_CITIES
SEARCH_QUERIES: list[str] = [
    f"{specialty} clinic {city}"
    for specialty in SPECIALTIES
    for city in DFW_CITIES
]

# Max results to request per Maps query (capped by API at 20/page)
MAPS_MAX_RESULTS_PER_QUERY: int = 20

# ---------------------------------------------------------------------------
# Hospital/health-system keywords — practices matching these are filtered out
# ---------------------------------------------------------------------------
HOSPITAL_SYSTEM_KEYWORDS: list[str] = [
    "ut southwestern",
    "utsw",
    "baylor",
    "hca",
    "tenet",
    "methodist",
    "parkland",
    "children's health",
    "childrens health",
    "texas health",
    "medical center",
    "hospital",
    "health system",
    "health network",
    "university",
    "academic medical",
    "va ",
    "veterans affairs",
    "kaiser",
    "dignity health",
    "commonspirit",
    "ascension",
]

# ---------------------------------------------------------------------------
# Google Sheets column layout (1-indexed)
# ---------------------------------------------------------------------------
SHEET_COLUMNS = [
    "Practice Name",       # A
    "Specialty",           # B
    "Address",             # C
    "Phone",               # D
    "Website",             # E
    "Email",               # F
    "Contact Name",        # G
    "Title",               # H
    "Email Sent Date",     # I
    "Email 2 Sent Date",   # J
    "Email 3 Sent Date",   # K
    "Reply Received",      # L  Y/N
    "Reply Date",          # M
    "Status",              # N
    "Notes",               # O
]

# Column letter helpers (A=1)
COL = {name: idx + 1 for idx, name in enumerate(SHEET_COLUMNS)}

# Status values
STATUS_NEW = "New"
STATUS_EMAIL1_SENT = "Email 1 Sent"
STATUS_EMAIL2_SENT = "Email 2 Sent"
STATUS_EMAIL3_SENT = "Email 3 Sent"
STATUS_CLOSED = "Closed"
STATUS_REPLIED = "Replied"
STATUS_ERROR = "Error"

# ---------------------------------------------------------------------------
# Claude model
# ---------------------------------------------------------------------------
CLAUDE_MODEL: str = "claude-haiku-4-5-20251001"
