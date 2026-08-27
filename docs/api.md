# API reference

Base URL in development: `http://127.0.0.1:8000`
Interactive docs: `/docs` · OpenAPI schema: `/openapi.json`

There is **no authentication**. Out of scope for the MVP, and the reason this is
not deployable as-is.

---

## Conventions

### Money

Every amount appears twice:

| Field | Type | Use |
| --- | --- | --- |
| `*_paise` | integer | Exact. Do arithmetic on this. |
| rupee field | JSON **string** | Display. `"3600.00"`. Never parse as a float. |

```json
{ "amount_paise": 360000, "amount": "3600.00" }
```

### Timestamps

ISO 8601, always UTC, always with an offset: `2026-08-27T09:53:52.832639Z`.

### Errors

One shape from every endpoint:

```json
{
  "error": {
    "code": "ORDER_NOT_PROPOSED",
    "message": "Order 7 is paid. Only proposed orders can be approved.",
    "details": { "order_id": 7, "status": "paid" }
  }
}
```

**Branch on `code`.** Messages are for humans and may change. `details` is
present when there is structured context. `request_id` is present on `500`/`503`
— quote it when reporting a problem.

| Status | Meaning |
| --- | --- |
| 400 | Valid request, invalid business operation |
| 401 | Webhook signature missing, wrong, or unverifiable |
| 404 | Resource not found |
| 409 | Resource is not in a state that allows this |
| 422 | Request validation, spend guardrail, or rejected model output |
| 502 | Upstream service (LLM or RazorpayX) failed |
| 503 | A required integration is not configured on this server |
| 504 | Payout timed out; **outcome unknown**, not retried |

### What a client may never send

The backend accepts an order id and an approval action. It does **not** accept —
and will ignore if injected — an `amount`, `unit_price`, `supplier_price`,
`delivery_days`, `status`, `payout_id`, or `current_stock`. All of those are
computed by the backend or reported by a verified webhook.

---

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness, database, integration readiness |
| GET | `/api/products` | List products |
| GET | `/api/products/{id}` | Product with suppliers and sales history |
| POST | `/api/inventory/check` | Low-stock sweep (writes audit) |
| GET | `/api/inventory/low-stock` | Same detection, read-only |
| POST | `/api/proposals/product/{id}` | Generate a reorder proposal |
| GET | `/api/proposals` | Proposals awaiting a decision |
| GET | `/api/proposals/{order_id}` | One proposal (summary) |
| GET | `/api/orders` | List orders |
| GET | `/api/orders/{id}` | One order, in full |
| POST | `/api/orders/{id}/approve` | **Human approval gate** |
| GET | `/api/audit` | Audit trail |
| GET | `/api/audit/{order_id}` | One order's decision chain |
| POST | `/api/webhooks/razorpayx` | RazorpayX payout webhook |

---

### `GET /health`

```json
{
  "status": "ok",
  "app": "ReStock AI",
  "environment": "development",
  "version": "1.0.0",
  "database": "up",
  "integrations": {
    "razorpayx_payouts_configured": true,
    "razorpayx_webhooks_configured": true,
    "llm_configured": true
  }
}
```

`503` with `"status": "degraded"` if the database is unreachable. The
`integrations` values are booleans only — no credential is ever exposed. Use them
to tell whether a real payout or a real forecast is possible on this server.

---

### `GET /api/products`

| Query | Type | Notes |
| --- | --- | --- |
| `low_stock` | bool | `true` = below threshold, `false` = at or above, omit = all |

```json
[
  {
    "id": 1,
    "name": "Milk",
    "current_stock": 42,
    "unit": "litre",
    "reorder_threshold": 60,
    "is_low_stock": true,
    "created_at": "2026-08-27T09:53:52.832639Z",
    "updated_at": "2026-08-27T09:53:52.832639Z"
  }
]
```

`is_low_stock` is computed, never stored: `current_stock < reorder_threshold`
(strict — equality is **not** low stock).

Sorted by name.

### `GET /api/products/{product_id}`

| Query | Type | Default | Notes |
| --- | --- | --- | --- |
| `sales_days` | int 1–365 | 28 | Most recent sales days to return |

Adds `suppliers` (cheapest first) and `recent_sales` (newest first).

```json
{
  "id": 1, "name": "Milk", "current_stock": 42, "unit": "litre",
  "reorder_threshold": 60, "is_low_stock": true,
  "suppliers": [
    {
      "id": 2, "product_id": 1, "name": "Krishna Dairy Co-op",
      "price_per_unit_paise": 4400, "price_per_unit": "44.00",
      "delivery_days": 6,
      "razorpay_fund_account_id": "fa_TESTMILKKRSH01",
      "has_fund_account": true
    }
  ],
  "recent_sales": [{ "date": "2026-08-26", "quantity_sold": 17 }]
}
```

`has_fund_account: false` means that supplier **cannot be paid** and will not be
offered to the supplier agent. Errors: `404`.

There is deliberately no product-mutation endpoint. Stock is changed only by a
verified payout webhook.

---

### `POST /api/inventory/check`

Deterministic sweep. No LLM. Writes audit events, hence `POST`.

```json
{
  "low_stock_products": [
    { "id": 1, "name": "Milk", "current_stock": 42, "reorder_threshold": 60,
      "unit": "litre", "status": "low_stock", "shortfall": 18 }
  ],
  "products_checked": 4,
  "low_stock_count": 2,
  "newly_detected_product_ids": [1, 2],
  "checked_at": "2026-08-27T09:53:52.832639Z"
}
```

`newly_detected_product_ids` lists products that produced a *new*
`LOW_STOCK_DETECTED` audit event. A product already recorded as low since its
last change is still reported in `low_stock_products` but is not re-audited — so
polling this endpoint cannot flood the trail. A per-sweep
`INVENTORY_CHECK_COMPLETED` event always records that the check ran, so the
absence of a detection event is never ambiguous.

### `GET /api/inventory/low-stock`

Same detection rule, same service function, writes nothing. Safe to poll.
Returns the `low_stock_products` array alone.

---

### `POST /api/proposals/product/{product_id}`

Runs the full pipeline and creates an order in `proposed` state. **No money
moves.** Costs two LLM calls.

Pipeline: verify low stock → load 28 days of sales → forecast agent → validate
quantity → load payable suppliers → supplier agent → re-read and validate the
supplier from the database → compute `quantity × db_price` → apply guardrails →
persist.

`201 Created`:

```json
{
  "order_id": 1,
  "status": "proposed",

  "product_id": 1, "product_name": "Milk", "unit": "litre",
  "current_stock": 42, "reorder_threshold": 60, "shortfall": 18,

  "recommended_quantity": 75,
  "forecast_reasoning": "Daily sales average about 20 litres with a weekend uplift. With a six-day worst-case lead time and 42 litres on hand, 75 litres covers the window and rebuilds a small buffer above the 60-litre threshold.",
  "observed_daily_average": 21.71,
  "history_days": 28,
  "forecast_provider": "llm-forecast",

  "supplier_id": 1, "supplier_name": "Amul Dairy Direct", "delivery_days": 2,
  "supplier_reasoning": "Stock is well below threshold, so the two-day delivery is worth the higher unit price; waiting six days risks a stockout that costs more than the premium.",
  "supplier_provider": "llm-supplier",
  "options_considered": 2,

  "unit_price_paise": 4800, "unit_price": "48.00",
  "total_amount_paise": 360000, "total_amount": "3600.00",

  "created_at": "2026-08-27T09:54:10.112233Z",
  "next_step": "Nothing has been spent. Call POST /api/orders/{order_id}/approve to authorise this purchase. There is no automatic approval."
}
```

Errors:

| Status | Code | When |
| --- | --- | --- |
| 400 | `PRODUCT_NOT_LOW_STOCK` | At or above threshold. Checked **before** any LLM call. |
| 400 | `NO_SUPPLIERS` | No suppliers at all |
| 400 | `NO_SALES_HISTORY` | Nothing to forecast from |
| 400 | `SUPPLIER_NO_FUND_ACCOUNT` | Suppliers exist but none is payable |
| 404 | `PRODUCT_NOT_FOUND` | — |
| 422 | `FORECAST_INVALID` | Malformed, wrong-typed, or non-positive quantity |
| 422 | `SUPPLIER_SELECTION_INVALID` | Hallucinated id, or a supplier for another product |
| 422 | `QUANTITY_LIMIT_EXCEEDED` | Over `MAX_REORDER_QUANTITY` |
| 422 | `ORDER_SPEND_LIMIT_EXCEEDED` | Over `MAX_ORDER_SPEND_INR` |
| 422 | `DAILY_SPEND_LIMIT_EXCEEDED` | Would breach `MAX_DAILY_SPEND_INR` |
| 502 | `FORECAST_UNAVAILABLE` / `SUPPLIER_SELECTION_UNAVAILABLE` | Model unreachable |
| 503 | `AGENT_NOT_CONFIGURED` | No `OPENAI_API_KEY` on this server |

Every failure creates **no order** and is audited. There is no fallback quantity:
a hardcoded number attributed to an agent that never chose it would be worse than
an error.

Repeated calls create **separate** orders. That is what makes the
alternative-supplier flow work after a failure.

### `GET /api/proposals` · `GET /api/proposals/{order_id}`

Orders in `proposed` state, newest first (`limit` 1–200 default 50, `offset`).
Returns `OrderRead` summaries. Use `GET /api/orders/{id}` for full detail.

---

### `GET /api/orders`

| Query | Notes |
| --- | --- |
| `status` | `proposed` \| `approved` \| `paid` \| `failed` \| `reversed` |
| `product_id`, `supplier_id` | filters |
| `limit` 1–200 (50), `offset` | |

```json
[{ "id": 1, "status": "paid", "product_id": 1, "product_name": "Milk",
   "supplier_id": 1, "supplier_name": "Amul Dairy Direct",
   "quantity": 75, "unit": "litre",
   "amount_paise": 360000, "amount": "3600.00",
   "created_at": "…", "approved_at": "…" }]
```

### `GET /api/orders/{order_id}`

Everything needed to render one order — one request, no follow-ups.

```json
{
  "id": 1, "status": "paid", "quantity": 75, "unit": "litre",
  "amount_paise": 360000, "amount": "3600.00",
  "unit_price_paise": 4800, "unit_price": "48.00",
  "unit_price_paise_at_proposal": 4800,

  "product":  { "id": 1, "name": "Milk", "unit": "litre",
                "current_stock": 117, "reorder_threshold": 60 },
  "supplier": { "id": 1, "name": "Amul Dairy Direct",
                "price_per_unit_paise": 4800, "price_per_unit": "48.00",
                "delivery_days": 2, "has_fund_account": true },

  "forecast_reasoning": "…the model's words at proposal time…",
  "supplier_reasoning": "…the model's words at proposal time…",

  "payment": {
    "payout_id": "pout_R7ambiUdUvg6AD",
    "payout_status": "processed",
    "payout_requested": true,
    "outcome_unknown": false,
    "awaiting_settlement": false,
    "failure_reason": null
  },

  "created_at": "…", "updated_at": "…", "approved_at": "…"
}
```

Reasoning is stored, never regenerated — a historical order keeps the words it
was actually decided with.

`unit_price_paise_at_proposal` differing from `unit_price_paise` means the
supplier changed price since the proposal.

`payment.outcome_unknown: true` needs attention: a payout was sent and no
identifier came back. See [payment-flow.md](payment-flow.md) §7.

Errors: `404`.

---

### `POST /api/orders/{order_id}/approve`

**The human authorisation gate.** The only path by which money leaves the
account. No automatic, agent-driven, or amount-based bypass exists at any value —
a ₹1 order needs the same explicit call as a ₹10,000 one.

**Send no request body.** A client supplies an order id and nothing else.

Revalidated from scratch at approval time, because a proposal can be stale: the
supplier must still exist, still supply this product, and still have a fund
account; the amount is recomputed from the supplier's *current* price; and the
quantity, per-order and daily caps are all checked again.

`200 OK`:

```json
{
  "order": { "…full OrderDetail…", "status": "approved" },
  "payout_requested": true,
  "payout_id": "pout_R7ambiUdUvg6AD",
  "payout_status": "queued",
  "amount_paise": 360000,
  "price_changed_since_proposal": false,
  "next_step": "Payout requested. The order stays 'approved' until a signature-verified payout.processed webhook arrives, at which point it becomes 'paid' and stock increases."
}
```

**`order.status` is `approved`, never `paid`.** A successful payout request means
RazorpayX accepted it, not that the supplier was paid. Do not render "paid" from
a successful approval.

Errors:

| Status | Code | When |
| --- | --- | --- |
| 400 | `SUPPLIER_NOT_FOR_PRODUCT` | Supplier was repointed since the proposal |
| 400 | `SUPPLIER_NO_FUND_ACCOUNT` | Bank details removed since the proposal |
| 404 | `ORDER_NOT_FOUND` | — |
| 409 | `ORDER_NOT_PROPOSED` | Order is `paid` / `failed` / `reversed` |
| 409 | `PAYOUT_ALREADY_REQUESTED` | Already approved and a payout was sent |
| 409 | `PAYOUT_OUTCOME_UNKNOWN` | Prior attempt's outcome unknown; not retried |
| 422 | `ORDER_SPEND_LIMIT_EXCEEDED` / `DAILY_SPEND_LIMIT_EXCEEDED` | Cap breached at approval time |
| 502 | `PAYMENT_PROVIDER_ERROR` | RazorpayX rejected it. No payout created. |
| 503 | `PAYMENT_NOT_CONFIGURED` | No credentials. **Nothing simulated.** |
| 504 | `PAYMENT_TIMEOUT` | **Outcome unknown.** Not retried. |

Safe to double-submit: two concurrent approvals cannot create two payouts, and
the idempotency key is persisted before the RazorpayX call.

A `422` guardrail rejection is **not** terminal — the order stays `proposed` and
can be approved later, e.g. once the daily budget resets.

---

### `GET /api/audit`

| Query | Notes |
| --- | --- |
| `order_id` | filter |
| `actor` | `agent` \| `human` \| `system` |
| `action` | exact match, e.g. `PAYOUT_PROCESSED` |
| `limit` 1–500 (100), `offset` | |

Newest first.

```json
[{
  "id": 12, "timestamp": "2026-08-27T09:54:11.221Z",
  "actor": "agent", "action": "FORECAST_GENERATED",
  "reasoning_text": "Daily sales average about 20 litres…",
  "related_order_id": null,
  "metadata": { "product_id": 1, "recommended_quantity": 75,
                "observed_daily_average": 21.71,
                "limits_at_decision": { "max_order_spend_paise": 1000000 } }
}]
```

`actor` distinguishes a model recommendation (`agent`) from a person's decision
(`human`) from deterministic backend code (`system`). For `agent` entries,
`reasoning_text` is the model's own output, verbatim.

`metadata` is scrubbed of anything secret-shaped before it is written.

### `GET /api/audit/{order_id}`

The decision chain, **oldest first**, so it reads in the order it happened.

```json
{
  "order_id": 1,
  "entry_count": 8,
  "entries": [ "…AuditLogRead, chronological…" ]
}
```

A completed happy path reads:

```
PROPOSAL_CREATED → ORDER_APPROVED → RAZORPAY_PAYOUT_REQUESTED
  → RAZORPAY_PAYOUT_CREATED → WEBHOOK_RECEIVED → PAYOUT_PROCESSED
  → INVENTORY_UPDATED
```

Errors: `404`.

**The audit trail is not writable through the API.** `POST`, `PUT`, `PATCH` and
`DELETE` return `405`.

### Action vocabulary

```
LOW_STOCK_DETECTED              INVENTORY_CHECK_COMPLETED
FORECAST_STARTED                FORECAST_GENERATED       FORECAST_FAILED
SUPPLIER_SELECTION_STARTED      SUPPLIER_SELECTED        SUPPLIER_SELECTION_FAILED
PROPOSAL_CREATED                PROPOSAL_FAILED
ORDER_APPROVED                  ORDER_APPROVAL_REJECTED
RAZORPAY_PAYOUT_REQUESTED       RAZORPAY_PAYOUT_CREATED
RAZORPAY_PAYOUT_FAILED          RAZORPAY_PAYOUT_OUTCOME_UNKNOWN
WEBHOOK_RECEIVED                WEBHOOK_REJECTED         WEBHOOK_DUPLICATE_IGNORED
PAYOUT_PROCESSED                PAYOUT_FAILED            PAYOUT_REVERSED
INVENTORY_UPDATED               GUARDRAIL_VIOLATION
```

---

### `POST /api/webhooks/razorpayx`

Called by RazorpayX, not by a client. Full detail in
[payment-flow.md](payment-flow.md).

Headers: `X-Razorpay-Signature` (required), `X-Razorpay-Event-Id`
(recommended — the idempotency key; a content hash is used as fallback).

`200 OK`:

```json
{
  "received": true,
  "event_id": "evt_R7ambiUdUvg6AD",
  "event_type": "payout.processed",
  "outcome": "applied",
  "duplicate": false,
  "order_id": 1,
  "order_status": "paid",
  "detail": "Order 1 is now paid."
}
```

| `outcome` | Meaning |
| --- | --- |
| `applied` | Drove a real state transition |
| `already_applied` | Order was already in the target state — idempotent no-op |
| `acknowledged` | Intermediate status recorded, deliberately no transition |
| `ignored` | Event type this system does not act on |

`200` is returned for every correctly-handled outcome, including duplicates,
because Razorpay retries on any non-2xx and retrying those would be pointless.

Errors: `400 WEBHOOK_MALFORMED`, `401 WEBHOOK_SIGNATURE_INVALID`,
`404 WEBHOOK_UNKNOWN_PAYOUT`, `409 INVALID_STATE_TRANSITION`.

---

## Worked example

```bash
# 1. What needs reordering?
curl -X POST localhost:8000/api/inventory/check
# → Milk 42/60, Coffee Beans 12/25

# 2. Ask the agents for a proposal. Nothing is spent.
curl -X POST localhost:8000/api/proposals/product/1
# → 201, order_id 1, 75 litres from Amul Dairy Direct, INR 3600.00,
#   plus both agents' reasoning

# 3. Read the reasoning, then authorise. NO REQUEST BODY.
curl -X POST localhost:8000/api/orders/1/approve
# → 200, status "approved", payout_id pout_…, payout_status "queued"
#   Stock has NOT changed.

# 4. RazorpayX calls back (this is what its delivery looks like):
BODY='{"entity":"event","event":"payout.processed","contains":["payout"],
"payload":{"payout":{"entity":{"id":"pout_R7ambiUdUvg6AD","status":"processed"}}},
"created_at":1755693679}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$RAZORPAY_WEBHOOK_SECRET" -hex | awk '{print $2}')
curl -X POST localhost:8000/api/webhooks/razorpayx \
     -H "X-Razorpay-Signature: $SIG" \
     -H "X-Razorpay-Event-Id: evt_abc123" \
     -H "Content-Type: application/json" \
     -d "$BODY"
# → 200, outcome "applied", order_status "paid". Milk 42 → 117.

# 5. Send it again — nothing changes.
#    → 200, duplicate true, Milk still 117.

# 6. Why did this order happen?
curl localhost:8000/api/audit/1
```

---

## Notes for a frontend

* **Never compute or send money.** Display `*_paise` or the rupee string; never
  post an amount.
* **A successful approval is not a payment.** Render `approved` +
  "awaiting confirmation" until a webhook makes it `paid`. `payment.awaiting_settlement`
  is exactly this flag.
* **Handle `409` on approve as normal.** It is what a double-click produces, and
  it means the system protected the merchant.
* **Surface `payment.outcome_unknown`** prominently. It needs a human.
* **A `422` guardrail error is recoverable.** The order is still `proposed`; show
  `details` (`already_committed_paise`, `limit_paise`) and let the merchant retry
  later.
* **Proposals cost LLM calls.** Do not generate one on page load.
* **Reasoning is historical.** Show the stored text; never regenerate it.
