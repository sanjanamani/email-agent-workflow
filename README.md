# find_and_enrich

Finds independent endocrinology and orthopedic practices in Dallas-Fort Worth,
scrapes their websites for contact emails, validates them with Claude, and
routes them into Brevo (for email outreach) or a call list CSV (for manual follow-up).

## Architecture

```
claude_finder.py         Claude + web search → finds real practices
serper_enricher.py       Serper (Google Search) → fills phone/website gaps
find_and_enrich.py       Orchestrates everything → Brevo or call_list.csv
```

### Pipeline steps

| Step | What happens |
|------|-------------|
| 1 | Claude uses web search to find up to 10 real independent clinics per specialty × city combo (12 combos total). Returns structured JSON verified against live web results. |
| 2 | Any practice missing a phone or website is passed to Serper, which searches Google and pulls data from the knowledge graph, places results, and organic results. |
| 3 | Each practice's website is scraped (homepage + `/contact` + `/contact-us` + `/about` + `/about-us`). All email addresses are collected — from `mailto:` links and regex on page text. |
| 4 | Claude reviews the candidate emails and picks the best one for a practice manager or billing coordinator. It also flags fax numbers. Generic/automated addresses are rejected. |
| 5 | **High/medium-confidence email found** → contact added to Brevo. **No valid email** → row appended to `call_list.csv`. |
| 6 | `brevo_added.csv` and `call_list.csv` saved. Summary printed. |

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure env vars

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

### 3. Set up a Brevo contact list

1. Log in at [app.brevo.com](https://app.brevo.com)
2. Go to **Contacts → Lists** and create a new list (e.g. "DFW Outreach")
3. Click the list — the numeric ID is in the URL:
   `https://app.brevo.com/contact/list/id/**7**/`
4. Set `BREVO_LIST_ID=7` in your `.env`

The script adds these contact attributes: `FIRSTNAME`, `LASTNAME` (practice name),
`PHONE`, `PRACTICE_NAME`, `SPECIALTY`, `CITY`. You'll need to create these custom
attributes in Brevo under **Contacts → Settings → Contact attributes** before the
first run if they don't exist.

### 4. Run

```bash
python find_and_enrich.py
```

Preview without touching Brevo (CSVs are still saved):

```bash
DRY_RUN=true python find_and_enrich.py
```

## Output files

| File | Contents |
|------|---------|
| `brevo_added.csv` | All contacts successfully added to Brevo: name, email, confidence, city, specialty, phone, website |
| `call_list.csv` | Practices with no usable email: name, phone, website, specialty, city, address, reason |

### Understanding `call_list.csv`

Each row is a practice that couldn't be reached by email — reasons include:

- **no email found** — website had no extractable email
- **low confidence email** — Claude found something but wasn't confident it reaches a human
- **fax number only** — the only phone number found appears to be a fax line
- **no contact info found** — no website and no phone

Use the `Phone` column to call these practices directly and ask for the office manager or billing coordinator.

## Env vars

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `SERPER_API_KEY` | No | — | Serper API key (enrichment skipped if absent) |
| `BREVO_API_KEY` | Yes* | — | Brevo API key (*not required with DRY_RUN=true) |
| `BREVO_LIST_ID` | Yes* | 7 | Numeric Brevo list ID |
| `DRY_RUN` | No | `false` | Print actions without writing to Brevo |
| `MAX_PRACTICES` | No | `300` | Cap on total practices processed per run |
