# find_and_enrich

Finds independent endocrinology and orthopedic practices in DFW via Google Maps,
scrapes their websites for a contact email, then either adds them to a Brevo
contact list or logs them to a call list CSV for manual follow-up.

## What it does

1. Queries Google Maps Places API for endocrinology and orthopedic clinics
   across Dallas, Plano, Frisco, Allen, McKinney, and Richardson TX
2. Filters out hospital systems, health networks, and large medical groups
3. For each practice, fetches their website and scrapes the homepage, `/contact`,
   and `/about` pages for a human-reachable email address
4. If an email is found → creates a contact in Brevo (skips if already exists)
5. If no email is found → appends to `call_list.csv` with a priority flag
   (HIGH if reviews mention insurance or prior-authorization friction)

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure env vars

```bash
cp .env.example .env
# Edit .env and fill in your API keys
```

### 3. Get a Brevo list ID

1. Log in at [app.brevo.com](https://app.brevo.com)
2. Go to **Contacts → Lists**
3. Create a new list (e.g. "DFW Outreach") or use an existing one
4. Click the list — the numeric ID appears in the URL:
   `https://app.brevo.com/contact/list/id/**42**/`
5. Set `BREVO_LIST_ID=42` in your `.env`

### 4. Run

```bash
python find_and_enrich.py
```

Use `DRY_RUN=true` to preview actions without touching any APIs:

```bash
DRY_RUN=true python find_and_enrich.py
```

## Output

| Outcome | Where |
|---|---|
| Email found | Contact created in Brevo (your list) |
| No email found | Row appended to `call_list.csv` |

`call_list.csv` columns: Practice Name, Phone, Website, Specialty, City, Priority

## Env vars

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_MAPS_API_KEY` | Yes | — | Google Maps Places API key |
| `BREVO_API_KEY` | Yes | — | Brevo API key |
| `BREVO_LIST_ID` | Yes | — | Numeric ID of your Brevo contact list |
| `DRY_RUN` | No | `false` | Print actions without calling APIs |
| `MAX_PRACTICES` | No | `100` | Cap on practices processed per run |
