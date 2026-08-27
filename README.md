# ReStock AI

Agentic inventory procurement for merchants. An AI agent watches stock, forecasts
demand, and recommends what to buy from whom — then deterministic backend code
validates the recommendation, a human authorises the spend, and a RazorpayX
payout pays the supplier.

The governing principle:

> **The LLM reasons and recommends. Deterministic backend code validates and
> executes. A human authorises spending.**

| Document | What is in it |
| --- | --- |
| [PROJECT.md](PROJECT.md) | Why the project exists, trust model, milestones, definition of done |
| [docs/architecture.md](docs/architecture.md) | Layers, database, state machine, idempotency, concurrency |
| [docs/api.md](docs/api.md) | Every endpoint, error codes, a worked example, frontend notes |
| [docs/payment-flow.md](docs/payment-flow.md) | RazorpayX contract, failure modes, verification status |

---

## Status

Backend **complete**. 314 tests passing. Frontend deliberately not started.

| Milestone | Status |
| --- | --- |
| 1 — Foundation (config, DB, models, Alembic, seed, products) | Done |
| 2 — Inventory (low-stock detector, audit service) | Done |
| 3 — Payment plumbing (RazorpayX, idempotency, webhooks) | Done |
| 4 — Approval (proposals, human gate, guardrails) | Done |
| 5 — AI (forecast agent, supplier agent, validation) | Done |
| 6 — Full integration (happy / failed / reversed proven) | Done |
| 7 — Basic frontend | Not started |

**Two things are NOT VERIFIED against live services:**

* **RazorpayX** — every request and response is tested against the documented
  contract and against local endpoints speaking that contract, but nothing has
  touched `api.razorpay.com`. Seed fund-account ids are placeholders.
* **The LLM provider** — the real client has run over HTTP against an
  OpenAI-compatible local endpoint, not against OpenAI.

See [docs/payment-flow.md §9](docs/payment-flow.md) for exactly what is and is not
proven, and how to verify against RazorpayX Test Mode.

There is also **no authentication** on any endpoint, including approval. Out of
scope for the MVP, and the reason this is not deployable as-is.

---

## Quick start

Requirements: **Python 3.11+**. Nothing external is needed to run the tests.

```bash
cd backend

python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt         # macOS / Linux

cp .env.example .env          # then fill in values (see below)

.venv/Scripts/python.exe -m alembic upgrade head    # create the schema
.venv/Scripts/python.exe -m app.seed.seed           # load demo data
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

* API docs: <http://127.0.0.1:8000/docs>
* Health: <http://127.0.0.1:8000/health>

Tests:

```bash
cd backend
.venv/Scripts/python.exe -m pytest
```

`.env` is gitignored. Nothing in this repository contains a credential.

---

## Configuration

Full annotated list in [backend/.env.example](backend/.env.example). What each
group unlocks:

| Group | Without it |
| --- | --- |
| `DATABASE_URL` | Defaults to `backend/restock_ai.db` (SQLite) |
| `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL` | Proposals return **503**. No recommendation is invented. |
| `RAZORPAY_KEY_ID`, `_KEY_SECRET`, `_ACCOUNT_NUMBER` | Approval returns **503**. No payment is simulated. |
| `RAZORPAY_WEBHOOK_SECRET` | Every webhook is refused with **401**. Fails closed. |
| `MAX_ORDER_SPEND_INR`, `MAX_DAILY_SPEND_INR`, `MAX_REORDER_QUANTITY` | Defaults 10,000 / 25,000 / 500 |
| `SPEND_DAY_TIMEZONE` | Defaults `Asia/Kolkata`; defines "today" for the daily cap |

`GET /health` reports which integrations are configured, as booleans only:

```json
{ "status": "ok", "database": "up",
  "integrations": { "razorpayx_payouts_configured": false,
                    "razorpayx_webhooks_configured": false,
                    "llm_configured": false } }
```

### LLM setup

Any OpenAI-compatible endpoint works — set `OPENAI_BASE_URL`. There is
deliberately **no** fake provider selectable at runtime: a stubbed recommendation
in production would be indistinguishable from a real one in the audit trail.

### RazorpayX setup (Test Mode)

1. Dashboard → **Test Mode** → generate API keys.
2. Create a Contact, then a Fund Account, and note its real `fa_…` id.
3. Replace the placeholder ids in `backend/app/seed/data.py` (or update the
   `suppliers` rows directly). RazorpayX rejects payouts to fund accounts that do
   not exist.
4. Fill `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_ACCOUNT_NUMBER`.

### Webhook setup

1. Expose `/api/webhooks/razorpayx` publicly (a tunnel, for local development).
2. Dashboard → Settings → Webhooks → subscribe to `payout.processed`,
   `payout.failed`, `payout.reversed`.
3. Put the webhook secret in `RAZORPAY_WEBHOOK_SECRET`.

---

## Database

Alembic owns the schema. SQLite is the local default; the same migrations run on
PostgreSQL.

```bash
cd backend
.venv/Scripts/python.exe -m alembic upgrade head    # create / update
.venv/Scripts/python.exe -m alembic check           # fail if models drifted
.venv/Scripts/python.exe -m alembic downgrade -1    # migrations are reversible
```

PostgreSQL instead:

```
DATABASE_URL=postgresql+psycopg://restock:restock@localhost:5432/restock_ai
```

Seed flags:

* `--reset-stock` — restore seeded stock, discarding workflow changes
* `--create-tables` — build tables straight from the models, bypassing Alembic
  (throwaway databases only)

The seed is idempotent: re-running adds nothing and will not clobber stock changed
by a real workflow.

### Scheduled sweep

```bash
.venv/Scripts/python.exe -m app.jobs.inventory_check
```

Calls the same service function as `POST /api/inventory/check`. It stops at
detection — it does **not** create proposals, because an unattended process must
never be able to spend money.

---

## Docker

**NOT VERIFIED** — the Docker daemon was unavailable where this was written. The
compose file parses (`docker compose config`) and nothing beyond that has been
exercised.

```bash
cp backend/.env.example backend/.env    # required; compose reads it
docker compose up --build               # db + migrate + backend
docker compose run --rm seed            # load demo data
docker compose run --rm inventory-check # scheduled sweep
```

`migrate` runs Alembic once and exits; `backend` waits for it. Migrations are
deliberately not part of the backend's own start-up so replicas cannot race.

---

## API

14 endpoints. Full reference in [docs/api.md](docs/api.md).

```
GET  /health

GET  /api/products                        GET  /api/products/{id}
POST /api/inventory/check                 GET  /api/inventory/low-stock
POST /api/proposals/product/{id}          GET  /api/proposals
                                          GET  /api/proposals/{order_id}
GET  /api/orders                          GET  /api/orders/{id}
POST /api/orders/{id}/approve             ← the human approval gate
GET  /api/audit                           GET  /api/audit/{order_id}
POST /api/webhooks/razorpayx
```

### The flow, end to end

```bash
# 1. What needs reordering? Deterministic, no LLM.
curl -X POST localhost:8000/api/inventory/check
# → Milk 42/60, Coffee Beans 12/25

# 2. Ask the agents. Two LLM calls. Nothing is spent.
curl -X POST localhost:8000/api/proposals/product/1
# → 201, order 1: 75 litres from Amul Dairy Direct, INR 3600.00,
#   with both agents' reasoning included

# 3. A human authorises. NO REQUEST BODY — an id is all a client may send.
curl -X POST localhost:8000/api/orders/1/approve
# → 200, status "approved" (NOT "paid"), payout_id pout_…
#   Stock has NOT changed.

# 4. RazorpayX calls back with a signed payout.processed
# → order becomes "paid", Milk 42 → 117

# 5. Send the same webhook again
# → 200 duplicate, Milk still 117

# 6. Why did this order happen?
curl localhost:8000/api/audit/1
# → PROPOSAL_CREATED → ORDER_APPROVED → RAZORPAY_PAYOUT_REQUESTED
#   → RAZORPAY_PAYOUT_CREATED → PAYOUT_PROCESSED → INVENTORY_UPDATED
```

---

## Demo dataset

Four products, 28 days of deterministic sales history each, nine suppliers where
the cheaper option is always the slower one — so the supplier agent faces a real
trade-off rather than a sort.

| Product | Stock / threshold | Deliberately demonstrates |
| --- | --- | --- |
| Milk | 42 / 60 → low | the clean happy path (75 × ₹48 = ₹3,600) |
| Coffee Beans | 12 / 25 → low | the per-order spend cap being breached |
| Rice | 320 / 150 → healthy | the "product is not low stock" refusal |
| Cooking Oil | 95 / 80 → healthy | a supplier with no RazorpayX fund account |

---

## What a client may never send

The backend accepts an order id and an approval action. It does **not** accept —
and ignores if injected — an `amount`, `unit_price`, `supplier_price`,
`delivery_days`, `status`, `payout_id`, or `current_stock`. There is a test that
posts all of them at the approval endpoint and asserts they have no effect.

There is also no `PATCH /orders/{id}/status` and no writable audit endpoint.

---

## Conventions worth knowing before reading the code

* **Money is integer paise** (`amount_paise`, `price_per_unit_paise`). Floats and
  SQLite `NUMERIC` both lose precision, and RazorpayX wants paise anyway — so no
  conversion happens at the moment money moves. Rupees appear only at the API
  boundary, as JSON *strings*.
* **A successful approval is not a payment.** The order stays `approved` until a
  signature-verified `payout.processed` webhook arrives.
* **Nothing writes stock directly.** `inventory_service.apply_received_stock` is
  the only writer, callable only from webhook processing. A test enforces that.
* **Order status is assigned in exactly one place**, `order_service.transition`. A
  test enforces that too.
* **Timestamps are UTC-aware everywhere**, via a `TypeDecorator`, because SQLite
  otherwise discards `tzinfo`.
* **Errors always look the same:** `{"error": {"code", "message", "details?"}}`.
  Branch on `code`.

---

## Repository layout

```
backend/
├── app/
│   ├── main.py            app factory, /health, error handlers
│   ├── api/               HTTP routers only
│   ├── services/          business rules, transactions, state transitions
│   ├── agents/            LLM only — no side effects, no payment access
│   │   └── prompts/       forecast_prompt.txt, supplier_prompt.txt
│   ├── models/            SQLAlchemy
│   ├── schemas/           Pydantic wire contracts
│   ├── core/              config, database, money, limits, errors, security
│   ├── jobs/              scheduled entry points
│   └── seed/              demo dataset
├── alembic/versions/      2 migrations
├── tests/                 314 tests
├── requirements.txt  .env.example  Dockerfile  pytest.ini  alembic.ini
docs/                      architecture · api · payment-flow
frontend/                  empty placeholder, intentionally
```
