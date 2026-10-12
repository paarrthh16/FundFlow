# FundFlow

FundFlow is a capital-call bank reconciliation tool for General Partner (GP) fund administrators. It imports a bank statement CSV, suggests which capital-call line item each incoming payment belongs to (and explains why), and lets the user approve, reject or ignore each suggestion. Approving a match updates how much the limited partner (LP) has funded.

Each GP firm is a separate organization (tenant). Every request is scoped to one organization, and an organization can never see or change another organization's data.

## Features

- **Organization-scoped reconciliation:** the organization is selected per request with the `X-Organization` header; the UI provides an organization switcher.
- **CSV import and validation:** upload through the web page. Each row is validated independently: invalid rows are reported with their file line number, and valid rows are still imported. A missing required column rejects the whole file and saves nothing.
- **Duplicate detection:** a bank reference already imported for the organization, or repeated earlier in the same file, is skipped and reported with its line number and an explanation. The same reference may exist in different organizations.
- **Match suggestions:** each credit is matched once, at import time.
  - **Reference first:** if a line item's expected reference appears in the description or bank reference (normalised, so `cc1001` matches `CC-1001`), it is proposed.
  - **Name fallback:** otherwise, the investor name is matched as whole words, but only when the amount equals the line item's remaining amount exactly.
  - Every suggestion and every `needs_review` transaction carries a reason; overpayments and ambiguous matches go to `needs_review`.
- **Approve / reject / ignore actions:**
  - **Approve:** funds the line item, rejects the transaction's other proposals and marks it `reconciled`.
  - **Reject:** marks a proposal `rejected`; when no proposals remain, the transaction becomes `needs_review`.
  - **Ignore:** marks an unresolved credit `ignored`.
  - Actions that conflict with the current state return `409 Conflict`.
- **Funding updates with integrity guarantees:**
  - Approvals run in one database transaction with row locks. Remaining amounts are re-checked after locking.
  - A line item can never be funded twice, or beyond its called amount, even under concurrent requests.
  - One payment can never fund two line items. PostgreSQL constraints back these rules up.
- **Tenant isolation:** all queries are filtered by the selected organization. Another organization's records return `404`, not `403`.
- **Demo data:** `python manage.py seed_demo` creates two organizations, three capital calls and seven line items. It is safe to run repeatedly and never resets funding.

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, Django 5.2, Django REST Framework 3.16, psycopg 3, python-dotenv |
| Database | PostgreSQL 17 (Docker Compose), Django migrations |
| Frontend | React 19, Vite 7 (plain CSS, no UI framework) |
| Tests | Django test runner against PostgreSQL (backend only) |

## Architecture and project structure

The Django app keeps views thin; parsing, matching and reconciliation logic live in plain service modules. The React single-page app talks to the API through one small client, and the Vite dev server proxies `/api` to Django.

```text
capital-call-reconciliation/
├── compose.yaml                 PostgreSQL service (the only container)
├── .env.example                 template for the local .env (placeholders only)
├── backend/
│   ├── manage.py
│   ├── requirements.txt
│   ├── config/                  settings (environment variables), root URLs, WSGI
│   └── reconciliation/
│       ├── models.py            organizations, investors, capital calls, line items,
│       │                        bank transactions, matches + database constraints
│       ├── services/            csv_parser, importer, normalization, matching,
│       │                        actions (approve/reject/ignore), seed
│       ├── api/                 views, serializers, URLs, organization header resolution
│       ├── management/commands/seed_demo.py
│       ├── migrations/
│       └── tests/               PostgreSQL-backed tests; tests/data/ has the sample CSV
└── frontend/
    ├── vite.config.js           dev server on port 5173, proxies /api to Django
    └── src/
        ├── App.jsx              organization state, data loading, uploads, actions
        ├── api/client.js        fetch wrapper and endpoint helpers
        ├── utils/money.js       decimal-string money formatting (no floats)
        └── components/          switcher, CSV upload, import summary,
                                 transaction table, line-item panel, feedback
```

Design notes: approvals take row locks in a fixed order (bank transaction, then its matches, then the line item) and re-check state after locking. PostgreSQL constraints back up the funding, uniqueness and status rules.

## Prerequisites (Windows)

- Python 3.11 (developed with 3.11.9; other versions not tried)
- Node.js 20.19+ or 22.12+ (required by Vite 7; developed with 22.14.0) and npm
- Docker Desktop (runs PostgreSQL only)
- PowerShell; VS Code optional

The first database start downloads the `postgres:17` image (about 650 MB).

## Setup from a fresh clone

> These steps match the commands used during development, but a complete fresh-clone run has **not yet been verified end to end**.

From the repository root in PowerShell:

```powershell
# 1. Local settings: copy the template, then replace the placeholder values
Copy-Item .env.example .env
#    Set DJANGO_SECRET_KEY and POSTGRES_PASSWORD in .env (never commit .env)

# 2. Start PostgreSQL and wait until it is healthy
docker compose up -d --wait db

# 3. Backend
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver            # API on http://127.0.0.1:8000

# 4. Frontend (second terminal, from the repository root)
cd frontend
npm ci
npm run dev                           # http://localhost:5173
```

Open `http://localhost:5173`. A quick API check: `http://127.0.0.1:8000/api/health/`.

### Configuration

All settings come from environment variables. Django loads the root `.env` (real environment variables take precedence); `compose.yaml` reads the same file.

| Variable | Used by | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django | Required; a long random string |
| `DJANGO_DEBUG` | Django | `true` for local development; `false` if unset |
| `DJANGO_ALLOWED_HOSTS` | Django | Comma-separated host names |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Django, Compose | Database name and credentials (Compose creates them on first start) |
| `POSTGRES_HOST`, `POSTGRES_PORT` | Django (port also Compose) | Default `localhost:5432` |
| `API_PROXY_TARGET` | Vite dev server | Where `/api` is proxied; default `http://127.0.0.1:8000` |

Useful database commands:

- **Stop the database:** `docker compose stop db`. Data persists in the `fundflow_pgdata` volume.
- **Delete the data:** `docker compose down -v` removes FundFlow's volume and all its data.

## Running tests and builds

Backend tests (from `backend/`, with the virtual environment active and the database running):

```powershell
python manage.py test reconciliation
```

The tests run against PostgreSQL (Django creates and drops a temporary `test_<POSTGRES_DB>` database). They cover models and constraints, the seed command, CSV parsing and import, matching, the actions, listing endpoints, tenant isolation, and real concurrent approvals using separate database connections.

Other backend checks:

```powershell
python manage.py check
python manage.py makemigrations --check
```

Frontend production build (from `frontend/`):

```powershell
npm run build
```

There are **no automated frontend tests**. The frontend has been checked with production builds and manual browser testing; see *Known limitations* below for what remains unverified.

## API summary

**Common rules:**

- **Base path:** `/api/`. JSON throughout.
- **Organization header:** every endpoint except `health` and `organizations` requires `X-Organization: <slug>`.
  - Missing, empty or whitespace-only header → `400`.
  - Unknown organization (exact, case-sensitive slug) → `404`.
  - A record belonging to another organization, or an unknown or malformed ID → `404`.
- **Errors** use `{"detail": "..."}`.
- **Amounts** are decimal strings such as `"25000.00"`.
- **Lists** are plain arrays with no pagination.

| Method | Path | Header | Purpose |
|---|---|---|---|
| GET | `/api/health/` | No | Liveness: `{"status":"ok","database":"ok"}`, or `503` if the database is unavailable |
| GET | `/api/organizations/` | No | Organizations for the switcher: `[{slug, name}]` only |
| POST | `/api/imports/` | Yes | Upload a CSV (multipart field `file`). Returns `{imported_count, duplicate_count, duplicates[{line, reference, message}], errors[{line, message}]}`. `400` if the file is unusable (nothing saved); `409` on a concurrent duplicate-reference conflict (nothing saved) |
| GET | `/api/transactions/` | Yes | Transactions newest first (`date`, then id), with nested matches and reasons |
| GET | `/api/line-items/` | Yes | Line items with capital call, investor, reference, called / funded / remaining amounts and status |
| POST | `/api/matches/{id}/approve/` | Yes | Approve a proposed match. `409` if it is no longer proposed, the transaction is no longer actionable, or the amount exceeds the current remaining amount |
| POST | `/api/matches/{id}/reject/` | Yes | Reject a proposed match. `409` if it is not proposed |
| POST | `/api/transactions/{id}/ignore/` | Yes | Ignore a `suggested` or `needs_review` credit. `409` if it is reconciled, already ignored, or a debit |

The action endpoints take no request body and return the updated transaction. The Django admin is not exposed.

## CSV format

The file must be UTF-8 (a byte-order mark is accepted). The header row is line 1 and must contain these columns:

```text
date,description,amount,type,reference
```

**File-level rules:**

- **Header names** are trimmed and case-insensitive. Columns may be in any order, and extra columns are ignored.
- **A missing or repeated required column,** an empty file, or a non-UTF-8 file rejects the whole upload (`400`) and saves nothing.

**Row rules.** Each value is trimmed before it's validated.

| Column | Rule |
|---|---|
| `date` | Strictly `YYYY-MM-DD` and a real calendar date (`2026-04-31`, `2026-4-3` and `03/04/2026` are rejected) |
| `description` | Required |
| `amount` | A decimal number with an optional leading `-` and optional `,` thousands separators in groups of three (quote values containing commas, e.g. `"25,000.00"`). At most 2 decimal places and 12 integer digits. Zero, currency symbols, parentheses, `+` and exponents are rejected. Amounts are never rounded |
| `type` | `credit` or `debit`, in any letter case. Credits must be positive and debits negative |
| `reference` | The bank's transaction reference; required, at most 100 characters |

**How rows are handled:**

- **Blank rows** are skipped.
- **Invalid rows** are reported with their file line number and all of their problems; the other valid rows are still imported.
- **Debits** are stored as `ignored` and never matched.

## Demo workflow

A sample statement is included at `backend/reconciliation/tests/data/sample_bank_statement.csv`.

1. Run `python manage.py seed_demo`. This creates `northwind` (Northwind Ventures, six line items CC-1001…CC-1006) and `bluebird` (Bluebird Capital, CC-2001).
2. Open the app, select **Northwind Ventures**, and upload the sample CSV.
3. Expected import summary:
   - **11 imported**, including one debit;
   - **1 duplicate** (line 11, "same as line 2 in this file");
   - **2 row errors**: line 13 (invalid date `2026-04-31`) and line 14 (credit with a negative amount).
4. Expected suggestions:

   | Transaction | Result |
   |---|---|
   | Rahul Shah, `CC-1002` | `suggested` (reference) |
   | Haller Capital, 20,000.00 and 7,500.00 | each `suggested` to CC-1001 (partial payments) |
   | Patel Family Trust, 12,500.00 | `suggested` (name and exact amount) |
   | Meridian Partners | `suggested` (name and exact amount) |
   | Oakridge Holdings | `needs_review` with two proposals (CC-1005, CC-1006) |
   | Patel `CC-1003`, 13,000.00 | `needs_review`: overpayment, no match |
   | Unknown wire | `needs_review`: no candidate |
   | Cedar Grove (Bluebird's CC-2001) | `needs_review` for Northwind |
   | Payment dated before the calls were issued | `needs_review` |
   | Bank fee (debit) | `ignored` |

5. Approve the Rahul Shah match: CC-1002 shows 25,000.00 funded, 0.00 remaining, status **Funded**, and the transaction becomes **Reconciled**.
6. Approve one of the Oakridge proposals: the other is rejected automatically. Trying a stale action shows an "Already changed" (409) message, and the data refreshes.
7. Switch to **Bluebird Capital**: only Bluebird's data is shown. Uploading the same file there imports it independently, and CC-2001 is suggested.

These outcomes are covered by the backend tests and were observed when testing manually in the browser. Approvals change the data, so run `seed_demo` on a fresh database if you want to repeat the demo from the start (`docker compose down -v` deletes all FundFlow data).

## Known limitations and outstanding verification

- **No authentication:** the `X-Organization` header selects a tenant; it is not a security boundary. Login and roles are out of scope.
- **No pagination:** list endpoints return every row for the organization.
- **Literal reference matching:** a reference is matched as a substring of the normalised description, as the brief specifies, so `CC1001` also matches inside `CC10010`.
- **Suggestions are calculated once, at import:** rejecting a match does not re-run matching.
- **Abandoned uploads and actions still complete:** switching organization while an upload or action is running does not cancel it on the server; its result is just not shown for the newly selected organization.
- **No import history:** the import summary is returned once and not stored.
- **No automated frontend tests:** the frontend was checked with builds and manual browser testing only.
- **Not implemented:** deployment and the optional Airtable integration.
- **Not yet verified:**
  - a complete fresh-clone setup run;
  - the 409-refresh path since the last frontend change.
