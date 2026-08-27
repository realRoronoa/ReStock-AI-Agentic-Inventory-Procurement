/**
 * Order queries and the two safety-critical mutations.
 *
 * The interesting logic here is `useOrder`'s polling, and the deliberate
 * absence of automatic retries on approval.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/endpoints'
import { ApiError } from '@/api/client'
import { invalidationSets, queryKeys } from '@/hooks/queryKeys'
import type {
  ApprovalResult,
  OrderDetail,
  OrderStatus,
  OrderSummary,
  RejectOrderInput,
} from '@/types/api'

/** Statuses from which nothing further will happen on its own. */
const TERMINAL_STATUSES: ReadonlySet<OrderStatus> = new Set<OrderStatus>([
  'paid',
  'failed',
  'reversed',
  'rejected',
])

export function isTerminal(status: OrderStatus): boolean {
  return TERMINAL_STATUSES.has(status)
}

/**
 * True while the backend is waiting on RazorpayX.
 *
 * This is what the design's "processing" state should key off. There is no
 * `processing` order status: it is `approved` plus a payout in flight.
 */
export function isAwaitingSettlement(order: OrderDetail): boolean {
  return order.status === 'approved' && order.payment.awaiting_settlement
}

/** Interval for polling an unsettled order. */
const SETTLEMENT_POLL_MS = 5_000

/** Give up polling after this long, so a stuck payout is not polled forever. */
const SETTLEMENT_POLL_CEILING_MS = 5 * 60 * 1000

export function useOrders(filters?: {
  status?: OrderStatus
  productId?: number
  supplierId?: number
  limit?: number
  offset?: number
}) {
  return useQuery<OrderSummary[]>({
    queryKey: queryKeys.orders.list(filters),
    queryFn: () => api.orders.list(filters),
  })
}

/**
 * One order, polled while its payout is unresolved.
 *
 * Settlement arrives asynchronously by webhook, so after approval the order sits
 * in `approved` until RazorpayX reports back. Polling stops the moment the
 * status is terminal — no interval, no wasted requests once the answer is known.
 *
 * `payment.outcome_unknown` also stops polling: that state needs a human, and no
 * amount of refetching will resolve it.
 */
export function useOrder(
  orderId: number | undefined,
  options?: { poll?: boolean },
) {
  const poll = options?.poll ?? true
  const startedAt = Date.now()

  return useQuery<OrderDetail>({
    queryKey: queryKeys.orders.detail(orderId as number),
    queryFn: () => api.orders.detail(orderId as number),
    enabled: orderId !== undefined,
    refetchInterval: (query) => {
      if (!poll) return false

      const order = query.state.data
      if (!order) return false

      if (isTerminal(order.status)) return false
      // Unknown outcome will not resolve by itself; it needs reconciliation.
      if (order.payment.outcome_unknown) return false
      if (!isAwaitingSettlement(order)) return false

      // Stop after the ceiling so a genuinely stuck payout does not generate
      // requests indefinitely. The user can refetch manually.
      if (Date.now() - startedAt > SETTLEMENT_POLL_CEILING_MS) return false

      return SETTLEMENT_POLL_MS
    },
  })
}

/**
 * Approve an order and initiate its payout.
 *
 * **`retry: false` is deliberate and important.** This is a financial mutation.
 * An automatic retry after an ambiguous failure is exactly how a duplicate
 * payment happens. The backend's idempotency key makes a *deliberate* retry
 * safe, but the decision to retry belongs to a human, not to a query library.
 *
 * Callers must disable their trigger while `isPending` — see §15 of the brief.
 * A second call would return 409 rather than paying twice, but the UI should not
 * rely on that as its only defence.
 */
export function useApproveOrder() {
  const queryClient = useQueryClient()

  return useMutation<ApprovalResult, ApiError, number>({
    mutationFn: (orderId: number) => api.orders.approve(orderId),
    retry: false,
    onSuccess: (result) => {
      // Seed the detail cache so a navigation straight to the order shows the
      // approved state without a refetch flash.
      queryClient.setQueryData(
        queryKeys.orders.detail(result.order.id),
        result.order,
      )
      for (const key of invalidationSets.approveOrder) {
        void queryClient.invalidateQueries({ queryKey: key })
      }
    },
    onError: (error) => {
      // A 409 means someone (or something) already approved this. The server
      // state is now more authoritative than anything cached, so refresh rather
      // than leaving a stale "proposed" on screen.
      if (error.isConflict) {
        for (const key of invalidationSets.approveOrder) {
          void queryClient.invalidateQueries({ queryKey: key })
        }
      }
    },
  })
}

/** Decline a proposal. Not financial, but still single-shot. */
export function useRejectOrder() {
  const queryClient = useQueryClient()

  return useMutation<
    OrderDetail,
    ApiError,
    { orderId: number; input?: RejectOrderInput }
  >({
    mutationFn: ({ orderId, input }) => api.orders.reject(orderId, input),
    retry: false,
    onSuccess: (order) => {
      queryClient.setQueryData(queryKeys.orders.detail(order.id), order)
      for (const key of invalidationSets.rejectOrder) {
        void queryClient.invalidateQueries({ queryKey: key })
      }
    },
  })
}
