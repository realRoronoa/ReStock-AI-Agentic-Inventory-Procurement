/**
 * Every cache key in one place.
 *
 * Centralised so that invalidation after a mutation is correct by construction:
 * approving an order touches orders, proposals, spending and audit, and getting
 * that list wrong means a merchant sees stale money. Hierarchical arrays mean
 * `invalidateQueries({ queryKey: keys.orders.all })` catches every order query
 * including detail views.
 */

import type { AuditActor, OrderStatus } from '@/types/api'

export const queryKeys = {
  health: ['health'] as const,
  settings: ['settings'] as const,

  products: {
    all: ['products'] as const,
    list: (lowStock?: boolean) => ['products', 'list', { lowStock }] as const,
    detail: (productId: number, salesDays?: number) =>
      ['products', 'detail', productId, { salesDays }] as const,
  },

  inventory: {
    all: ['inventory'] as const,
    lowStock: ['inventory', 'low-stock'] as const,
  },

  proposals: {
    all: ['proposals'] as const,
    list: (limit?: number, offset?: number) =>
      ['proposals', 'list', { limit, offset }] as const,
  },

  orders: {
    all: ['orders'] as const,
    list: (filters?: {
      status?: OrderStatus
      productId?: number
      supplierId?: number
      limit?: number
      offset?: number
    }) => ['orders', 'list', filters ?? {}] as const,
    detail: (orderId: number) => ['orders', 'detail', orderId] as const,
  },

  spending: {
    all: ['spending'] as const,
    summary: (historyDays?: number) =>
      ['spending', 'summary', { historyDays }] as const,
  },

  audit: {
    all: ['audit'] as const,
    list: (filters?: {
      orderId?: number
      actor?: AuditActor
      action?: string
      limit?: number
      offset?: number
    }) => ['audit', 'list', filters ?? {}] as const,
    trail: (orderId: number) => ['audit', 'trail', orderId] as const,
  },
}

/**
 * Cache families invalidated by each mutation.
 *
 * Written out rather than inlined at each call site so the blast radius of a
 * mutation is reviewable in one glance.
 */
export const invalidationSets = {
  /** A sale changes stock, low-stock status, sales history and the audit trail. */
  recordSale: [
    queryKeys.products.all,
    queryKeys.inventory.all,
    queryKeys.audit.all,
  ],

  /** A sweep only writes audit events. */
  inventoryCheck: [queryKeys.inventory.all, queryKeys.audit.all],

  /** A new proposal is a new order, and appears in the proposals list. */
  createProposal: [
    queryKeys.proposals.all,
    queryKeys.orders.all,
    queryKeys.audit.all,
  ],

  /**
   * Approval commits money: the order changes, it leaves the proposals list,
   * and today's spend goes up.
   */
  approveOrder: [
    queryKeys.orders.all,
    queryKeys.proposals.all,
    queryKeys.spending.all,
    queryKeys.audit.all,
  ],

  /** Rejection changes the order and removes it from proposals. Spend unchanged. */
  rejectOrder: [
    queryKeys.orders.all,
    queryKeys.proposals.all,
    queryKeys.audit.all,
  ],
}
