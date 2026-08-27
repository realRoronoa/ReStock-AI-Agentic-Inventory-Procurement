# ReStock AI — Project Document

The durable reference for *why* this system is shaped the way it is. Written for
an engineer who has never seen it.

Every statement here describes behaviour that exists and is tested. Where
something is unverified or deliberately not built, it says so.

Deeper detail lives in [docs/architecture.md](docs/architecture.md),
[docs/api.md](docs/api.md) and [docs/payment-flow.md](docs/payment-flow.md).

---

## 1. Why this project exists

A small merchant runs out of stock because nobody noticed a product dipping, or
over-orders because nobody looked at the trend. The decision is genuinely
judgement-shaped — *how much will sell in the next few days, and is the cheaper
supplier worth waiting six days for* — which is what an LLM is good at framing.

It is also a decision whose output is a bank transfer, which is what an LLM must
never be trusted with.

ReStock AI splits the problem exactly along that seam.

---

## 2. The core principle

> **The LLM reasons and recommends. Deterministic backend code validates and
> executes. A human authorises spending.**

| Concern | Owner |
| --- | --- |
| Noticing low stock | deterministic code (a comparison) |
| Estimating demand | LLM — recommendation only |
| Choosing a supplier | LLM — recommendation only |
| Validating those answers | deterministic code |
| Computing the amount | deterministic code, from database prices |
| Authorising the spend | **a human** |
| Moving the money | deterministic code (RazorpayX, idempotent) |
| Deciding payment succeeded | RazorpayX webhook, signature-verified |
| Updating stock | deterministic code, exactly once, after settlement |

Three consequences, each enforced rather than asserted:

1. **The LLM has no side effects.** `app/agents/` imports nothing that can write
   to the database or call a payment API. Enforced by
   `tests/test_architecture.py`, which reads the import graph.
2. **LLM output is untrusted input.** Parsed with strict Pydantic, then
   re-validated against the database. A named supplier must exist *and* belong to
   the product in question.
3. **No amount comes from outside the backend.** Not from the LLM, not from the
   frontend. `amount = quantity × supplier.price_per_unit`, read from the
   database at the moment of use.

## 3. Trust model

| Component | Trusted? | Why |
| --- | --- | --- |
| Database | Yes | The single authority for price, stock, and state |
| Backend service code | Yes | Where every invariant is enforced |
| LLM | **No** | May hallucinate quantities, suppliers, and prices |
| Frontend | **No** | Anyone can craft an HTTP request |
| Incoming webhook | **No, until verified** | Anyone can POST to a public URL |
| RazorpayX after verification | Yes | The signature proves origin |

---

## 4. Architecture

```
                        ┌────────────────────┐
                        │  Dashboard/client  │   UNTRUSTED
                        └─────────┬──────────┘
                                  ▼
                        ┌────────────────────┐
                        │      FastAPI       │   app/api/ — HTTP only
                        └─────────┬──────────┘
             ┌────────────────────┼────────────────────┐
             ▼                    ▼                    ▼
       Inventory             Proposal              Orders
        Service               Service              Service      app/services/
             │              ┌─────┴─────┐              │
             │              ▼           ▼              │
             │          Forecast     Supplier          │        app/agents/
             │            Agent        Agent           │        LLM ONLY
             │              └─────┬─────┘              │
             │                    ▼                    │
             │          ┌──────────────────┐           │
             │          │    VALIDATION    │           │  deterministic
             │          └────────┬─────────┘           │
             └───────────────────┼─────────────────────┘
                                 ▼
                       HUMAN APPROVAL GATE            no bypass, any amount
                                 ▼
                     DETERMINISTIC PAYMENT SERVICE    no LLM import, ever
                                 ▼
                             RazorpayX
                                 ▼
                              WEBHOOK
                                 ▼
                      SIGNATURE VERIFICATION          raw bytes, constant-time
                                 ▼
                       PAYMENT STATE MACHINE
                      ┌──────────┴──────────┐
                      ▼                     ▼
              ORDER + INVENTORY         AUDIT LOG     one transaction
```

### Layer rules

| Directory | Contains | Must not |
| --- | --- | --- |
| `api/` | Request parsing, status codes, response shaping | Business rules, external calls |
| `services/` | All business rules, state transitions, transactions | HTTP concerns |
| `agents/` | LLM interaction; returns validated Pydantic objects | Import services, models, database, limits, or payment code |
| `models/` | SQLAlchemy persistence | Wire contracts |
| `schemas/` | Pydantic wire contracts | Be written to directly from a client payload |
| `core/` | Config, database, money, guardrails, errors, crypto | — |
| `jobs/` | Scheduled entry points reusing service functions | Duplicate business logic |

---

## 5. User flow

1. Merchant opens the dashboard and sees stock levels.
2. Merchant (or the scheduled job) triggers `POST /api/inventory/check`.
3. Low-stock products are listed. No LLM involved.
4. For a low-stock product, the merchant requests a proposal.
5. The system returns a recommended quantity, a recommended supplier, the
   authoritative total, and **both agents' reasoning**. Nothing has been spent.
6. The merchant reads the reasoning and explicitly approves — or does nothing.
7. On approval the backend re-validates everything and requests a payout.
8. The order reads `approved` / awaiting confirmation.
9. On a verified `payout.processed`, stock increases and the order reads `paid`.
10. Every step is reconstructable from `GET /api/audit/{order_id}`.

---

## 6. Data flow

```
sales_history ──┐
products ───────┼──▶ forecast agent ──▶ quantity + reasoning
                │                            │
                │                            ▼
                │                      validate bounds
                │                            │
suppliers ──────┼──▶ supplier agent ──▶ supplier_id + reasoning
                │                            │
                │                            ▼
                │                    re-read supplier from DB,
                │                    confirm it supplies THIS product
                │                            │
                │                            ▼
                └──────────────────▶ amount = qty × db_price
                                             │
                                             ▼
                                    quantity / order / daily caps
                                             │
                                             ▼
                                    orders (status=proposed)
                                             │
                                    ── human approval ──
                                             ▼
                                    revalidate, recompute amount
                                             │
                                             ▼
                                    RazorpayX payout + payout_id
                                             │
                                    ── verified webhook ──
                                             ▼
                                orders.status, products.current_stock,
                                audit_log, webhook_events  (ONE transaction)
```

---

## 7. Database model

Six tables. Full DDL detail and rationale in
[docs/architecture.md §6](docs/architecture.md).

```
products ──┬──▶ sales_history   (CASCADE, UNIQUE product+date)
           ├──▶ suppliers       (CASCADE, UNIQUE product+name)
           └──▶ orders          (RESTRICT)
suppliers ────▶ orders          (RESTRICT)
orders    ──┬──▶ audit_log      (SET NULL)
            └──▶ webhook_events (SET NULL)
```

Choices worth knowing before reading the code:

* **Foreign keys are real.** SQLite ignores them without
  `PRAGMA foreign_keys=ON` per connection, which the engine sets on every
  connect. A test proves it is applied.
* **`orders` FKs are `RESTRICT`.** A product or supplier in a purchase order must
  not be deletable out from under it.
* **`audit_log.related_order_id` is `SET NULL`.** History outlives its subject.
* **`razorpay_payout_id`, `payout_idempotency_key`, `webhook_events.event_id` are
  all `UNIQUE`.** Each is a specific correctness guarantee, not hygiene.
* **Enums are `VARCHAR` + `CHECK`,** so one migration runs on SQLite and
  PostgreSQL.
* **`audit_log.action` is plain `VARCHAR`.** An append-only event vocabulary
  grows; a `CHECK` would force a migration per new event type.
* **Timestamps are UTC-aware** via a `TypeDecorator`, because SQLite silently
  drops `tzinfo` and would emit offset-less timestamps that clients read as local
  time.
* **Money is integer paise** — §9.

---

## 8. Order state machine

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
   └────────── payout.reversed ──────────▶ REVERSED ◀┘
```

Declared once in `ALLOWED_TRANSITIONS` (`app/models/order.py`) and applied
through exactly one function, `order_service.transition`. A test asserts no other
module assigns `order.status`.

`transition` separates two outcomes that are easy to conflate: a **forbidden**
transition (`FAILED → PAID`) raises and surfaces as `409`, while a transition to
the state the order is **already in** is an idempotent no-op — which is what makes
a redelivered webhook safe.

### No `payout_initiated` status

The vocabulary stays at five values; the payout lifecycle lives in three columns
that express more than a sixth status could:

| Column state | Meaning |
| --- | --- |
| `payout_attempted_at IS NULL` | no payout request sent |
| `attempted_at` set, `payout_id IS NULL` | request sent, **outcome unknown** |
| both set | payout created, awaiting webhook |

That middle state is the one that needs care, and a boolean status could not
express it.

---

## 9. Money

Every monetary value is an **integer number of paise**.

1. Integer arithmetic cannot drift; `quantity × price` is exact.
2. SQLite has no real `DECIMAL`, so a `NUMERIC` column round-trips through a C
   double — silent precision loss on the one value that must be exact.
3. RazorpayX takes `amount` as an integer in the smallest currency unit, so no
   conversion happens at the moment money moves.

Rupees appear only at the API boundary, as `Decimal` serialised to a JSON
**string** so no client parses money into a float. Helpers live in
`app/core/money.py`.

This is a deliberate departure from the original specification's rupee
`price_per_unit` / `amount` fields; the field names gained a `_paise` suffix.

---

## 10. LLM boundary

The agents' entire output surface:

```python
ForecastResult    { recommended_quantity: int, reasoning: str }
SupplierSelection { supplier_id: int,          reasoning: str }
```

Both `strict=True`, so `{"recommended_quantity": "75"}` is **rejected**, not
coerced.

| The LLM cannot | Prevented by |
| --- | --- |
| Create or alter a supplier | It returns an integer id, nothing else |
| Change a price or delivery time | Both re-read from the supplier row at use |
| Set an order amount | Computed in `supplier_service` from DB values |
| Name a supplier for another product | `validate_supplier_for_product` compares `product_id` |
| Name a supplier it was not offered | Offered-id check before DB lookup |
| Approve an order | No approval path reachable from `agents/` |
| Create a payout | `payment_service` not importable from `agents/` |
| See a credential | `assert_no_secrets` on both prompt halves |
| Learn the spend caps | Limits never appear in a prompt (tested) |

That last one is subtle: a model told the per-order cap could size a
recommendation to sit just under it, so the forecast would reflect the budget
rather than the demand — defeating the guardrail it appeared to respect.

Provider swap surface is one file, `app/agents/llm_client.py`; `OPENAI_BASE_URL`
points at anything speaking OpenAI chat-completions.

**There is no fake LLM selectable at runtime.** A stubbed recommendation in
production would be indistinguishable from a real one in the audit trail. Fakes
exist only in `tests/fakes.py`.

---

## 11. Payment boundary

`app/services/payment_service.py` is the only module that talks to RazorpayX.

* No LLM code can import it (tested, both directions).
* If credentials are missing it **raises**. There is no code path in which it
  reports a payout that did not happen.
* The key secret is passed to httpx as Basic auth credentials and appears in no
  log, audit row, exception message, or request body.

Full detail: [docs/payment-flow.md](docs/payment-flow.md).

---

## 12. Guardrails

| Setting | Default | Checked against |
| --- | --- | --- |
| `MAX_REORDER_QUANTITY` | 500 | the recommended quantity |
| `MAX_ORDER_SPEND_INR` | 10,000 | one order's total |
| `MAX_DAILY_SPEND_INR` | 25,000 | the day's committed spend |

Limits are **inclusive** — exactly at the limit passes, one paisa over fails,
tested from both sides. Checked **twice**: at proposal, and again at approval
immediately before money moves, because a proposal can be stale.

Also revalidated at approval: the order is still `proposed`; the supplier still
exists, still supplies this product, and still has a fund account; and the amount
is recomputed from the supplier's *current* price (the recomputed figure wins).

**Daily spend policy.** `APPROVED` and `PAID` count — approval is where the
business commits. `PROPOSED` does not (a suggestion; counting it would let drafts
starve the budget). `FAILED` and `REVERSED` do not (the money never left or came
back; charging them would lock a merchant out of retrying after a bank rejection
they did not cause). Attributed by `approved_at`, on a day boundary following
`SPEND_DAY_TIMEZONE` (default `Asia/Kolkata`) — a UTC day would roll over at
05:30 IST and split one trading day in two.

---

## 13. Approval gate

`POST /api/orders/{order_id}/approve` is the only route to spending money.

There is deliberately **no** auto-approve, agent-approve, approve-if-under-a-
threshold, or background auto-pay. A ₹1 order needs the same explicit human call
as a ₹10,000 one. The request body is empty by design: a client supplies an order
id and nothing else.

**Concurrency.** Two compare-and-swap claims — conditional `UPDATE ... WHERE`
with `rowcount` as arbiter — mean the database picks the winner, so a
double-click, two tabs, or a client retry cannot produce two payouts. The
authorisation is committed *before* RazorpayX is contacted, so a process death
mid-call leaves a resumable order rather than a duplicate payment.

---

## 14. Webhook flow

```
raw bytes
  → verify HMAC-SHA256 over the RAW body   ← before the JSON is parsed
  → parse JSON
  → extract event type + payout id
  → claim the delivery (UNIQUE event id; duplicates replay stored outcome)
  → resolve the local order
  → apply the transition (state machine refuses illegal moves)
  → increment stock, only if processed
  → audit
  → ONE commit
```

Verification is first so an unverified body is never trusted — not even for a
lookup, which would let a stranger probe which payout ids exist. The signature
covers the raw bytes because re-serialising changes key order and whitespace; a
test proves that breaks verification. Comparison is constant-time. Missing
secret, missing header and wrong signature all produce the same `401`.

**Idempotency has two independent layers:**

1. `webhook_events.event_id` is `UNIQUE`, keyed on Razorpay's
   `X-Razorpay-Event-Id` (unique per event, stable across its retries). A
   redelivery replays the *stored* outcome rather than recomputing it, so a caller
   cannot get `200` then `404` for the same event.
2. The state machine refuses a second transition into `PAID`. So a duplicate
   arriving with a *fresh* event id still cannot increment stock.

Layer 2 protects the money; layer 1 keeps the trail honest and responses
consistent.

---

## 15. Failure flows

| Event | Order | Inventory | Follow-up |
| --- | --- | --- | --- |
| `payout.processed` | `paid` | `+= quantity`, once | none |
| `payout.failed` / `.rejected` / `.cancelled` | `failed` | unchanged | merchant chooses next step |
| `payout.reversed` | `reversed` | **not adjusted** | human investigation |
| Malformed / invalid LLM output | no order created | unchanged | error surfaced, failure audited |
| Guardrail breach | no payout | unchanged | recoverable; retry later |
| Missing RazorpayX credentials | no payout | unchanged | fails loudly; never faked |
| Payout request times out | stays `approved`, outcome unknown | unchanged | **not retried**; manual reconciliation |
| Forged / unsigned webhook | unchanged | unchanged | `401`, nothing recorded |

**Failed payouts are never silently retried.** The cause is usually something only
a human can fix — wrong bank details, a frozen account, an unfunded source
account. The merchant creates a **new proposal** instead, which gets its own order
id, approval, payout, idempotency key, and audit trail. Tested end to end.

**A reversal never unwinds stock.** Money came back; that is not evidence the
goods did not arrive. Subtracting stock would be the system guessing about the
physical world.

**A timeout is not a failure.** A lost response may mean the payout succeeded, so
it raises a distinct `504`, leaves the order in the "outcome unknown" state, and
refuses later approval attempts with `409 PAYOUT_OUTCOME_UNKNOWN` naming the
`reference_id` to look up in the RazorpayX dashboard.

---

## 16. Audit trail

Every state-changing action is logged with an actor (`agent` / `human` /
`system`), an action name, free-text reasoning, an optional order reference, and
JSON metadata. Agent events store the model's reasoning **verbatim**, so a human
can later see what it actually argued rather than a paraphrase — and reasoning is
never regenerated for display, so historical decisions stay historically
accurate.

The trail reconstructs:

```
trigger → AI reasoning → proposal → human approval
        → payment request → payment result → inventory change
```

A completed happy path reads:

```
PROPOSAL_CREATED → ORDER_APPROVED → RAZORPAY_PAYOUT_REQUESTED
  → RAZORPAY_PAYOUT_CREATED → WEBHOOK_RECEIVED → PAYOUT_PROCESSED
  → INVENTORY_UPDATED
```

Append-only: the API exposes no way to create, alter, or delete an entry.
Metadata is recursively scrubbed of secret-shaped keys and of any literal
configured secret value before it is written.

---

## 17. Development milestones

All complete except the frontend.

| # | Milestone | Status |
| --- | --- | --- |
| 1 | Foundation: config, database, models, Alembic, seed, health, product APIs | Done |
| 2 | Inventory: low-stock detector, endpoints, audit service | Done |
| 3 | Payment plumbing: RazorpayX client, idempotency, webhooks, state machine | Done |
| 4 | Approval: proposals, human gate, guardrails | Done |
| 5 | AI: forecast agent, supplier agent, structured output, validation | Done |
| 6 | Full integration: happy, failed and reversed paths proven end to end | Done |
| 7 | Basic frontend | **Not started** (deliberately) |

Payment plumbing was built **before** the LLM, so the money path was understood
before anything non-deterministic touched it.

---

## 18. Definition of done

Proven, not merely coded — see `tests/test_integration_flow.py` and the live-run
evidence in [docs/payment-flow.md §9](docs/payment-flow.md):

1. A product exists and is below its threshold. ✅
2. The detector identifies it. ✅
3. Sales history is loaded. ✅
4. The forecast agent returns a valid recommendation. ✅
5. The backend validates it. ✅
6. The supplier agent selects a supplier. ✅
7. The backend validates that supplier belongs to the product. ✅
8. The backend computes the authoritative amount from the database. ✅
9. Spend limits pass. ✅
10. A `proposed` order is created. ✅
11. A human explicitly approves. ✅
12. The backend re-validates everything. ✅
13. A RazorpayX payout is created. ✅ *(against local stub endpoints; see §20)*
14. The payout id is stored. ✅
15. A webhook arrives and its signature is verified. ✅
16. `payout.processed` is handled. ✅
17. The order becomes `paid`. ✅
18. Inventory increases **exactly once**. ✅
19. The audit trail contains the entire chain. ✅

Plus the failure paths: `payout.failed` leaves inventory unchanged and the order
`failed`; `payout.reversed` marks the order `reversed` without adding stock; a
duplicate `payout.processed` does not increment stock again. All ✅.

---

## 19. Testing

314 tests. No network access, no credentials required, ~5s.

External services are replaced two ways: a **Protocol plus an injected double**
(`tests/fakes.py`) for orchestration tests, and **`httpx.MockTransport`** for the
real provider classes so actual request-building and response-interpretation code
runs and exact bytes are asserted.

`tests/conftest.py` sets environment variables *before* importing any `app`
module, so a populated local `.env` — possibly holding real credentials — cannot
influence a test run.

`tests/test_architecture.py` turns the architectural claims in this document into
executable assertions: the agent import graph, the single inventory writer, the
single status assignment site, prompt secret-freedom, and the absence of
interpolated SQL.

---

## 20. Known limitations

* **RazorpayX is NOT VERIFIED against the live service.** All request/response
  handling is tested against the documented contract and against local endpoints
  speaking that contract, but nothing has touched `api.razorpay.com`. Seed fund
  account ids are placeholders. See
  [docs/payment-flow.md §9](docs/payment-flow.md) for the steps to verify.
* **The LLM is NOT VERIFIED against a live provider.** Same shape of caveat: the
  real client has been exercised over HTTP against an OpenAI-compatible local
  endpoint, not against OpenAI.
* **No authentication.** Every endpoint is unauthenticated, including the
  approval gate. Out of scope per the specification — and the reason this is not
  deployable as-is.
* **No automatic payment reconciliation.** Resolving an "outcome unknown" order
  is manual. The idempotency key is persisted so a deliberate retry is safe.
* **Docker config is unbuilt.** The daemon was unavailable; the compose file
  parses and nothing more.
* **Low-stock audit suppression keys off `products.updated_at`**, so an unrelated
  product edit can re-arm a `LOW_STOCK_DETECTED` event.
* **`audit_service.latest_action_timestamps` filters in Python**, not SQL,
  because JSON access syntax differs across dialects. Fine at this scale.
* **No pagination metadata.** List endpoints take `limit`/`offset` but return a
  bare array with no total count.

---

## 21. Out of scope

Authentication, role-based access, real supplier integrations, notifications,
multi-currency, refunds, a chat interface, statistical forecasting models, and
multi-supplier bidding. The architecture leaves room for them; the MVP does not
build them.
