/**
 * Turning backend errors into something a merchant can act on.
 *
 * The backend already writes decent messages, so the default is to show them.
 * This module exists for the cases where the *situation* needs more explanation
 * than a single sentence — chiefly the payment states, where "it failed" and
 * "we do not know" require very different responses from the user.
 */

import { ApiError } from '@/api/client'

export interface FriendlyError {
  /** Short heading. */
  title: string
  /** Body text. Usually the backend message, which is already human-readable. */
  message: string
  /** What the user can do about it, if anything. */
  action?: string
  /**
   * Whether retrying the same action makes sense.
   *
   * False for business rules (a guardrail will reject it again) and, crucially,
   * for anything payment-related where the outcome is uncertain.
   */
  retryable: boolean
  /** True when this is not really a failure — e.g. a duplicate approval. */
  benign?: boolean
  requestId?: string
}

const FALLBACK: FriendlyError = {
  title: 'Something went wrong',
  message: 'The action could not be completed. Please try again.',
  retryable: true,
}

/**
 * Per-code overrides, for codes where extra context genuinely helps.
 *
 * Anything not listed falls through to the backend message, which is fine.
 */
const BY_CODE: Record<string, Omit<FriendlyError, 'message' | 'requestId'>> = {
  // --- approval conflicts: the system working, not breaking ---
  PAYOUT_ALREADY_REQUESTED: {
    title: 'Already approved',
    action: 'No action needed. This order has already been sent for payment.',
    retryable: false,
    benign: true,
  },
  ORDER_NOT_PROPOSED: {
    title: 'No longer awaiting approval',
    action: 'Refresh to see the current state of this order.',
    retryable: false,
    benign: true,
  },
  ORDER_NOT_REJECTABLE: {
    title: 'Cannot be declined',
    action: 'Only proposals awaiting approval can be declined.',
    retryable: false,
    benign: true,
  },

  // --- the one that needs a human ---
  PAYOUT_OUTCOME_UNKNOWN: {
    title: 'Payment outcome unknown',
    action:
      'Check the RazorpayX dashboard before doing anything else. This will not be retried automatically, because a lost response does not mean the money did not move.',
    retryable: false,
  },
  PAYMENT_TIMEOUT: {
    title: 'Payment request timed out',
    action:
      'Do not retry yet. The payout may have been created. Check the RazorpayX dashboard, then refresh this order.',
    retryable: false,
  },

  // --- guardrails: recoverable, and the proposal survives ---
  DAILY_SPEND_LIMIT_EXCEEDED: {
    title: 'Daily spend limit reached',
    action:
      'The proposal is still valid and can be approved once today’s committed spend falls, or tomorrow.',
    retryable: false,
  },
  ORDER_SPEND_LIMIT_EXCEEDED: {
    title: 'Over the per-order limit',
    action:
      'This order is larger than the configured per-order cap. A smaller quantity or a cheaper supplier would be needed.',
    retryable: false,
  },
  QUANTITY_LIMIT_EXCEEDED: {
    title: 'Quantity over the limit',
    action: 'The recommended quantity exceeds the configured maximum.',
    retryable: false,
  },

  // --- proposal preconditions ---
  PRODUCT_NOT_LOW_STOCK: {
    title: 'Not below the reorder threshold',
    action: 'This product does not need reordering yet.',
    retryable: false,
  },
  NO_SUPPLIERS: {
    title: 'No suppliers',
    action: 'Add a supplier for this product before ordering.',
    retryable: false,
  },
  NO_SALES_HISTORY: {
    title: 'No sales history',
    action: 'Record some sales so demand can be forecast.',
    retryable: false,
  },
  SUPPLIER_NO_FUND_ACCOUNT: {
    title: 'Supplier cannot be paid',
    action:
      'This supplier has no RazorpayX fund account. Add its bank details before ordering from it.',
    retryable: false,
  },
  INSUFFICIENT_STOCK: {
    title: 'Not enough stock',
    action: 'Enter a quantity no larger than the stock on hand.',
    retryable: false,
  },

  // --- AI failures: nothing was created, trying again is reasonable ---
  FORECAST_UNAVAILABLE: {
    title: 'Could not reach the forecasting model',
    action: 'No order was created. Try again in a moment.',
    retryable: true,
  },
  SUPPLIER_SELECTION_UNAVAILABLE: {
    title: 'Could not reach the supplier model',
    action: 'No order was created. Try again in a moment.',
    retryable: true,
  },
  FORECAST_INVALID: {
    title: 'Recommendation rejected',
    action:
      'The model returned a figure the backend refused to act on. No order was created. Trying again may produce a valid recommendation.',
    retryable: true,
  },
  SUPPLIER_SELECTION_INVALID: {
    title: 'Supplier choice rejected',
    action:
      'The model recommended a supplier that failed validation. No order was created.',
    retryable: true,
  },

  // --- configuration: nothing the merchant can fix ---
  AGENT_NOT_CONFIGURED: {
    title: 'AI is not configured',
    action:
      'This server has no LLM provider configured, so recommendations cannot be generated. Nothing was invented in its place.',
    retryable: false,
  },
  PAYMENT_NOT_CONFIGURED: {
    title: 'Payments are not configured',
    action:
      'This server has no RazorpayX credentials, so no payout can be created. Nothing was charged and no payment was simulated.',
    retryable: false,
  },

  // --- transport ---
  NETWORK_ERROR: {
    title: 'Cannot reach the server',
    action: 'Check that the backend is running, then try again.',
    retryable: true,
  },
  DATABASE_ERROR: {
    title: 'Database unavailable',
    action: 'Try again shortly.',
    retryable: true,
  },
  VALIDATION_ERROR: {
    title: 'Invalid input',
    retryable: false,
  },
}

export function toFriendlyError(error: unknown): FriendlyError {
  if (!(error instanceof ApiError)) {
    return FALLBACK
  }

  const override = BY_CODE[error.code]
  if (override) {
    return {
      ...override,
      message: error.message,
      requestId: error.requestId,
    }
  }

  return {
    title: error.status >= 500 ? 'Server error' : 'Could not complete that',
    message: error.message,
    retryable: error.isRetryable,
    requestId: error.requestId,
  }
}

/**
 * True for errors that mean "this already happened" rather than "this broke".
 *
 * A duplicate approval should read as reassurance, not as an incident.
 */
export function isBenignConflict(error: unknown): boolean {
  return toFriendlyError(error).benign === true
}

/** Pull a numeric field out of an error's `details`, e.g. a breached limit. */
export function errorDetailNumber(
  error: unknown,
  key: string,
): number | undefined {
  if (!(error instanceof ApiError) || !error.details) return undefined
  const value = error.details[key]
  return typeof value === 'number' ? value : undefined
}
