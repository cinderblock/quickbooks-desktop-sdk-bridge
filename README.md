# QuickBooks Desktop SDK Bridge

Bridges a RESTy HTTP API to the QuickBooks Desktop SDK.

QuickBooks Desktop only exposes data through a Windows COM interface (QBXMLRP2), which means you need a Windows process running in the same user session as QB. This project wraps that COM interface in a local HTTP server so any language, any machine on your network, can read and write QuickBooks data with simple REST-style calls.

## Quick start

**Requirements:** Windows, QuickBooks Desktop 2021+ running, Python 3.12+, [uv](https://docs.astral.sh/uv/)

```sh
# Clone and install
git clone https://github.com/cinderblock/quickbooks-desktop-sdk-bridge.git
cd quickbooks-desktop-sdk-bridge
uv sync

# Create an API key
uv run python -m qb_bridge.cli create-key "My Key"
# Save the key it prints — it won't be shown again

# Start the server (QuickBooks Desktop must be open)
uv run python -m qb_bridge

# Try it
curl -H "X-API-Key: qbb_..." http://localhost:8743/api/v1/customers?max_returned=5
```

The server binds to port **8743** and accepts connections from private network IPs (10.x, 172.16-31.x, 192.168.x, 127.x).

## API overview

Interactive docs are available at `http://localhost:8743/docs` (Swagger UI) and `http://localhost:8743/redoc`.

### Entities

Every QuickBooks entity gets a full set of REST endpoints:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/{entities}` | List with filters and pagination |
| `GET` | `/api/v1/{entities}/{id}` | Get by ListID or TxnID |
| `POST` | `/api/v1/{entities}` | Create |
| `PUT` | `/api/v1/{entities}/{id}` | Update (requires EditSequence) |
| `DELETE` | `/api/v1/{entities}/{id}` | Delete |

**Supported entities:** accounts, customers, vendors, employees, items (service, inventory, non-inventory), invoices, bills, checks, deposits, payments, journal-entries, estimates, sales-receipts, credit-memos, purchase-orders, bill-payments, classes, terms, sales-reps, payment-methods

### Query parameters (list endpoints)

| Parameter | Description | Example |
|-----------|-------------|---------|
| `max_returned` | Limit results (1-5000, default 100) | `?max_returned=50` |
| `name` | Filter by name. List entities: contains match on the entity name. Transactions: contains match on RefNumber | `?name=Acme` |
| `active` | Filter by status: `ActiveOnly`, `InactiveOnly`, `All`. **List entities only** — ignored for transactions (they have no active status) | `?active=All` |
| `modified_after` | Only records modified after this datetime | `?modified_after=2024-01-01T00:00:00` |
| `from_date` / `to_date` | **Transactions only.** Filter by `TxnDate` range (YYYY-MM-DD) | `?from_date=2026-01-01&to_date=2026-12-31` |
| `entity_name` | **Transactions only.** Filter by the transaction's customer/job/vendor, including sub-jobs. Exact `FullName` (as returned by `/customers` or `/vendors`) | `?entity_name=Acme Corp:Phase 1` |
| `iterator_id` | Paginate through large result sets — see below | `?iterator_id=Start` |

> **Transactions are returned oldest-first** and there is no newest-first option in qbXML. To reach recent records in a table with more than `max_returned` rows, use `from_date`/`to_date` to window by date, or use `iterator_id` to page through all of them.

> **Strict parameters.** The API never silently ignores a query parameter. An unknown
> parameter (typo or unsupported option) returns `400 UNKNOWN_QUERY_PARAM`. A parameter
> that doesn't apply to the target entity — `active` on a transaction, or
> `from_date`/`to_date`/`entity_name` on a list entity — returns `400 PARAM_NOT_APPLICABLE`.
> Both responses list what *is* accepted, so a wrong call fails immediately instead of
> appearing to work.

#### Filtering by job — important limitation

`entity_name` matches a transaction's **top-level** entity:
- For customer-facing transactions (invoices, estimates, sales receipts, credit memos, payments) that's the **customer/job**, so `entity_name` works as expected.
- For **checks and bills**, the top-level entity is the **payee (a vendor)**, *not* the job. The job linkage on costs lives at the **line-item level** (`CustomerRef` on each expense/item line), which qbXML transaction queries **cannot** filter on.

So a query like "all costs charged to job X" (the classic job-costing question) cannot be answered by a transaction list query. Use a **report** with the `entity` filter instead — see [Job costing](#reports) (e.g. `reports/profit-and-loss-detail?entity=...` or `reports/general-ledger?entity=...`).

### Iterator pagination

When a table has more rows than `max_returned`, an iterator is the only way to
reach the records past the first page. **Pass `iterator_id=Start` to begin** —
a plain query (no `iterator_id`) returns only the first `max_returned` rows and
no cursor, so the rest are unreachable.

```sh
# Page 1 — pass iterator_id=Start to open the cursor
curl "http://localhost:8743/api/v1/checks?max_returned=50&iterator_id=Start"
# Response: {"data": [...], "meta": {"count": 50, "iterator_id": "{guid}", "remaining": 5728}}

# Page 2+ — pass the iterator_id from the previous response
curl "http://localhost:8743/api/v1/checks?max_returned=50&iterator_id={guid}"
# Repeat until meta.remaining is 0
```

Not all entities support iterators (it depends on the qbXML DTD). Entities that don't will return a `400 ITERATOR_NOT_SUPPORTED` error with a helpful message.

### Transaction line items

GET requests for transaction entities (invoices, bills, checks, etc.) automatically include line item detail — `ExpenseLineRet`, `ItemLineRet`, etc.

### Reports

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/reports` | List available reports |
| `GET` | `/api/v1/reports/{slug}` | Run a report |

**Available reports:** profit-and-loss, balance-sheet, trial-balance, general-ledger, ar/ap-aging (summary & detail), sales-by-customer, sales-by-item, customer/vendor-balance, inventory-valuation, open-invoices, unpaid-bills, income-by-customer, expense-by-vendor

**Report parameters:**

| Parameter | Description | Example |
|-----------|-------------|---------|
| `from_date` | Start date (YYYY-MM-DD) | `?from_date=2024-01-01` |
| `to_date` | End date | `?to_date=2024-12-31` |
| `date_macro` | Preset range | `?date_macro=ThisYear` |
| `entity` | Scope to a customer/job/vendor, including sub-jobs. Exact `FullName` from `/customers` or `/vendors` | `?entity=Acme Corp:Phase 1` |
| `basis` | `Accrual` or `Cash` | `?basis=Accrual` |
| `summarize_by` | `Month`, `Quarter`, `Year`, `TotalOnly` | `?summarize_by=Month` |
| `format` | `json` (default), `csv`, `pdf` | `?format=csv` |

**Job costing:** the `entity` filter is how you answer "everything for job X" — including
costs on checks/bills that a transaction list query can't reach (those are filtered by
line-level `CustomerRef`, which only reports can see). Use it on a detail report:

```sh
# All income and expense detail for a job (and its sub-jobs)
curl "http://localhost:8743/api/v1/reports/profit-and-loss-detail?entity=Acme Corp&from_date=2026-01-01&to_date=2026-12-31"

# Every transaction touching the job (general ledger, filtered)
curl "http://localhost:8743/api/v1/reports/general-ledger?entity=Acme Corp:Phase 1&from_date=2026-01-01&to_date=2026-12-31"
```

`entity` only applies to reports that have an entity dimension (detail reports, customer/income
reports). Passing it to a report without one (e.g. `balance-sheet`) returns an error.

### Other endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/` | No | API root with links |
| `GET` | `/api/v1/health` | No | Health check |
| `GET` | `/api/v1/status` | Yes | Connection state, uptime |
| `GET` | `/api/v1/entities` | Yes | List all entity types and supported operations |
| `GET` | `/api/v1/company` | Yes | Company info from QB |

## Authentication

Every `/api/v1/*` request requires an `X-API-Key` header.

```sh
# Create keys with different permission levels
uv run python -m qb_bridge.cli create-key "Read Only" --preset readonly
uv run python -m qb_bridge.cli create-key "Full Access" --preset admin
uv run python -m qb_bridge.cli create-key "Custom" --permissions '{"Customer":["read","write"],"Invoice":["read"]}'

# Manage keys
uv run python -m qb_bridge.cli list-keys
uv run python -m qb_bridge.cli revoke-key 3
uv run python -m qb_bridge.cli set-permissions 3 readonly
```

**Permission presets:**
- `readonly` — list and get all entities
- `readwrite` — full CRUD on all entities
- `admin` — everything

**Fine-grained permissions** use QB entity names as keys:
```json
{
  "*": ["read"],
  "Customer": ["read", "write"],
  "Invoice": ["read", "insert"],
  "Report": ["read"]
}
```

Operations: `read` (list+get), `write` (create+update+delete), `insert` (create only), `admin` (everything), or explicit: `list`, `get`, `create`, `update`, `delete`.

## Configuration

All settings are configurable via environment variables (prefix `QBB_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `QBB_DATA_DIR` | `C:\ProgramData\QBBridge` | Database and log directory |
| `QBB_PORT` | `8743` | Listen port |
| `QBB_HOST` | `0.0.0.0` | Listen address |
| `QBB_COMPANY_FILE` | *(empty)* | Path to .qbw file, or empty for whatever QB has open |
| `QBB_IDLE_TIMEOUT` | `120` | Seconds before releasing the QB COM session |
| `QBB_AUTO_LAUNCH_QB` | `false` | Start QuickBooks Desktop automatically if not running |
| `QBB_REQUEST_TIMEOUT` | `60` | Seconds before a QB request times out |
| `QBB_LOG_LEVEL` | `INFO` | Logging level |

## Running as a background service

The server needs to run in the same Windows user session as QuickBooks Desktop (COM requirement). Two options:

### Option A: Scheduled task (recommended)

Runs in your login session, auto-starts on login, auto-restarts on failure:

```sh
uv run python install_task.py
```

### Option B: Windows service

Uses [NSSM](https://nssm.cc/) for service management. Download `nssm.exe` into the project directory, then:

```sh
install_service.bat
```

## How it works

```
┌──────────────────────────────────────────────────────┐
│  Your code (any language, any machine on the LAN)    │
│  curl / Python / Node / etc.                         │
└────────────────────┬─────────────────────────────────┘
                     │ HTTP (port 8743)
┌────────────────────▼─────────────────────────────────┐
│  FastAPI server (uvicorn)                            │
│  - Auth, IP filtering, rate limiting                 │
│  - Builds qbXML request strings                      │
│  - Parses qbXML responses to JSON                    │
└────────────────────┬─────────────────────────────────┘
                     │ JSON-over-stdio
┌────────────────────▼─────────────────────────────────┐
│  Worker subprocess (COM apartment)                   │
│  - QBXMLRP2.RequestProcessor via pywin32             │
│  - ProcessRequest(ticket, qbxml) → response XML      │
└────────────────────┬─────────────────────────────────┘
                     │ COM / QBXMLRP2
┌────────────────────▼─────────────────────────────────┐
│  QuickBooks Desktop                                  │
│  - Company file (.qbw)                               │
└──────────────────────────────────────────────────────┘
```

The server talks to QuickBooks through a subprocess to avoid COM apartment threading issues with asyncio. The subprocess holds the COM session and is killed after idle timeout to release the company file lock (so you can use QB Desktop normally between API calls).

## Development

```sh
# Install dev dependencies
uv sync --group dev

# Run tests (no QuickBooks or company file needed)
uv run pytest tests/ -v

# Lint
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

Tests use a `FakeQBSession` that captures qbXML requests and returns canned responses, so the full HTTP-to-XML pipeline is tested without QuickBooks.

## License

MIT
