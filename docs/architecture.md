# Architecture

How ReStock AI is put together, and why. Written for an engineer who has never
seen the codebase.

---

## 1. The one idea

> **The LLM reasons and recommends. Deterministic backend code validates and
> executes. A human authorises spending.**

Everything else here is a consequence of that sentence. The problem being solved
is genuinely judgement-shaped — *how much will sell in the next few days, and is
the cheaper supplier worth waiting six days for* — which is what LLMs are good
at. It is also a problem whose output is a bank transfer, which is what LLMs must
never be trusted with.

So the work is split along that seam:

| Concern | Owner | Why |
| --- | --- | --- |
| Noticing low stock | deterministic code | It is a comparison, not a judgement |
| Estimating demand | LLM | Genuine judgement over noisy data |
| Choosing a supplier | LLM | A real price-versus-speed trade-off |
| Validating those answers | deterministic code | Model output is untrusted input |
| Computing the amount to pay | deterministic code | From database prices only |
| Authorising the spend | a human | Irreversible, so it needs intent |
| Moving the money | deterministic code | RazorpayX, idempotent |
| Deciding payment succeeded | RazorpayX webhook | Only the bank knows |
| Updating stock | deterministic code | Exactly once, after settlement |

---

## 2. Request flow

```
                        ┌────────────────────┐
                        │  Dashboard/client  │   UNTRUSTED
                        └─────────┬──────────┘
                                  │  HTTP
                                  ▼
                        ┌────────────────────┐
                        │      FastAPI       │   app/api/
                        │  routers only —    │   status codes, schemas,
                        │  no business rules │   raw webhook bytes
                        └─────────┬──────────┘
                                  │
             ┌────────────────────┼────────────────────┐
             ▼                    ▼                    ▼
       Inventory             Proposal              Orders
        Service               Service              Service      app/services/
             │                    │                    │
             │              ┌─────┴─────┐              │
             │              ▼           ▼              │
             │          Forecast     Supplier          │        app/agents/
             │            Agent        Agent           │        LLM ONLY,
             │              │           │              │        no side effects
             │              └─────┬─────┘              │
             │                    ▼                    │
             │          ┌──────────────────┐           │
             │          │    VALIDATION    │           │  quantity bounds,
             │          │  (deterministic) │           │  supplier ownership,
             │          └────────┬─────────┘           │  authoritative price,
             │                   │                     │  spend guardrails
             └───────────────────┼─────────────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │   HUMAN APPROVAL GATE   │  POST /orders/{id}/approve
                    │  no bypass at any value │  explicit, mandatory
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  DETERMINISTIC PAYMENT  │  app/services/payment_service
                    │        SERVICE          │  no LLM import, ever
                    └────────────┬────────────┘
                                 ▼
                          ┌─────────────┐
                          │  RazorpayX  │  POST /v1/payouts
                          └──────┬──────┘  + X-Payout-Idempotency
                                 │
                                 │  (async, minutes later)
                                 ▼
                          ┌─────────────┐
                          │   WEBHOOK   │  POST /api/webhooks/razorpayx
                          └──────┬──────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  SIGNATURE VERIFICATION │  HMAC-SHA256 over RAW bytes,
                    │   before parsing JSON   │  constant-time compare
                    └────────────┬────────────┘
                                 ▼
                    ┌─────────────────────────┐
                    │  PAYMENT STATE MACHINE  │  ALLOWED_TRANSITIONS
                    └────────────┬────────────┘
                    ┌────────────┴────────────┐
                    ▼                         ▼
            ┌───────────────┐         ┌───────────────┐
            │   INVENTORY   │         │   AUDIT LOG   │   one transaction
            │  (once only)  │         │  (append-only)│
            └───────────────┘         └───────────────┘
```

---

## 3. Layers and their rules

```
backend/app/
├── api/          HTTP only. Parse, delegate, shape a response.
├── services/     All business rules and state transitions. Owns transactions.
├── agents/       LLM interaction. Returns validated data. No side effects.
├── models/       SQLAlchemy persistence.
├── schemas/      Pydantic wire contracts, separate from models.
├── core/         Config, database, money, guardrails, errors, crypto.
├── jobs/         Scheduled entry points that reuse service functions.
└── seed/         Demo data.
```

**`api/` contains no business rules.** No status-code decisions for business
failures either — a service raises an `AppError` subclass that already knows its
own status code. This is why the same rule cannot behave differently over HTTP
than it does in a test.

**`services/` owns transactions.** `get_db` only guarantees the session is
closed. A service decides what commits together, which is how "order is PAID"
and "stock went up" can never disagree.

**`agents/` cannot touch anything.** No import of `app.models`,
`app.core.database`, `app.core.limits`, or any service. Enforced by
`tests/test_architecture.py`, which reads the import graph.

**`schemas/` is separate from `models/` on purpose.** No client payload is ever
written straight to a table, and the wire contract can change without a
migration.

---

## 4. The LLM boundary, concretely

The agents' entire output surface is two Pydantic objects:

```python
ForecastResult    { recommended_quantity: int, reasoning: str }
SupplierSelection { supplier_id: int,          reasoning: str }
```

Both are declared `strict=True`, so `{"recommended_quantity": "75"}` is
**rejected**, not coerced. A model returning the wrong type has misunderstood the
contract; quietly repairing it means tolerating drift in a value that becomes a
bank transfer.

What the LLM structurally cannot do:

| Cannot | Prevented by |
| --- | --- |
| Create or alter a supplier | It returns an integer id, nothing else |
| Change a price | Price is re-read from the supplier row at point of use |
| Set an order amount | `amount = quantity × db_price`, computed in `supplier_service` |
| Name a supplier for another product | `validate_supplier_for_product` re-reads and compares `product_id` |
| Name a supplier it was not offered | Offered-id set check before database lookup |
| Approve an order | No approval code path is reachable from `agents/` |
| Create a payout | `payment_service` is not importable from `agents/` |
| See a credential | Prompts are asserted secret-free by `assert_no_secrets` |
| Learn the spend caps | Limits never appear in a prompt (tested) |

That last one is subtle and deliberate. A model told the per-order cap could size
a recommendation to sit just under it — the forecast would then reflect the
budget rather than the demand, defeating the guardrail it appeared to respect.

Provider swap surface is one file: `app/agents/llm_client.py`. `OPENAI_BASE_URL`
points at anything speaking OpenAI chat-completions.

---

## 5. Money

Every monetary value is an **integer number of paise**, in columns suffixed
`_paise`.

1. Integer arithmetic cannot drift. `quantity × price` is exact.
2. SQLite has no real `DECIMAL`, so a `NUMERIC` column round-trips through a C
   double there — silent precision loss on the one value that must be exact.
3. The RazorpayX Payouts API takes `amount` as an integer in the smallest
   currency unit, so **no conversion happens at the moment money moves**.

Rupees appear only at the API boundary, as `Decimal` serialised to a JSON
*string* so no client parses money into a float. Responses expose both, e.g.
`amount_paise: 360000` and `amount: "3600.00"`.

`app/core/money.py` holds the only two conversion functions.

---

## 6. Database

```
products                     sales_history
  id                           id
  name             UNIQUE      product_id  ──FK CASCADE──▶ products.id
  current_stock    CHECK >=0   date
  unit                         quantity_sold  CHECK >=0
  reorder_threshold CHECK>=0   created_at
  created_at                   UNIQUE (product_id, date)
  updated_at                   INDEX  (product_id, date)

suppliers                    orders
  id                           id
  product_id ──CASCADE──▶      product_id  ──FK RESTRICT──▶ products.id
  name                         supplier_id ──FK RESTRICT──▶ suppliers.id
  price_per_unit_paise         quantity            CHECK >0
             CHECK >0          amount_paise        CHECK >0
  delivery_days CHECK >=0      status              (5 values)
  razorpay_fund_account_id     razorpay_payout_id       UNIQUE
  UNIQUE (product_id, name)    payout_idempotency_key   UNIQUE
                               payout_attempted_at
audit_log                      payout_status
  id                           failure_reason
  timestamp        INDEX       approved_at
  actor            (3 values)  forecast_reasoning
  action           INDEX       supplier_reasoning
  reasoning_text               unit_price_paise_at_proposal
  related_order_id ─SET NULL─▶ razorpay_order_id / razorpay_payment_id (unused)
  metadata  JSON/JSONB
  INDEX (action, timestamp)   webhook_events
                                id
                                event_id     UNIQUE  ◀── idempotency guarantee
                                event_type   INDEX
                                payout_id    INDEX
                                payout_status
                                related_order_id ──SET NULL──▶ orders.id
                                outcome
                                received_at
                                payload  JSON/JSONB
```

### Choices worth understanding

**Foreign keys are real.** SQLite ignores them unless `PRAGMA foreign_keys=ON`
is issued *per connection*, which `app/core/database.py` does on every connect.
Without it, every FK above would be decoration. There is a test that proves the
pragma is applied.

**`orders` FKs are `RESTRICT`, not `CASCADE`.** A product or supplier involved in
a purchase order must not be deletable out from under it.

**`audit_log.related_order_id` is `SET NULL`.** History outlives its subject.

**`razorpay_payout_id` is `UNIQUE`.** One payout can never attach to two local
orders.

**`payout_idempotency_key` is `UNIQUE`.** Two orders can never share a key.

**`webhook_events.event_id` is `UNIQUE`.** This is the database-level guarantee
that a redelivered webhook cannot be processed twice — see §8.

**Enums are `VARCHAR` + `CHECK`, not native PostgreSQL enum types.** The same
migration runs on SQLite and PostgreSQL, and adding a status later needs a
constraint rewrite rather than type surgery. Note that this requires
`create_constraint=True` — SQLAlchemy's `Enum(native_enum=False)` defaults to
*no* constraint, which leaves the column a bare `VARCHAR` validated only in
Python. Alembic autogenerate does not compare CHECK constraints either, so
`tests/test_migrations.py` asserts they are present and enforced.

**`audit_log.action` is a plain `VARCHAR`.** The canonical names live in
`AuditAction`. A DB-level `CHECK` would force a migration for every new event
type and would make an old row unreadable if a name were retired.

**Timestamps are UTC-aware**, enforced by a `TypeDecorator` (`UTCDateTime`),
because SQLite silently discards `tzinfo` and would otherwise emit offset-less
timestamps that every client reads as local time.

**Money as integer paise** — §5.

---

## 7. Order state machine

```
PROPOSED
   │  human approval
   ▼
APPROVED ─────── payout created, id stored ───────┐
   │                                              │
   │ payout.processed    payout.failed            │ payout.reversed
   ▼                     payout.rejected          │
 PAID                    payout.cancelled         │
   │                          ▼                   │
   │                       FAILED                 │
   │                                              │
   └────────── payout.reversed ──────────▶ REVERSED ◀┘
```

Declared once, in `ALLOWED_TRANSITIONS` (`app/models/order.py`), and applied
through exactly one function, `order_service.transition`. A test asserts no other
module assigns `order.status`.

`transition` distinguishes two outcomes that are easy to conflate:

* a transition the machine **forbids** (`FAILED → PAID`) raises — it is a
  contradiction, surfaced as `409`;
* a transition to the state the order is **already in** returns `False` — an
  idempotent no-op, which is what makes a redelivered webhook safe.

### Why there is no `payout_initiated` status

The status vocabulary is fixed at five values. The payout lifecycle is carried by
three columns instead, which express more than a sixth status could:

| Column state | Meaning |
| --- | --- |
| `payout_attempted_at IS NULL` | no payout request sent yet |
| `attempted_at` set, `payout_id IS NULL` | request sent, **outcome unknown** |
| `attempted_at` and `payout_id` both set | payout created, awaiting webhook |

That middle row is the one a boolean status could not express, and it is exactly
the state that needs care (§9).

---

## 8. Idempotency, in two independent layers

Inventory increasing twice is the most expensive bug this system could have, so
it is prevented twice over.

**Layer 1 — `webhook_events.event_id` UNIQUE.** Razorpay's `X-Razorpay-Event-Id`
is unique per event and stable across its own retry attempts, which makes it the
correct dedupe key. A redelivery hits the constraint and is answered by
*replaying the stored outcome* rather than recomputing it, so a caller cannot get
`200` on one attempt and `404` on the next for the same event.

Deduplicating on `payout_id` would be wrong: one payout legitimately produces
several events (`queued` → `initiated` → `processed`). Deduplicating on
`(payout_id, event_type)` would also be wrong: `payout.updated` can arrive more
than once for genuinely different updates.

**Layer 2 — the state machine.** `PAID` cannot become `PAID`. So even a duplicate
arriving with a *fresh* event id — a different delivery carrying the same fact —
cannot increment stock again. Layer 2 is the one that actually protects the
money; layer 1 keeps the audit trail honest and the responses consistent.

**Payout creation** has its own protection: a UUID idempotency key, generated
once and **committed before** the RazorpayX call, sent as `X-Payout-Idempotency`.
Any retry — user, proxy, or reconciliation — reuses the stored key, and RazorpayX
collapses it onto the original payout.

---

## 9. Concurrency at the approval gate

Two approvals in flight at once — a double-click, two tabs, a client retrying
after a timeout — must not produce two payouts. Disabling a button does not solve
this; the backend must.

Two compare-and-swap claims, each a conditional `UPDATE ... WHERE`, with
`rowcount` as the arbiter so the *database* picks the winner rather than
application logic reading and then writing:

```sql
-- 1. approval claim
UPDATE orders SET status='approved', approved_at=…, payout_idempotency_key=…
 WHERE id=:id AND status='proposed';

-- 2. payout claim (separate, so a resumed approval cannot double-send)
UPDATE orders SET payout_attempted_at=…
 WHERE id=:id AND payout_attempted_at IS NULL AND status='approved';
```

Both work identically on SQLite and PostgreSQL and need no `SELECT … FOR UPDATE`
(which SQLite lacks).

The authorisation is **committed before** RazorpayX is contacted. If the process
dies mid-call, the approval and its idempotency key survive, so the payout can be
resumed with the same key rather than duplicated.

### The uncertain outcome

A timeout is **not** a failure. The request may well have been accepted.
`PaymentTimeoutError` is therefore a distinct type (`504`), and the order is
deliberately left `APPROVED` with `payout_attempted_at` set and no payout id.
Nothing retries automatically, and a later approval attempt returns `409
PAYOUT_OUTCOME_UNKNOWN` naming the `reference_id` to look up in the RazorpayX
dashboard.

**Known limitation:** there is no automatic reconciliation job. Resolving an
unknown outcome is a manual step today. The stored idempotency key is what makes
a deliberate retry safe when someone does it.

---

## 10. Webhook processing order

The ordering of these steps is a security property, not a style choice.

```
raw bytes
  → verify HMAC-SHA256 signature      ← BEFORE the JSON is even parsed
  → parse JSON
  → extract event type + payout id
  → claim the delivery (UNIQUE event id; duplicates replay)
  → resolve the local order
  → apply the transition (state machine refuses illegal moves)
  → increment stock, only if processed
  → audit
  → ONE commit
```

Verification comes first so that an unverified body is never trusted — not even
to perform a lookup, which would otherwise let a stranger probe which payout ids
exist.

The signature is computed over the **raw bytes**. Parsing and re-serialising
would change key order, whitespace, and unicode escaping, and an authentic
signature would stop matching. There is a test that proves re-serialisation
breaks it.

### Event classification

| Events | Effect |
| --- | --- |
| `payout.processed` | → `PAID`, stock += quantity, once |
| `payout.failed` / `.rejected` / `.cancelled` | → `FAILED`, stock unchanged |
| `payout.reversed` | → `REVERSED`, stock **not** adjusted |
| `payout.queued` / `.initiated` / `.pending` / `.processing` / `.updated` | recorded, **no transition** |
| `payout.downtime.*`, anything unrecognised | acknowledged, ignored |

`rejected` and `cancelled` mapping to `FAILED` is a documented extension beyond
the three required events: both mean no money moved, and leaving the order
`APPROVED` forever would have a merchant waiting for a settlement that is never
coming.

A test asserts the terminal and intermediate sets do not overlap, and that
`payout.processed` is the *only* event mapping to `PAID`.

---

## 11. Guardrails

Three configurable ceilings in `app/core/limits.py`, enforced in code the LLM
cannot read or reach:

| Setting | Default | Checked against |
| --- | --- | --- |
| `MAX_REORDER_QUANTITY` | 500 | the recommended quantity |
| `MAX_ORDER_SPEND_INR` | 10,000 | one order's total |
| `MAX_DAILY_SPEND_INR` | 25,000 | the day's committed spend |

Limits are **inclusive**: exactly at the limit passes, one paisa over fails.
Tested from both sides.

Checked **twice** — at proposal, and again at approval immediately before money
moves, because a proposal can be stale.

### Daily spend policy

Counted: `APPROVED` and `PAID`. Approval is where the business commits, so an
approved-but-unsettled order has money in motion.

Not counted: `PROPOSED` (a suggestion; counting it would let drafts starve the
budget), `FAILED` and `REVERSED` (the money never left or came back — charging
them would lock a merchant out of retrying after a bank rejection they did not
cause).

Attributed by `approved_at`, not `created_at`. The day boundary follows
`SPEND_DAY_TIMEZONE` (default `Asia/Kolkata`), because a UTC day would roll over
at 05:30 IST and split one trading day's spending across two budgets.

---

## 12. Error contract

One shape, everywhere:

```json
{ "error": { "code": "ORDER_NOT_PROPOSED",
             "message": "Only proposed orders can be approved.",
             "details": { "order_id": 7, "status": "paid" } } }
```

Four handlers in `main.py` cover the whole surface — domain errors,
`HTTPException`, request validation, and anything unexpected — so no endpoint can
return an inconsistent shape. Branch on `code`, never on message text.

Status codes: `400` invalid business operation, `401` bad webhook signature,
`404` not found, `409` invalid state or conflict, `422` validation / guardrail /
rejected model output, `502` upstream failure, `503` not configured, `504` payout
timeout with unknown outcome.

Unexpected errors return a `request_id` and nothing else; the cause is logged
server-side. No stack trace, DSN, SQL, or credential ever reaches a client.

---

## 13. Secret hygiene

* Secrets enter the process in exactly one place, `app/core/config.py`.
* Never logged. `redact_headers` masks credential-bearing headers;
  `audit_service` recursively scrubs secret-shaped keys *and* any literal
  configured secret value from audit metadata before it is written.
* Never in a prompt. `assert_no_secrets` checks both prompt halves before an LLM
  call, and a test proves the assertion actually fires.
* Never in an error message. `PaymentNotConfiguredError` names the missing
  *variables*, not their values.
* The RazorpayX key secret is passed to httpx as Basic auth credentials and
  appears nowhere else. A test asserts it is absent from the request body.
* `/health` reports integration readiness as **booleans** only.

---

## 14. Testing strategy

314 tests, no network access, no credentials required.

| File | Focus |
| --- | --- |
| `test_products.py` | catalogue endpoints, DB constraints, FK enforcement |
| `test_money.py` | exact arithmetic; the float drift being avoided |
| `test_migrations.py` | migrations run; schema matches models; UTC round-trip |
| `test_seed.py` | dataset shape, determinism, idempotency |
| `test_inventory.py` | the `<` boundary from three angles; audit suppression |
| `test_forecast.py` | malformed / wrong-type / out-of-range model output |
| `test_supplier.py` | hallucinated and wrong-product supplier ids |
| `test_guardrails.py` | every limit from both sides of the boundary |
| `test_payment.py` | RazorpayX contract via `MockTransport`; timeout semantics |
| `test_webhooks.py` | signature, event classification, duplicates |
| `test_approval.py` | state gate, revalidation, concurrent approval |
| `test_proposals.py` | pipeline order, refusals before LLM spend |
| `test_integration_flow.py` | the three end-to-end flows |
| `test_architecture.py` | the boundaries above, as executable assertions |

External services are replaced two ways:

* **Protocol + injected double** (`tests/fakes.py`) for orchestration tests.
  Fakes live in `tests/` and no environment variable can select them, so no
  configuration mistake can put a fake payout in a running server.
* **`httpx.MockTransport`** for the real provider classes, so actual
  request-building and response-interpretation code runs and the exact bytes are
  asserted on.

`tests/conftest.py` sets environment variables *before* importing any `app`
module, so a populated local `.env` — possibly holding real credentials — cannot
influence a test run.

---

## 15. Deliberate departures from the original specification

**Money as integer paise** rather than rupee `price_per_unit` / `amount` fields.
Rationale in §5. Field names gained a `_paise` suffix; rupee values are derived
at the API boundary.

**`payout.rejected` and `payout.cancelled` map to `FAILED`**, beyond the three
required events. Both mean no money moved; ignoring them would leave an order
`APPROVED` indefinitely.

**Files added beyond the specified tree**, each for a stated reason:
`core/money.py` (money representation is cross-cutting), `core/errors.py` (the
single API error contract), `services/order_service.py` (centralised state
transitions, required by the spec's own instruction not to scatter them),
`models/webhook_event.py` (webhook idempotency), `agents/llm_client.py` (the
provider-swap seam), `schemas/audit.py` and `schemas/error.py` (both schemas the
spec requires), plus `tests/test_architecture.py`, `test_money.py`,
`test_migrations.py`, `test_seed.py` and `tests/fakes.py`.

---

## 16. Known limitations

* **No automatic payment reconciliation.** An unknown payout outcome (§9) needs a
  human to check the RazorpayX dashboard. The idempotency key is persisted so a
  deliberate retry is safe, but nothing sweeps for these automatically.
* **No authentication.** Every endpoint is unauthenticated, including the
  approval gate. Out of scope per the specification, and the reason this is not
  deployable as-is.
* **RazorpayX not verified against the live service.** See
  [payment-flow.md](payment-flow.md) §9.
* **Low-stock audit suppression keys off `products.updated_at`**, so an unrelated
  product edit can re-arm a `LOW_STOCK_DETECTED` event. Harmless; noted for
  honesty.
* **`audit_service.latest_action_timestamps` filters in Python**, not SQL,
  because JSON access syntax differs across dialects. Fine at this scale; would
  need a dialect-specific query or a dedicated column at volume.
* **Docker config is unbuilt.** The daemon was unavailable; the compose file
  parses and nothing more.
