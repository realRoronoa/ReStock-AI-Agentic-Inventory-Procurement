/**
 * Server-state hooks, one per screen concern.
 *
 * All read hooks return TanStack Query results, so every consumer gets
 * `isPending` / `isError` / `error` / `refetch` for free and can render the
 * loading and error states the design specifies.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError } from '@/api/client'
import { api } from '@/api/endpoints'
import { invalidationSets, queryKeys } from '@/hooks/queryKeys'
import type {
  AppSettings,
  AuditActor,
  AuditEvent,
  AuditTrail,
  Health,
  InventoryCheckResult,
  LowStockProduct,
  OrderSummary,
  Product,
  ProductDetail,
  Proposal,
  RecordSaleInput,
  SaleResult,
  SpendSummary,
} from '@/types/api'

export * from '@/hooks/useOrders'
export { queryKeys, invalidationSets } from '@/hooks/queryKeys'

// --- meta -------------------------------------------------------------------

/**
 * Server health and integration readiness.
 *
 * Worth surfacing: if `integrations.llm_configured` is false, proposal creation
 * will return 503, and the UI can say so up front instead of letting a merchant
 * click into a failure.
 */
export function useHealth() {
  return useQuery<Health>({
    queryKey: queryKeys.health,
    queryFn: () => api.meta.health(),
    // Reflects deployment state, which changes rarely.
    staleTime: 60_000,
  })
}

/** Read-only guardrails and configuration. Never contains a secret. */
export function useSettings() {
  return useQuery<AppSettings>({
    queryKey: queryKeys.settings,
    queryFn: () => api.meta.settings(),
    staleTime: 5 * 60_000,
  })
}

// --- products ---------------------------------------------------------------

export function useProducts(params?: { lowStock?: boolean }) {
  return useQuery<Product[]>({
    queryKey: queryKeys.products.list(params?.lowStock),
    queryFn: () => api.products.list(params),
  })
}

export function useProduct(
  productId: number | undefined,
  params?: { salesDays?: number },
) {
  return useQuery<ProductDetail>({
    queryKey: queryKeys.products.detail(productId as number, params?.salesDays),
    queryFn: () => api.products.detail(productId as number, params),
    enabled: productId !== undefined,
  })
}

// --- inventory --------------------------------------------------------------

/** Read-only low-stock list. Writes nothing, so safe to render anywhere. */
export function useLowStock() {
  return useQuery<LowStockProduct[]>({
    queryKey: queryKeys.inventory.lowStock,
    queryFn: () => api.inventory.lowStock(),
  })
}

/**
 * Run an inventory sweep.
 *
 * A mutation rather than a query because it writes audit events — it should be
 * triggered by a user action, not by rendering.
 */
export function useInventoryCheck() {
  const queryClient = useQueryClient()

  return useMutation<InventoryCheckResult, ApiError, void>({
    mutationFn: () => api.inventory.check(),
    retry: false,
    onSuccess: () => {
      for (const key of invalidationSets.inventoryCheck) {
        void queryClient.invalidateQueries({ queryKey: key })
      }
    },
  })
}

// --- sales ------------------------------------------------------------------

/**
 * Record a sale.
 *
 * Deliberately **not** optimistic. The backend owns stock, and it can refuse a
 * sale that exceeds what is on hand — an optimistic decrement would show a
 * number that then jumps back, and could briefly display stock the merchant
 * does not have.
 */
export function useRecordSale() {
  const queryClient = useQueryClient()

  return useMutation<SaleResult, ApiError, RecordSaleInput>({
    mutationFn: (input) => api.sales.record(input),
    retry: false,
    onSuccess: () => {
      for (const key of invalidationSets.recordSale) {
        void queryClient.invalidateQueries({ queryKey: key })
      }
    },
  })
}

// --- proposals --------------------------------------------------------------

export function useProposals(params?: { limit?: number; offset?: number }) {
  return useQuery<OrderSummary[]>({
    queryKey: queryKeys.proposals.list(params?.limit, params?.offset),
    queryFn: () => api.proposals.list(params),
  })
}

/**
 * Generate a proposal for a product.
 *
 * Two LLM calls, so this is slow (seconds) — the design's generating state
 * matters here. `retry: false` because a retry means paying for the model twice
 * and the failure is usually deterministic (not low stock, no supplier, guardrail).
 */
export function useCreateProposal() {
  const queryClient = useQueryClient()

  return useMutation<Proposal, ApiError, number>({
    mutationFn: (productId) => api.proposals.create(productId),
    retry: false,
    onSuccess: () => {
      for (const key of invalidationSets.createProposal) {
        void queryClient.invalidateQueries({ queryKey: key })
      }
    },
  })
}

// --- spending ---------------------------------------------------------------

export function useSpending(params?: { historyDays?: number }) {
  return useQuery<SpendSummary>({
    queryKey: queryKeys.spending.summary(params?.historyDays),
    queryFn: () => api.spending.summary(params),
  })
}

// --- audit ------------------------------------------------------------------

export function useAudit(params?: {
  orderId?: number
  actor?: AuditActor
  action?: string
  limit?: number
  offset?: number
}) {
  return useQuery<AuditEvent[]>({
    queryKey: queryKeys.audit.list(params),
    queryFn: () => api.audit.list(params),
  })
}

/** One order's decision chain, oldest first. */
export function useAuditTrail(orderId: number | undefined) {
  return useQuery<AuditTrail>({
    queryKey: queryKeys.audit.trail(orderId as number),
    queryFn: () => api.audit.trail(orderId as number),
    enabled: orderId !== undefined,
  })
}
