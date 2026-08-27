# Payment flow

How money actually moves, and every way it can go wrong.

**Verification status:** the RazorpayX integration has **never been run against
the live RazorpayX service.** See §9 before trusting anything here in production.

---

## 1. Why payouts, not a payment link

The direction of money matters:

```
        Payment Link                         Payout
   customer ──money──▶ merchant     merchant ──money──▶ supplier
   (collection)                     (disbursal)          ▲
                                                    what we need
```

A Payment Link collects money *from* someone. ReStock AI pays a supplier, so it
uses **RazorpayX Payouts**: `POST /v1/payouts`.

`orders.razorpay_payout_id` is therefore the primary payment identifier.
`razorpay_order_id` and `razorpay_payment_id` exist on the table for a possible
future collection-side flow and stay `NULL` — they are never populated with
something invented.

---

## 2. The whole sequence

```
 MERCHANT                BACKEND                  RAZORPAYX            BANK
    │                       │                         │                  │
    │  POST /approve        │                         │                  │
    ├──────────────────────▶│                         │                  │
    │                       │ revalidate everything   │                  │
    │                       │ (supplier, price, caps) │                  │
    │                       │                         │                  │
    │                       │ CAS: proposed→approved  │                  │
    │                       │ store idempotency key   │                  │
    │                       │ ══ COMMIT ══            │                  │
    │                       │                         │                  │
    │                       │ CAS: claim payout       │                  │
    │                       │ ══ COMMIT ══            │                  │
    │                       │                         │                  │
    │                       │ POST /v1/payouts        │                  │
    │                       │ X-Payout-Idempotency    │                  │
    │                       ├────────────────────────▶│                  │
    │                       │  201 {id, status:queued}│                  │
    │                       │◀────────────────────────┤                  │
    │                       │ store payout_id         │                  │
    │                       │ ══ COMMIT ══            │                  │
    │  200 approved         │                         │                  │
    │◀──────────────────────┤   status STILL approved │                  │
    │                       │   stock UNCHANGED       │                  │
    │                       │                         │                  │
    │                       │                         │  transfer        │
    │                       │                         ├─────────────────▶│
    │                       │  payout.queued          │                  │
    │                       │◀────────────────────────┤                  │
    │                       │ acknowledged, NO change │                  │
    │                       │                         │      credited    │
    │                       │                         │◀─────────────────┤
    │                       │  payout.processed       │                  │
    │                       │◀────────────────────────┤                  │
    │                       │ verify signature        │                  │
    │                       │ claim event id          │                  │
    │                       │ approved→paid           │                  │
    │                       │ stock += quantity       │                  │
    │                       │ audit                   │                  │
    │                       │ ══ ONE COMMIT ══        │                  │
```

The load-bearing detail: **the approval response is not a payment confirmation.**
Three separate commits happen before RazorpayX is even contacted, and the order
stays `approved` until a webhook says otherwise.

---

## 3. The payout request

Implemented in `app/services/payment_service.py` against the documented contract.

```http
POST https://api.razorpay.com/v1/payouts
Authorization: Basic base64(RAZORPAY_KEY_ID:RAZORPAY_KEY_SECRET)
Content-Type: application/json
X-Payout-Idempotency: 8f14e45f-ceea-467a-9c1e-2d3b4c5a6e7f
```

```json
{
  "account_number": "2323230099089860",
  "fund_account_id": "fa_TESTMILKAMUL01",
  "amount": 360000,
  "currency": "INR",
  "mode": "IMPS",
  "purpose": "vendor bill",
  "queue_if_low_balance": false,
  "reference_id": "restock-order-1",
  "narration": "Milk order 1",
  "notes": { "order_id": "1", "product_id": "1",
             "supplier_id": "1", "quantity": "75" }
}
```

| Field | Source | Constraint handled |
| --- | --- | --- |
| `account_number` | `RAZORPAY_ACCOUNT_NUMBER` | Your RazorpayX source account |
| `fund_account_id` | `suppliers.razorpay_fund_account_id` | Approval refuses if `NULL` |
| `amount` | `orders.amount_paise` | **Integer paise.** Minimum 100, checked locally |
| `currency` | constant | `INR` |
| `mode` | `RAZORPAY_PAYOUT_MODE` | `IMPS` \| `NEFT` \| `RTGS`, case-sensitive |
| `purpose` | `RAZORPAY_PAYOUT_PURPOSE` | Default `vendor bill` |
| `reference_id` | `restock-order-{id}` | ≤40 chars. Reconciliation handle |
| `narration` | product name + order id | ≤30 chars, **alphanumerics and spaces only** |
| `notes` | order identifiers | Echoed on webhooks and in the dashboard |

Two constraints are enforced before the call so a merchant gets a clear reason
rather than an opaque upstream `400`:

* **Amount below 100 paise** → local `PAYMENT_PROVIDER_ERROR` naming the minimum.
* **Narration charset** — `build_narration` strips non-alphanumerics and
  truncates to 30. `"Coffee Beans (Arabica) — 100% !"` becomes
  `"Coffee Beans order 42"`; a name with no usable characters falls back to
  `"ReStock order 42"`.

The key secret is passed to httpx as Basic auth credentials. It appears in no
log, no audit row, no exception message, and no request body — there is a test
asserting its absence from the body.

---

## 4. Idempotency: the thing that prevents double payment

Three mechanisms, layered.

### a) A stable idempotency key

```
generate UUID once  →  persist it  →  COMMIT  →  then call RazorpayX
```

`X-Payout-Idempotency` makes RazorpayX collapse repeated requests carrying the
same key onto a single payout. That only works if the key is **stable**, so it is
generated exactly once per order and stored in `orders.payout_idempotency_key`
(`UNIQUE`) *before* the request goes out.

Generating a fresh key on retry is precisely the bug that creates duplicate
payouts. `payment_service` therefore never mints one — the key is an input.

### b) Two compare-and-swap claims

The dangerous case is two approvals in flight simultaneously. `rowcount` on a
conditional `UPDATE` makes the *database* pick the winner:

```sql
-- claim 1: only one caller can move proposed → approved
UPDATE orders SET status='approved', approved_at=…, payout_idempotency_key=…
 WHERE id=:id AND status='proposed';

-- claim 2: only one caller can send the payout
UPDATE orders SET payout_attempted_at=…
 WHERE id=:id AND payout_attempted_at IS NULL AND status='approved';
```

Two claims rather than one, because an order that is `approved` but whose payout
never went out is *resumable* — and a resume must not race another resume. Both
statements work identically on SQLite and PostgreSQL, needing no
`SELECT … FOR UPDATE`.

### c) A `UNIQUE` payout id

`orders.razorpay_payout_id` is `UNIQUE`, so one payout can never be attached to
two local orders even if something upstream went badly wrong.

---

## 5. Payout accepted ≠ supplier paid

This is the single most important rule in the payment path.

```
     WRONG                          RIGHT
  create payout                  create payout
       ↓                              ↓
   mark PAID                    store payout_id
                                      ↓
                             order stays APPROVED
                                      ↓
                              wait for webhook
                                      ↓
                              payout.processed
                                      ↓
                                  mark PAID
```

A `201` from `POST /v1/payouts` means RazorpayX accepted the *request*. The money
has not reached the supplier — that takes minutes and can still fail or be
reversed.

The code goes further: even if the creation response already carries
`status: "processed"`, the order is **still not** marked paid. Settlement is only
ever accepted from a signature-verified webhook, so one code path owns the
transition and the inventory increment.

---

## 6. Webhook processing

```
raw bytes
  → verify HMAC-SHA256 signature      ← BEFORE the JSON is parsed
  → parse JSON
  → extract event type + payout id
  → claim the delivery (UNIQUE event id)
  → resolve the local order
  → apply the transition
  → increment stock, only if processed
  → audit
  → ONE commit
```

### Signature verification

```python
expected = hmac.new(
    key=RAZORPAY_WEBHOOK_SECRET.encode(),
    msg=raw_body,                      # the exact bytes received
    digestmod=hashlib.sha256,
).hexdigest()

if not hmac.compare_digest(expected, received_signature):
    raise WebhookSignatureError()
```

Four properties, each deliberate:

1. **Raw bytes.** Parsing and re-serialising would change key order, whitespace,
   and unicode escaping, and an authentic signature would stop matching. A test
   proves re-serialisation breaks verification.
2. **Before parsing.** An unverified body is never trusted — not even to perform
   a lookup, which would let a stranger probe which payout ids exist.
3. **Constant-time compare.** A byte-by-byte early exit leaks the expected digest
   one character at a time under a timing attack.
4. **Fails closed.** No secret configured, no header, or a wrong signature all
   produce the same `401`, so a prober learns nothing about which precondition
   failed. An unverifiable webhook is refused rather than trusted, because
   accepting it would let anyone move stock.

### Event classification

| Events | Order becomes | Inventory |
| --- | --- | --- |
| `payout.processed` | `paid` | `+= quantity`, **exactly once** |
| `payout.failed` | `failed` | unchanged |
| `payout.rejected`, `payout.cancelled` | `failed` | unchanged |
| `payout.reversed` | `reversed` | **not adjusted** |
| `payout.queued`, `.initiated`, `.pending`, `.processing`, `.updated` | *no change* | unchanged |
| `payout.downtime.*`, unrecognised | *no change* | unchanged |

Intermediate events **record** RazorpayX's status on the order — so a merchant can
see progress — while causing no transition. Treating "accepted" as "paid" is the
classic bug here; there is a parametrised test over all five intermediate events
asserting the order stays `approved`.

`rejected` and `cancelled` mapping to `failed` is a documented extension beyond
the three required events. Both mean no money moved; leaving the order `approved`
forever would have a merchant waiting for a settlement that is never coming.

A test asserts the terminal and intermediate sets do not overlap, and that
`payout.processed` is the **only** event that can produce `paid`.

### Payload

```json
{
  "entity": "event",
  "account_id": "acc_…",
  "event": "payout.processed",
  "contains": ["payout"],
  "payload": { "payout": { "entity": {
      "id": "pout_R7ambiUdUvg6AD",
      "status": "processed",
      "amount": 360000,
      "utr": "523223155921",
      "failure_reason": null,
      "status_details": { "reason": "payout_processed",
                          "description": "…", "source": "beneficiary_bank" }
  }}},
  "created_at": 1755693679
}
```

Payout id at `payload.payout.entity.id`, status at `…entity.status`.

`failure_reason` is assembled for the merchant from, in order:
`entity.failure_reason`, `entity.status_details.description`, then
`entity.error.description`.

---

## 7. Every failure mode

| What happens | Order | Inventory | Response | Follow-up |
| --- | --- | --- | --- | --- |
| No RazorpayX credentials | `proposed` | unchanged | `503 PAYMENT_NOT_CONFIGURED` | Configure. **Nothing simulated.** |
| Supplier has no fund account | `proposed` | unchanged | `400 SUPPLIER_NO_FUND_ACCOUNT` | Onboard bank details |
| Amount below ₹1 | `proposed` | unchanged | `502` naming the minimum | — |
| RazorpayX rejects (e.g. low balance) | `approved`, no payout id | unchanged | `502 PAYMENT_PROVIDER_ERROR` | Fix cause; resume |
| Connection refused | `approved`, no payout id | unchanged | `502` "No payout was created" | Retry deliberately |
| **Request times out** | `approved`, attempted, no id | unchanged | `504 PAYMENT_TIMEOUT` | **See below** |
| Guardrail breached at approval | `proposed` | unchanged | `422` | Recoverable; retry later |
| Approving an already-approved order | unchanged | unchanged | `409 PAYOUT_ALREADY_REQUESTED` | Nothing — protection worked |
| `payout.failed` | `failed` | unchanged | `200` | Merchant chooses next step |
| `payout.reversed` | `reversed` | **unchanged** | `200` | Human investigation |
| Forged / unsigned webhook | unchanged | unchanged | `401` | Nothing recorded |
| Duplicate webhook | unchanged | **unchanged** | `200 duplicate` | Nothing |
| Webhook for unknown payout | — | unchanged | `404` | Recorded, replays consistently |
| `processed` after `failed` | stays `failed` | unchanged | `409` | Investigate — a contradiction |

### A timeout is not a failure

A lost response does **not** mean the payout did not happen. The request may have
been accepted and be settling right now.

So on timeout the system:

* raises a **distinct** error type (`504`), never a failure;
* leaves the order `approved` with `payout_attempted_at` set and no payout id —
  the "outcome unknown" state;
* audits `RAZORPAY_PAYOUT_OUTCOME_UNKNOWN`;
* **does not retry**, and refuses a later approval with `409
  PAYOUT_OUTCOME_UNKNOWN`, naming the `reference_id`
  (`restock-order-{id}`) to search for in the RazorpayX dashboard.

Marking it `failed` would be worse than useless: it could hide a payout that went
through, and would invite a second payment for the same goods.

`GET /api/orders/{id}` exposes this as `payment.outcome_unknown: true`.

### Why failures are never retried automatically

A failed payout is a business event, not a transient error. The cause is usually
something only a human can resolve — wrong bank details, a frozen account, an
unfunded source account. Retrying blindly would burn RazorpayX rate limits, spam
the audit trail, and possibly double-pay.

The merchant instead creates a **new proposal**, which gets its own order id,
approval, payout, idempotency key, and audit trail. Nothing about the failed
order is reused or mutated. There is an end-to-end test for exactly this
alternative-supplier flow.

### Why a reversal does not unwind stock

`payout.reversed` means money came back. It does **not** mean the goods did not
arrive. Automatically subtracting stock would be the system guessing about the
physical world.

So the order becomes `reversed`, stock is left exactly as it is, and the audit
entry says in words that this needs human follow-up.

---

## 8. Transaction boundaries

| Operation | One transaction contains |
| --- | --- |
| Approval claim | status change + `approved_at` + idempotency key + recomputed amount + `ORDER_APPROVED` audit |
| Payout claim | `payout_attempted_at` + `RAZORPAY_PAYOUT_REQUESTED` audit |
| Payout stored | `razorpay_payout_id` + `payout_status` + `RAZORPAY_PAYOUT_CREATED` audit |
| **Webhook settlement** | `webhook_events` row + order status + **stock** + `PAYOUT_PROCESSED` + `INVENTORY_UPDATED` audit |

The last row is the one that matters. Because it is a single commit, these states
are impossible:

* order `paid` but stock unchanged;
* stock increased but order still `approved`;
* stock increased with no audit record saying why.

Failure paths use `audit_service.log_independently`, which rolls back the pending
business transaction and commits the audit row alone — so a rejection is recorded
even though the work it describes was abandoned.

---

## 9. Verification status

### Verified

* Request construction against the documented contract — endpoint, Basic auth,
  `X-Payout-Idempotency` header, integer-paise `amount`, `mode`, `purpose`,
  `reference_id` and `narration` limits — asserted byte-for-byte via
  `httpx.MockTransport` in `tests/test_payment.py`.
* Response interpretation: success, `4xx` with a Razorpay error body, `5xx`,
  missing payout id, unparseable body, connection error, timeout.
* Status normalisation for all nine documented payout statuses, plus the rule
  that an unrecognised status never maps to success.
* Signature verification: valid, wrong, missing, unconfigured, and the
  re-serialisation case.
* All three settlement flows, duplicate deliveries, intermediate events, unknown
  payouts, and illegal transitions — end to end over HTTP.
* The full workflow driven against a **running server** with the real
  `RazorpayXPaymentProvider` and `LLMForecastProvider` classes making real HTTP
  calls to local endpoints that speak the documented request/response shapes.
  This exercises the production wire-level code, including the idempotency header
  and paise amount.

### NOT VERIFIED — requires RazorpayX credentials

Nothing here has touched `api.razorpay.com`. Specifically unverified:

* that RazorpayX accepts these exact field values in Test Mode;
* that its idempotency behaviour matches what is assumed;
* real webhook delivery, retry timing, and header casing;
* that a real `payout.processed` payload matches the documented shape.

Seed `razorpay_fund_account_id` values are **placeholders**
(`fa_TESTMILKAMUL01`, …). RazorpayX will reject a payout to a fund account that
does not exist.

### To verify against Test Mode

1. RazorpayX dashboard → **Test Mode**. Generate API keys.
2. Create a Contact, then a Fund Account for it, and note the real `fa_…` id.
3. Replace the placeholder ids in `backend/app/seed/data.py`, or update the
   `suppliers` rows directly.
4. Fill `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_ACCOUNT_NUMBER` in
   `backend/.env`.
5. Configure a webhook pointing at your public
   `/api/webhooks/razorpayx` (a tunnel is needed for local development),
   subscribe to `payout.processed`, `payout.failed`, `payout.reversed`, and put
   its secret in `RAZORPAY_WEBHOOK_SECRET`.
6. Confirm `GET /health` reports all three integration flags `true`.
7. Run the flow: `POST /api/inventory/check` →
   `POST /api/proposals/product/1` → `POST /api/orders/1/approve`.
8. Check the RazorpayX dashboard for a payout with `reference_id`
   `restock-order-1`, then confirm the webhook arrived and the order became
   `paid` with stock increased exactly once.

### Known limitation: no reconciliation job

There is no background process that sweeps for orders in the
"outcome unknown" state and asks RazorpayX what actually happened. Resolving one
is manual today.

Mitigation: the idempotency key is persisted, so re-sending the same logical
payout is safe whenever someone chooses to. The right fix is a job that queries
`GET /v1/payouts?reference_id=restock-order-{id}` and reconciles from the
authoritative answer — deliberately not built, rather than half-built.
