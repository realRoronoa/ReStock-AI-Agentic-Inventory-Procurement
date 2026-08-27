/**
 * Types mirroring the backend response schemas.
 *
 * Two conventions carried over from the backend, both load-bearing:
 *
 * 1. **Money appears twice.** `*_paise` is an integer and is the only field safe
 *    to do arithmetic on. The matching rupee field is a `string` (the backend
 *    serialises `Decimal` to a JSON string on purpose) and is for display only.
 *    It is typed as `string` here so that `amount * 2` is a compile error.
 *
 * 2. **Timestamps are ISO-8601 UTC strings** with an offset, e.g.
 *    `"2026-08-27T09:53:52.832639Z"`.
 *
 * Kept hand-written rather than generated: the set is small, and the comments
 * about which fields are trustworthy are worth more than the codegen would be.
 * If this drifts, `GET /openapi.json` is the source of truth.
 */

// --- primitives -------------------------------------------------------------

/** Integer paise. 100 paise = ₹1. Safe for arithmetic. */
export type Paise = number

/** Rupee amount as a decimal string, e.g. `"3600.00"`. Display only. */
export type RupeeString = string

/** ISO-8601 timestamp, UTC. */
export type IsoDateTime = string

/** ISO-8601 calendar date, `YYYY-MM-DD`. */
export type IsoDate = string

// --- enums ------------------------------------------------------------------

/**
 * The order lifecycle. Six values, and the distinctions matter:
 *
 * - `proposed`  — the agents recommended it; nothing spent. Awaiting a human.
 * - `approved`  — a human authorised it and a payout was **requested**. NOT paid.
 * - `paid`      — a verified `payout.processed` webhook arrived. Stock increased.
 * - `failed`    — the payout did not complete. Stock unchanged.
 * - `reversed`  — money moved and came back. Stock deliberately NOT adjusted.
 * - `rejected`  — a human declined. Nothing was ever sent to RazorpayX.
 *
 * There is no `processing` status. "Processing" is `approved` together with
 * `payment.awaiting_settlement === true`.
 */
export type OrderStatus =
  | 'proposed'
  | 'approved'
  | 'paid'
  | 'failed'
  | 'reversed'
  | 'rejected'

export type StockStatus = 'low_stock' | 'ok'

/**
 * Who caused an audited event.
 *
 * - `agent`  — an LLM produced it; `reasoning_text` is the model's own words.
 * - `human`  — a person acted (approval, rejection, recording a sale).
 * - `system` — deterministic backend code.
 */
export type AuditActor = 'agent' | 'human' | 'system'

/** What webhook processing did with a delivery. */
export type WebhookOutcome =
  | 'applied'
  | 'already_applied'
  | 'acknowledged'
  | 'ignored'
  | 'unknown_payout'
  | 'conflict'

// --- errors -----------------------------------------------------------------

/**
 * The single error envelope every endpoint returns.
 *
 * Branch on `code`, never on `message` — messages are written for humans and
 * may be reworded.
 */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details?: Record<string, unknown>
    /** Present on 500/503. Quote it when reporting a problem. */
    request_id?: string
  }
}

// --- health & settings ------------------------------------------------------

export interface IntegrationFlags {
  razorpayx_payouts_configured: boolean
  razorpayx_webhooks_configured: boolean
  llm_configured: boolean
}

export interface Health {
  status: 'ok' | 'degraded'
  app: string
  environment: string
  version: string
  database: 'up' | 'down'
  integrations: IntegrationFlags
}

export interface GuardrailSettings {
  max_reorder_quantity: number
  max_order_spend_paise: Paise
  max_order_spend: RupeeString
  max_daily_spend_paise: Paise
  max_daily_spend: RupeeString
  spend_day_timezone: string
}

export interface IntegrationSettings extends IntegrationFlags {
  payout_mode: string
  payout_purpose: string
  llm_model: string
}

export interface AppSettings {
  app_name: string
  environment: string
  version: string
  guardrails: GuardrailSettings
  integrations: IntegrationSettings
  forecast: { forecast_history_days: number }
  /** Always false. Limits come from the server environment. */
  editable: boolean
}

// --- products ---------------------------------------------------------------

export interface Product {
  id: number
  name: string
  current_stock: number
  unit: string
  reorder_threshold: number
  /** `current_stock < reorder_threshold`. Strict — equality is NOT low. */
  is_low_stock: boolean
  created_at: IsoDateTime
  updated_at: IsoDateTime
}

export interface Supplier {
  id: number
  product_id: number
  name: string
  price_per_unit_paise: Paise
  price_per_unit: RupeeString
  delivery_days: number
  razorpay_fund_account_id: string | null
  /** False means this supplier cannot be paid and will not be recommended. */
  has_fund_account: boolean
}

export interface SalesPoint {
  date: IsoDate
  quantity_sold: number
}

export interface ProductDetail extends Product {
  /** Cheapest first. */
  suppliers: Supplier[]
  /** Most recent day first. */
  recent_sales: SalesPoint[]
}

// --- inventory --------------------------------------------------------------

export interface LowStockProduct {
  id: number
  name: string
  current_stock: number
  reorder_threshold: number
  unit: string
  status: StockStatus
  /** Units below the threshold. */
  shortfall: number
}

export interface InventoryCheckResult {
  low_stock_products: LowStockProduct[]
  products_checked: number
  low_stock_count: number
  /** Products that produced a *new* LOW_STOCK_DETECTED audit event. */
  newly_detected_product_ids: number[]
  checked_at: IsoDateTime
}

// --- sales ------------------------------------------------------------------

export interface RecordSaleInput {
  product_id: number
  quantity: number
  /** Defaults to today. Same-day sales accumulate. */
  sale_date?: IsoDate
}

export interface SaleResult {
  product_id: number
  product_name: string
  unit: string
  quantity_sold: number
  sale_date: IsoDate
  stock_before: number
  stock_after: number
  reorder_threshold: number
  status: StockStatus
  is_low_stock: boolean
  shortfall: number
  /** True when this sale is what pushed the product below its threshold. */
  became_low_stock: boolean
  next_step: string
}

// --- proposals --------------------------------------------------------------

/**
 * A reorder proposal: an order in `proposed` state, plus the reasoning chain.
 *
 * Everything here is a fact recorded by the backend. `forecast_reasoning` and
 * `supplier_reasoning` are the models' own words at proposal time, stored
 * verbatim and never regenerated — render them as history, not as live output.
 */
export interface Proposal {
  order_id: number
  status: OrderStatus

  // what triggered it
  product_id: number
  product_name: string
  unit: string
  current_stock: number
  reorder_threshold: number
  shortfall: number

  // what the forecast agent decided
  recommended_quantity: number
  forecast_reasoning: string
  observed_daily_average: number
  history_days: number
  forecast_provider: string

  // what the supplier agent decided
  supplier_id: number
  supplier_name: string
  delivery_days: number
  supplier_reasoning: string
  supplier_provider: string
  options_considered: number

  // what the backend computed — never the model, never the client
  unit_price_paise: Paise
  unit_price: RupeeString
  total_amount_paise: Paise
  total_amount: RupeeString

  created_at: IsoDateTime
  next_step: string
}

// --- orders -----------------------------------------------------------------

export interface OrderSummary {
  id: number
  status: OrderStatus
  product_id: number
  product_name: string
  supplier_id: number
  supplier_name: string
  quantity: number
  unit: string
  amount_paise: Paise
  amount: RupeeString
  created_at: IsoDateTime
  approved_at: IsoDateTime | null
}

/** Payment-side view of an order. */
export interface PaymentState {
  payout_id: string | null
  /** RazorpayX status verbatim, e.g. `"queued"`, `"processed"`. */
  payout_status: string | null
  payout_requested: boolean
  /**
   * A payout was sent but no identifier came back. Needs human
   * reconciliation; the backend will not retry automatically. Surface this
   * prominently.
   */
  outcome_unknown: boolean
  /** Payout created, waiting on a webhook. This is the "processing" state. */
  awaiting_settlement: boolean
  failure_reason: string | null
}

export interface OrderProduct {
  id: number
  name: string
  unit: string
  current_stock: number
  reorder_threshold: number
}

export interface OrderSupplier {
  id: number
  name: string
  price_per_unit_paise: Paise
  price_per_unit: RupeeString
  delivery_days: number
  has_fund_account: boolean
}

export interface OrderDetail {
  id: number
  status: OrderStatus
  quantity: number
  unit: string

  amount_paise: Paise
  amount: RupeeString
  unit_price_paise: Paise
  unit_price: RupeeString
  /** Differs from `unit_price_paise` if the supplier changed price since. */
  unit_price_paise_at_proposal: Paise | null

  product: OrderProduct
  supplier: OrderSupplier

  forecast_reasoning: string | null
  supplier_reasoning: string | null

  payment: PaymentState

  created_at: IsoDateTime
  updated_at: IsoDateTime
  approved_at: IsoDateTime | null
}

export interface ApprovalResult {
  /** `status` is `approved`, never `paid`. Do not render "paid" from this. */
  order: OrderDetail
  payout_requested: boolean
  payout_id: string | null
  payout_status: string | null
  /** Recomputed from the supplier's current price at approval time. */
  amount_paise: Paise
  price_changed_since_proposal: boolean
  next_step: string
}

export interface RejectOrderInput {
  /** Recorded in the audit trail. Affects no business logic. */
  reason?: string
}

// --- spending ---------------------------------------------------------------

export interface DaySpend {
  day: IsoDate
  committed_paise: Paise
  committed: RupeeString
  order_count: number
  /** The current day, whose figure is usually partial. */
  is_today: boolean
}

export interface SpendSummary {
  spend_day_start: IsoDateTime
  spend_day_end: IsoDateTime
  timezone: string

  committed_today_paise: Paise
  committed_today: RupeeString
  daily_limit_paise: Paise
  daily_limit: RupeeString
  remaining_today_paise: Paise
  remaining_today: RupeeString
  utilisation_percent: number

  max_order_spend_paise: Paise
  max_order_spend: RupeeString
  max_reorder_quantity: number

  /** Statuses whose money counts toward the cap: `approved` and `paid`. */
  counted_statuses: OrderStatus[]
  orders_today: OrderSummary[]
  /** Chronological, includes zero-spend days so charts do not close gaps. */
  history: DaySpend[]
}

// --- audit ------------------------------------------------------------------

export interface AuditEvent {
  id: number
  timestamp: IsoDateTime
  actor: AuditActor
  /** Canonical action name, e.g. `PAYOUT_PROCESSED`. */
  action: string
  /** For `agent` events, the model's own reasoning, verbatim. */
  reasoning_text: string | null
  related_order_id: number | null
  metadata: Record<string, unknown> | null
}

export interface AuditTrail {
  order_id: number
  entries: AuditEvent[]
  entry_count: number
}

// --- webhook ack (for completeness; the frontend never posts one) -----------

export interface WebhookAck {
  received: boolean
  event_id: string
  event_type: string
  outcome: WebhookOutcome
  duplicate: boolean
  order_id: number | null
  order_status: OrderStatus | null
  detail: string
}
