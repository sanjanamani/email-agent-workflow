# Inara AI — Outbound Email Agent

Automated outbound email agent for Sanjana Manikandan's prior-authorization research outreach. Finds independent endocrinology and orthopedic practices in the Dallas-Fort Worth area, discovers contact emails, and sends a 3-email research-framing sequence automatically.

---

## What it does

On each daily cron run:
1. **Discovers** practices via Google Maps Places API (endocrinology + orthopedic clinics across Dallas, Plano, Frisco, Allen, McKinney)
2. **Filters** to independent/small-group practices (drops hospital systems, academic medical centers)
3. **Finds emails** via Snov.io domain search (targets office managers, billing coordinators, practice managers)
4. **Deduplicates** — never contacts the same email or practice twice
5. **Logs all contacts** to a Google Sheet
6. **Sends Email 1** immediately for new contacts; **Email 2** after 5 days (if no reply); **Email 3** after 10 days total (if no reply); then marks the contact Closed
7. **Personalizes** each email using Claude Haiku (practice name, specialty, city)
8. **Rate-limits** to max 20 emails/day with a 30-second delay between sends
9. **Updates the sheet** at every step

---

## File structure

```
email-agent-workflow/
├── main.py                    # Entry point — full daily orchestration
├── config.py                  # All env vars, constants, search queries
├── Procfile                   # Render deployment
├── requirements.txt
├── .env.example               # Copy to .env for local dev
│
├── src/
│   ├── maps_client.py         # Google Maps Places API
│   ├── snov_client.py         # Snov.io email finder
│   ├── gmail_client.py        # Gmail API sender
│   ├── sheets_client.py       # Google Sheets tracker
│   ├── claude_client.py       # Claude email personalizer
│   ├── email_sequence.py      # Sequence logic (which email, when)
│   └── filters.py             # Independent practice filter
│
├── templates/
│   ├── email1.txt             # Claude prompt for intro email
│   ├── email2.txt             # Claude prompt for nudge
│   └── email3.txt             # Claude prompt for breakup email
│
└── tests/
    ├── conftest.py
    ├── test_filters.py
    ├── test_maps_client.py
    ├── test_snov_client.py
    ├── test_email_sequence.py
    ├── test_claude_client.py
    ├── test_gmail_client.py
    ├── test_sheets_client.py
    └── test_main.py
```

---

## Prerequisites

- Python 3.11+
- A Google Cloud project with billing enabled (for API access; the $200/month free credit covers Maps usage)
- A Snov.io account (free tier)
- An Anthropic account
- Gmail account: sanjana@emberluna.co

---

## API Key Setup

### 1. Google Maps Places API

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use existing)
3. Go to **APIs & Services → Library**
4. Enable **Places API (New)** (search "Places API")
5. Go to **APIs & Services → Credentials → Create Credentials → API Key**
6. Copy the key → set as `GOOGLE_MAPS_API_KEY`

**Tip:** Restrict the key to the Places API only under "API restrictions" for security.

---

### 2. Gmail API + OAuth2 (one-time local setup)

This is the most involved step. You only do it once.

**Step 1: Enable Gmail API**
1. In Google Cloud Console, go to **APIs & Services → Library**
2. Enable **Gmail API**

**Step 2: Create OAuth2 credentials**
1. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. Application type: **Desktop app**
3. Download the JSON → save as `credentials.json` in the project root

**Step 3: Configure OAuth consent screen**
1. Go to **APIs & Services → OAuth consent screen**
2. User type: **External**
3. Add your Gmail address as a test user
4. Scopes needed: `gmail.send`, `gmail.readonly`

**Step 4: Run the one-time auth flow locally**
```bash
# Make sure credentials.json is in the project root
python -c "from src.gmail_client import run_oauth_flow; run_oauth_flow()"
```
A browser window opens. Sign in as sanjana@emberluna.co and authorize. This generates `token.json`.

**Step 5: Encode token.json for Render**
```bash
base64 -w0 token.json
```
Copy the output → set as `GMAIL_CREDENTIALS` env var on Render.

> **Important:** `token.json` and `credentials.json` are in `.gitignore`. Never commit them.

---

### 3. Google Sheets API + Sheet setup

**Step 1: Enable Sheets API**
1. In Google Cloud Console → **APIs & Services → Library**
2. Enable **Google Sheets API**

> The Sheets API uses the same OAuth2 credentials as Gmail (same token.json). No extra setup needed.

**Step 2: Create the tracking spreadsheet**
1. Go to [Google Sheets](https://sheets.google.com) and create a new blank spreadsheet
2. Name it something like "Inara AI — Outreach Tracker"
3. Copy the spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/**[SHEET_ID]**/edit`
4. Set as `GOOGLE_SHEET_ID` env var

**Step 3: Share with your Gmail account**
Make sure sanjana@emberluna.co has Editor access to the sheet (it should by default if you created it with that account).

The agent writes headers automatically on first run.

---

### 4. Snov.io API

1. Sign up at [snov.io](https://snov.io) (free tier works)
2. Go to **Settings → API** or **Integrations → API**
3. Copy your **Client ID** and **Client Secret**
4. Set as `SNOV_CLIENT_ID` and `SNOV_CLIENT_SECRET`

**Free tier limits:** ~50 credits/month. Each domain search costs ~1 credit. This is enough for testing; upgrade if you're running high volume.

---

### 5. Anthropic API

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an API key
3. Set as `ANTHROPIC_API_KEY`

The agent uses `claude-haiku-4-5-20251001` (cheapest, fastest). Cost is negligible at this scale.

---

## Local Development

### Setup

```bash
# Clone and enter the repo
git clone <repo-url>
cd email-agent-workflow

# Create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy the env template
cp .env.example .env
# Edit .env and fill in your API keys
```

### Run in dry-run mode (recommended for testing)

```bash
DRY_RUN=true python main.py
```

This runs the full discovery and sequence logic but:
- Logs `[DRY RUN]` before every action that would write something
- Does NOT send any emails
- Does NOT write to the Google Sheet
- Does NOT require valid `GMAIL_CREDENTIALS` or `GOOGLE_SHEET_ID`

You only need `GOOGLE_MAPS_API_KEY`, `SNOV_CLIENT_ID`, `SNOV_CLIENT_SECRET`, and `ANTHROPIC_API_KEY` for a meaningful dry run (Maps + Snov + Claude still run). If you want to test completely offline, mock those too — see the test suite.

### Run tests

```bash
# Run all tests
pytest

# With coverage report
pytest --cov=src --cov=main --cov-report=term-missing

# Run a specific test file
pytest tests/test_email_sequence.py -v
```

All tests use mocked API calls — no real API keys needed for the test suite.

---

## Deploying to Render

### Step 1: Push to GitHub

Make sure your repo is on GitHub (private is fine). **Do not commit `.env`, `token.json`, or `credentials.json`.**

### Step 2: Create a Render Cron Job

1. Log in to [render.com](https://render.com)
2. New → **Cron Job**
3. Connect your GitHub repo
4. Settings:
   - **Name:** `inara-email-agent`
   - **Runtime:** Python 3
   - **Build command:** `pip install -r requirements.txt`
   - **Command:** `python main.py`
   - **Schedule:** `0 14 * * *` (9am Central = 14:00 UTC; adjust for daylight saving: `0 13 * * *` during CDT)

### Step 3: Set environment variables

In Render → your cron job → **Environment**, add all variables from `.env.example`:

| Variable | Value |
|---|---|
| `GOOGLE_MAPS_API_KEY` | your Maps API key |
| `SNOV_CLIENT_ID` | your Snov.io client ID |
| `SNOV_CLIENT_SECRET` | your Snov.io client secret |
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `GMAIL_CREDENTIALS` | output of `base64 -w0 token.json` |
| `GOOGLE_SHEET_ID` | your Google Sheet ID |
| `DRY_RUN` | `false` |
| `MAX_EMAILS_PER_DAY` | `20` |

### Step 4: First run

Trigger a manual run from the Render dashboard to verify everything works. Check the logs — you should see the full discovery → filter → enrich → send cycle.

### Ongoing maintenance

- You only need to look at this when replies come in — everything else is automatic.
- Check the Google Sheet periodically to see the Status column and reply tracking.
- If the Gmail OAuth token ever expires (it shouldn't with a refresh token, but just in case), re-run the local auth flow and update `GMAIL_CREDENTIALS` on Render.

---

## Google Sheet columns

| Column | Description |
|---|---|
| Practice Name | Name of the practice |
| Specialty | Endocrinology / Orthopedics / General |
| Address | Full street address |
| Phone | Phone number |
| Website | Practice website |
| Email | Contact email address |
| Contact Name | Name of the contact (if found by Snov) |
| Title | Contact's job title |
| Email Sent Date | Date Email 1 was sent |
| Email 2 Sent Date | Date Email 2 was sent |
| Email 3 Sent Date | Date Email 3 was sent |
| Reply Received | Y or N |
| Reply Date | Date of reply (fill in manually) |
| Status | New / Email 1 Sent / Email 2 Sent / Email 3 Sent / Closed / Replied / Error |
| Notes | Error messages or manual notes |

> **Reply tracking is semi-manual.** When you see a reply, update the sheet: set **Reply Received = Y**, fill in **Reply Date**, set **Status = Replied**. The agent checks Status before sending and will skip Replied rows automatically.

---

## Email sequence

| Email | When sent | Purpose |
|---|---|---|
| Email 1 | Day 0 (contact added) | Intro — research framing, ask for 15 min call or survey |
| Email 2 | Day 5 (if no reply) | Nudge — offer survey as lower-friction option |
| Email 3 | Day 10 (if no reply) | Breakup — last note, leave door open |
| — | Day 15+ | Marked Closed, no further contact |

Survey link: https://tally.so/r/VLllxl

---

## Troubleshooting

**`No Google credentials found`** — Either `GMAIL_CREDENTIALS` env var is not set, or the base64 encoding is wrong. Re-encode with `base64 -w0 token.json` (the `-w0` flag disables line wrapping).

**`GOOGLE_SHEET_ID is not set`** — Set the env var and redeploy.

**Snov.io returns no emails** — The practice's domain may not have indexed emails yet. The agent logs this and skips those practices gracefully.

**Gmail send fails with 403** — The OAuth consent screen may need to be moved from "Testing" to "Production" in Google Cloud Console, or the refresh token may have expired. Re-run the local auth flow.

**Maps returns `REQUEST_DENIED`** — Your API key is missing, invalid, or the Places API isn't enabled. Check the key restrictions in Google Cloud Console.
