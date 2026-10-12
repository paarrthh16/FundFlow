# FundFlow — Assessment Notes

> **Status (2026-10-12): implemented and committed. Final verification still to do.**
>
> Implemented: the Django + DRF backend, the PostgreSQL data model and constraints, `seed_demo`, CSV import with import-time matching, the approve / reject / ignore actions, the listing APIs, and the React frontend (phases 7A–7D plus the refresh/accessibility remediation).
>
> The setup steps and the test command below were run on Windows during development. The remaining checks are listed under **Next steps**.

## App name

**FundFlow** — a capital-call bank reconciliation tool for GP fund administrators. The name is shown in the page title and header.

## Setup instructions (Windows PowerShell / VS Code)

Verified with: Python 3.11.9, Node.js 22.14.0 (Vite 7 needs Node 20.19+ or 22.12+), Docker Desktop (engine 29.6.2), PostgreSQL 17 (Docker image `postgres:17`).

Only PostgreSQL runs in Docker. Django and Vite run directly on the host.

```powershell
# 1. From the repository root: create your local settings file, then replace the two placeholder values
Copy-Item .env.example .env
#    (set DJANGO_SECRET_KEY and POSTGRES_PASSWORD in .env; never commit .env)

# 2. Start PostgreSQL (first run downloads the ~650 MB postgres:17 image) and wait until it is healthy
docker compose up -d --wait db

# 3. Backend
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver            # http://127.0.0.1:8000/api/health/

# 4. Frontend (second terminal, from the repository root)
cd frontend
npm ci
npm run dev                           # http://localhost:5173  (proxies /api to Django)
```

- **VS Code:** select the interpreter `backend\.venv\Scripts\python.exe` (Command Palette → "Python: Select Interpreter").
- **Seed data:** after `migrate`, run `python manage.py seed_demo` (safe to repeat; creates only missing records and never resets funding).
- **Stop the database:** `docker compose stop db`. Data persists in the named volume `fundflow_pgdata`.
- **Reset the database:** `docker compose down -v`. This deletes the FundFlow volume only.

### Environment settings (`.env`, template in `.env.example`)

| Variable | Used by | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django | Required; long random string |
| `DJANGO_DEBUG` | Django | `true` for local development; defaults to `false` if unset |
| `DJANGO_ALLOWED_HOSTS` | Django | Comma-separated host names |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | compose + Django | Database name and credentials (compose creates the database and user on first start) |
| `POSTGRES_HOST`, `POSTGRES_PORT` | Django (port also compose) | Default `localhost:5432` |
| `API_PROXY_TARGET` | Vite dev server | Where `/api` is proxied; default `http://127.0.0.1:8000` |

Django loads the root `.env` through `python-dotenv`. Real environment variables take precedence. A missing required variable stops startup with a clear `ImproperlyConfigured` error.

## Single test command

From `backend/`, with the virtual environment active and the database running:

```powershell
python manage.py test reconciliation
```

Tests run against PostgreSQL. Django creates and drops a `test_<POSTGRES_DB>` database, so the configured user needs `CREATEDB`; the compose user is a superuser. SQLite is not used anywhere.

## Data model overview

| Model | Key fields | Integrity |
|---|---|---|
| Organization | `slug` (`northwind`, `bluebird`), `name` | unique slug |
| InvestorEntity | organization, name | unique name per org |
| CapitalCall | organization, number, call date, due date | unique number per org |
| LineItem | organization, capital call, investor, expected reference, amount called, amount funded, status | 0 ≤ funded ≤ called; status consistent with amounts; one per (call, investor); **expected reference not unique** |
| BankTransaction | organization, date, description, signed Decimal amount, type, bank reference, status, review reason | bank reference unique per org; sign matches type; debit ⇒ ignored; `needs_review` ⇒ reason |
| Match | organization, transaction, line item, status, reason | at most one approved per transaction; one per (transaction, line item) |

- Remaining amount is derived (called − funded). Money is `Decimal(14, 2)`, never float.
- Approvals lock the bank transaction row, then its matches, then the line item, and re-check state after locking, so a line item is never funded twice and one payment never funds two line items.

## API endpoints

**Common rules:**

- **Organization header:** every endpoint except `/api/health/` and `/api/organizations/` requires `X-Organization: <slug>`.
  - Missing, empty or whitespace-only header → `400`.
  - Unknown slug (exact, case-sensitive match) → `404`.
  - Another organization's record, or an unknown or malformed ID → `404`.
- **Errors** are JSON: `{"detail": "..."}`.
- **Amounts** are decimal strings, for example `"25000.00"`.
- **Lists** are plain JSON arrays with no pagination.
- **Admin:** the Django admin is not routed.

| Method | Path | Header | Returns | Errors |
|---|---|---|---|---|
| GET | `/api/health/` | No | `{"status":"ok","database":"ok"}`, or `503` `{"status":"error","database":"unavailable"}`. Never exposes configuration | — |
| GET | `/api/organizations/` | No | `[{slug, name}]` ordered by slug; no financial data | — |
| POST | `/api/imports/` | Yes | Multipart field `file`. Returns `200` `{imported_count, duplicate_count, duplicates[{line, reference, message}], errors[{line, message}]}`; credits are matched at import time | `400`: no file, empty, not UTF-8, or a missing/repeated required column (nothing saved). `404`: unknown organization. `409`: concurrent duplicate bank reference (nothing saved) |
| GET | `/api/transactions/` | Yes | Transactions newest first (`date DESC, id DESC`), each with `matches[{id, status, reason, line_item{id, expected_reference, investor_name}}]` and `review_reason` | `400` / `404` (header) |
| GET | `/api/line-items/` | Yes | `[{id, capital_call{number, call_date, due_date}, investor_name, expected_reference, amount_called, amount_funded, amount_remaining, status}]`, ordered by call date, reference, id | `400` / `404` (header) |
| POST | `/api/matches/{id}/approve/` | Yes | `200` with the updated transaction: funds the line item, approves the match, rejects the others, marks the transaction `reconciled` | `404`. `409`: match not `proposed`; transaction not a `suggested`/`needs_review` credit; call date after the transaction date; or amount exceeds the current remaining amount |
| POST | `/api/matches/{id}/reject/` | Yes | `200` with the updated transaction. If no proposed matches remain, the transaction becomes `needs_review` with a reason | `404`. `409`: match not `proposed`, or transaction not actionable |
| POST | `/api/transactions/{id}/ignore/` | Yes | `200` with the updated transaction, now `ignored`; its proposed matches are rejected | `404`. `409`: transaction `reconciled`, already `ignored`, or a debit |

## Assumptions and trade-offs

**Accepted (2026-10-11):**

- **CSV:**
  - A UTF-8 BOM is accepted.
  - Header names are trimmed and matched case-insensitively.
  - Columns may be reordered; extra columns are ignored.
  - All five required columns must be present.
- **Amounts:** strict format. More than 2 decimal places, currency symbols, parentheses, `+` and malformed grouping are row errors; amounts are never rounded.
- **Blank rows** create nothing and are not reported. Reported line numbers are physical file lines, with the header as line 1.
- **Duplicates:**
  - Reported with source line, bank reference and an explanation (`already imported for this organization` or `same as line N in this file`), and included in `duplicate_count`.
  - Bank references are compared exactly after trimming, with no further normalisation.
  - An invalid row is never counted as a duplicate.
- **Debits:** valid debits count as imported (stored as `ignored`).
- **Matching** uses exactly the brief's normalisation. Expected wire references are **not** forced to be unique; a shared reference produces several candidates.
- **409 policy:** state-changing actions that conflict with the current state (rejected or approved match, ignored or reconciled transaction, debit) return `409`. The brief's own status codes are unchanged.
- **Ordering:** transactions by date descending, then ID descending.
- **`seed_demo`** creates only missing records and never updates or resets funding, status, transactions or matches.
- **Money display:** `12,500.00` style, two decimals. No currency symbol, because the brief names no currency.
- **Organization header:** empty or whitespace-only → `400`; slugs matched exactly.
- **Organization list endpoint:** an additional, read-only, header-free endpoint for the switcher that exposes no financial data.

**Implementation defaults (adopted 2026-10-11):**

- One error entry per invalid row.
- Each upload is one database transaction, and imports are serialised per organization.
- A non-UTF-8 file or a repeated required header → `400`.
- A row with more values than header columns → row error.
- Amounts stored as `Decimal(14, 2)` and serialised as decimal strings.
- Actions and upload return `200`.
- The Django admin is not routed.

**Trade-offs:**

- Reference matching uses substring containment as the brief specifies, so `CC1001` also matches inside `CC10010`.
- The organization is stored on every tenant table for simple scoping. Cross-table consistency is checked in services and tests, because Django has no composite foreign keys.
- No import history entity: the summary is returned once, not stored.
- **No pagination** on the list endpoints, which follows the documented plain-array contract. A very large organization would receive every row in one response.
- **Switching organization doesn't cancel uploads or actions,** because the server may already be processing them. Their late results are discarded in the frontend, but the server-side change still happens.
- **No automated frontend tests;** the brief requires none. The frontend has been verified by source inspection, `npm run build` and manual/live browser checks.

## Next steps

Outstanding before submission:

1. **Final technical checks** from a fresh clone, following only these notes:
   - setup, `migrate`, `seed_demo`;
   - `python manage.py test reconciliation`, `python manage.py check`, `python manage.py makemigrations --check`;
   - `npm ci` and `npm run build`.
2. **Manual browser testing** on the final code:
   - a final pass over the main UI flows: organization switching, CSV upload and import summary, the transaction table and its actions, line-item updates, loading/empty/error states, 409 handling and money formatting;
   - remaining gaps: the organization-list error when the page is first loaded with the backend already stopped, and the 409-refresh path since the last frontend change. Already checked manually: narrow-screen layout, basic colour contrast and keyboard focus, and backend stop/restart with error/retry recovery.
3. **Recording** (up to 5 minutes, brief §12): the seed command and CSV upload, the import summary, suggestions and `needs_review` items, an approval updating a line item, switching organization, and a quick code tour.
4. **Submission:**
   - decide which local files are part of the deliverable and commit them (this file and `README.md` are written but not yet committed);
   - complete the integrity section below truthfully;
   - provide the repository or ZIP.
5. **Optional, only if time allows:** the Airtable bonus (brief §10) and a deployment.

## Integrity statement

I take responsibility for the implementation, testing, and final submission of this assessment. I have reviewed the submitted work and verified its core functionality.
