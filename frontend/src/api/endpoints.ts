/**
 * Typed wrappers for every backend endpoint.
 *
 * Grouped by resource. Each function does exactly one HTTP call and returns a
 * typed body; no caching, no React, no state — that is the hooks' job.
 *
 * Endpoint paths are written out here once and nowhere else, so a backend route
 * change has a single place to land.
 */

import { apiClient } from '@/api/client'
import type {
  ApprovalResult,
  AppSettings,
  AuditEvent,
  AuditActor,
  AuditTrail,
  Health,
  InventoryCheckResult,
  LowStockProduct,
  OrderDetail,
  OrderStatus,
  OrderSummary,
  Product,
  ProductDetail,
  Proposal,
  RecordSaleInput,
  RejectOrderInput,
  SaleResult,
  SpendSummary,
} from '@/types/api'

// --- meta -------------------------------------------------------------------

export const meta = {
  /** Liveness, database reachability, and which integrations are configured. */
  health: () => apiClient.get<Health>('/health'),

  /** Read-only server configuration: guardrails, integrations, forecast window. */
  settings: () => apiClient.get<AppSettings>('/api/settings'),
}

// --- products ---------------------------------------------------------------

export const products = {
  list: (params?: { lowStock?: boolean }) =>
    apiClient.get<Product[]>('/api/products', {
      query: { low_stock: params?.lowStock },
    }),

  /** Product with its suppliers (cheapest first) and recent sales (newest first). */
  detail: (productId: number, params?: { salesDays?: number }) =>
    apiClient.get<ProductDetail>(`/api/products/${productId}`, {
      query: { sales_days: params?.salesDays },
    }),
}

// --- inventory --------------------------------------------------------------

export const inventory = {
  /**
   * Read-only low-stock list. Safe to poll — writes nothing.
   *
   * Prefer this over `check()` for rendering.
   */
  lowStock: () => apiClient.get<LowStockProduct[]>('/api/inventory/low-stock'),

  /**
   * Run a sweep and record it in the audit trail.
   *
   * A deliberate user action, not something to call on render: it writes
   * `LOW_STOCK_DETECTED` / `INVENTORY_CHECK_COMPLETED` events.
   */
  check: () => apiClient.post<InventoryCheckResult>('/api/inventory/check'),
}

// --- sales ------------------------------------------------------------------

export const sales = {
  /**
   * Record units sold. Decreases stock and appends to sales history.
   *
   * Cannot increase stock — that only ever happens via a verified payout
   * webhook. A sale larger than stock on hand is refused with
   * `INSUFFICIENT_STOCK`.
   */
  record: (input: RecordSaleInput) =>
    apiClient.post<SaleResult>('/api/sales', { body: input }),
}

// --- proposals --------------------------------------------------------------

export const proposals = {
  /**
   * Generate a reorder proposal. Costs two LLM calls and commits no money.
   *
   * Fails (creating no order) if the product is not low stock, if either model
   * is unreachable or returns something invalid, or if a spend guardrail
   * rejects the result.
   */
  create: (productId: number) =>
    apiClient.post<Proposal>(`/api/proposals/product/${productId}`, {
      // Proposals involve two model calls; allow more headroom than the default.
      timeoutMs: 90_000,
    }),

  /** Orders awaiting a human decision, newest first. */
  list: (params?: { limit?: number; offset?: number }) =>
    apiClient.get<OrderSummary[]>('/api/proposals', {
      query: { limit: params?.limit, offset: params?.offset },
    }),
}

// --- orders -----------------------------------------------------------------

export const orders = {
  list: (params?: {
    status?: OrderStatus
    productId?: number
    supplierId?: number
    limit?: number
    offset?: number
  }) =>
    apiClient.get<OrderSummary[]>('/api/orders', {
      query: {
        status: params?.status,
        product_id: params?.productId,
        supplier_id: params?.supplierId,
        limit: params?.limit,
        offset: params?.offset,
      },
    }),

  /** Full detail: product, supplier, amounts, AI reasoning, payment state. */
  detail: (orderId: number) =>
    apiClient.get<OrderDetail>(`/api/orders/${orderId}`),

  /**
   * **The human approval gate.** Sends no body: an order id is all a client
   * may supply.
   *
   * On success the order is `approved` and a payout has been *requested*. It is
   * NOT paid — settlement arrives later by webhook.
   *
   * Safe to have been double-submitted: a second call returns 409
   * `PAYOUT_ALREADY_REQUESTED` rather than creating a second payout.
   */
  approve: (orderId: number) =>
    apiClient.post<ApprovalResult>(`/api/orders/${orderId}/approve`, {
      // A payout request can be slow; the backend caps its own upstream call.
      timeoutMs: 60_000,
    }),

  /**
   * Decline a proposal. Terminal, and sends nothing to RazorpayX.
   *
   * Only `proposed` orders can be rejected.
   */
  reject: (orderId: number, input?: RejectOrderInput) =>
    apiClient.post<OrderDetail>(`/api/orders/${orderId}/reject`, {
      body: input ?? {},
    }),
}

// --- spending ---------------------------------------------------------------

export const spending = {
  /**
   * Committed spend against the configured caps, plus a daily series.
   *
   * Backed by the same functions as the approval gate, so this never disagrees
   * with what an approval will actually allow.
   */
  summary: (params?: { historyDays?: number }) =>
    apiClient.get<SpendSummary>('/api/spending', {
      query: { history_days: params?.historyDays },
    }),
}

// --- audit ------------------------------------------------------------------

export const audit = {
  /** Newest first. */
  list: (params?: {
    orderId?: number
    actor?: AuditActor
    action?: string
    limit?: number
    offset?: number
  }) =>
    apiClient.get<AuditEvent[]>('/api/audit', {
      query: {
        order_id: params?.orderId,
        actor: params?.actor,
        action: params?.action,
        limit: params?.limit,
        offset: params?.offset,
      },
    }),

  /** One order's decision chain, **oldest first** so it reads in order. */
  trail: (orderId: number, params?: { limit?: number }) =>
    apiClient.get<AuditTrail>(`/api/audit/${orderId}`, {
      query: { limit: params?.limit },
    }),
}

export const api = {
  meta,
  products,
  inventory,
  sales,
  proposals,
  orders,
  spending,
  audit,
}
