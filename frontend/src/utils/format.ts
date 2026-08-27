/**
 * Display formatting.
 *
 * The money rules are the ones to get right. The backend sends every amount
 * twice: an integer `*_paise` field and a rupee *string*. Format from the paise
 * integer — it is exact, and it cannot be accidentally treated as rupees because
 * the field name says paise.
 *
 * Never do this:
 *   `₹${order.amount_paise}`         → shows ₹360000 instead of ₹3,600
 *   `parseFloat(order.amount) * 2`   → reintroduces float error on money
 */

import type { AuditActor, OrderStatus, Paise, StockStatus } from '@/types/api'

const PAISE_PER_RUPEE = 100

const inrFormatter = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const inrCompactFormatter = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
})

/**
 * Format integer paise as Indian-format rupees: `360000` → `"₹3,600.00"`.
 *
 * Division by 100 is safe here: the result is only ever displayed, never used
 * for further arithmetic, and paise values in this system are far below the
 * precision limit of a double.
 */
export function formatInr(paise: Paise): string {
  return inrFormatter.format(paise / PAISE_PER_RUPEE)
}

/** Whole rupees, for tight spaces: `360000` → `"₹3,600"`. */
export function formatInrCompact(paise: Paise): string {
  return inrCompactFormatter.format(paise / PAISE_PER_RUPEE)
}

/** Rupees as a bare number string, no symbol: `360000` → `"3,600.00"`. */
export function formatRupeeAmount(paise: Paise): string {
  return new Intl.NumberFormat('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(paise / PAISE_PER_RUPEE)
}

/** `75`, `"litre"` → `"75 litre"`. Units are already singular in the backend. */
export function formatQuantity(quantity: number, unit: string): string {
  return `${new Intl.NumberFormat('en-IN').format(quantity)} ${unit}`
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat('en-IN').format(value)
}

/** `32` → `"32%"`. */
export function formatPercent(value: number, fractionDigits = 0): string {
  return `${value.toFixed(fractionDigits)}%`
}

// --- dates ------------------------------------------------------------------

/**
 * Backend timestamps are UTC with an offset, so `new Date` parses them
 * correctly and the browser renders them in the viewer's local timezone.
 */
export function formatDateTime(iso: string): string {
  return new Intl.DateTimeFormat('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(iso))
}

export function formatTime(iso: string): string {
  return new Intl.DateTimeFormat('en-IN', {
    hour: 'numeric',
    minute: '2-digit',
    month: 'short',
    day: 'numeric',
  }).format(new Date(iso))
}

export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium' }).format(
    new Date(iso),
  )
}

/** Short axis label for a chart: `"27 Aug"`. */
export function formatDayLabel(iso: string): string {
  return new Intl.DateTimeFormat('en-IN', {
    day: 'numeric',
    month: 'short',
  }).format(new Date(iso))
}

export function formatWeekday(iso: string): string {
  return new Intl.DateTimeFormat('en-IN', { weekday: 'short' }).format(
    new Date(iso),
  )
}

/** `"3 minutes ago"`. Falls back to an absolute date beyond a week. */
export function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  const seconds = Math.round((Date.now() - then) / 1000)

  if (seconds < 45) return 'just now'

  const relative = new Intl.RelativeTimeFormat('en-IN', { numeric: 'auto' })
  const thresholds: [Intl.RelativeTimeFormatUnit, number][] = [
    ['minute', 60],
    ['hour', 3600],
    ['day', 86400],
  ]

  for (const [unit, unitSeconds] of thresholds) {
    if (seconds < unitSeconds * (unit === 'day' ? 7 : 60)) {
      return relative.format(-Math.round(seconds / unitSeconds), unit)
    }
  }

  return formatDate(iso)
}

// --- status labels ----------------------------------------------------------

/**
 * Human labels for order status.
 *
 * `approved` reads as "Awaiting payment" because that is what it means to a
 * merchant: authorised, money requested, not yet settled. Calling it "Approved"
 * invites reading it as done.
 */
const ORDER_STATUS_LABELS: Record<OrderStatus, string> = {
  proposed: 'Awaiting approval',
  approved: 'Payment processing',
  paid: 'Paid',
  failed: 'Payment failed',
  reversed: 'Payment reversed',
  rejected: 'Declined',
}

export function orderStatusLabel(status: OrderStatus): string {
  return ORDER_STATUS_LABELS[status]
}

/**
 * Coarse tone per status, for whatever the design uses to signal severity.
 *
 * Returns a semantic name, not a colour — the design owns colours.
 */
export type StatusTone = 'pending' | 'progress' | 'success' | 'danger' | 'warning' | 'neutral'

const ORDER_STATUS_TONES: Record<OrderStatus, StatusTone> = {
  proposed: 'pending',
  approved: 'progress',
  paid: 'success',
  failed: 'danger',
  reversed: 'warning',
  rejected: 'neutral',
}

export function orderStatusTone(status: OrderStatus): StatusTone {
  return ORDER_STATUS_TONES[status]
}

export function stockStatusLabel(status: StockStatus): string {
  return status === 'low_stock' ? 'Low stock' : 'In stock'
}

const AUDIT_ACTOR_LABELS: Record<AuditActor, string> = {
  agent: 'AI agent',
  human: 'You',
  system: 'System',
}

export function auditActorLabel(actor: AuditActor): string {
  return AUDIT_ACTOR_LABELS[actor]
}

/**
 * `PAYOUT_PROCESSED` → `"Payout processed"`.
 *
 * The backend's action vocabulary grows, so this transforms rather than looks up
 * — an unmapped action still renders readably instead of showing a raw constant.
 */
export function auditActionLabel(action: string): string {
  const words = action.toLowerCase().split('_')
  const [first, ...rest] = words
  if (!first) return action
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(' ')
}
