import type { OrderStatus } from '@/types/api'

export type ChipVariant =
  | 'low'
  | 'healthy'
  | 'proposed'
  | 'approved'
  | 'paid'
  | 'failed'
  | 'reversed'

interface StatusChipProps {
  status: OrderStatus | 'LOW STOCK' | 'Healthy' | 'Low stock' | string
  variant?: ChipVariant
}

export function StatusChip({ status, variant }: StatusChipProps) {
  let v: ChipVariant = variant || 'healthy'
  const normalized = status.toLowerCase()

  if (!variant) {
    if (normalized.includes('low')) v = 'low'
    else if (normalized.includes('health')) v = 'healthy'
    else if (normalized === 'proposed' || normalized === 'waiting for approval') v = 'proposed'
    else if (normalized === 'approved') v = 'approved'
    else if (normalized === 'paid' || normalized === 'processed') v = 'paid'
    else if (normalized === 'failed') v = 'failed'
    else if (normalized === 'reversed' || normalized === 'rejected') v = 'reversed'
  }

  return (
    <span className={`status-chip ${v}`}>
      {status}
    </span>
  )
}
