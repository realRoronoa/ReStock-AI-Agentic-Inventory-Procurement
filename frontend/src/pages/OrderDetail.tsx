import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Icon } from '@/components/icons/Icons'
import { StatusChip } from '@/components/ui/StatusChip'
import { LoadingSequence } from '@/components/ui/LoadingSequence'
import { AuditTimeline } from '@/components/ui/AuditTimeline'
import { AuditEventModal } from '@/components/modals/AuditEventModal'
import { useOrder } from '@/hooks/useOrders'
import { useAuditTrail } from '@/hooks'
import { formatInr } from '@/utils/format'
import { useToast } from '@/context/ToastContext'
import type { AuditEvent } from '@/types/api'

export function OrderDetail() {
  const { orderId } = useParams<{ orderId: string }>()
  const id = Number(orderId)
  const navigate = useNavigate()
  const { showToast } = useToast()

  const orderQuery = useOrder(isNaN(id) ? undefined : id)
  const auditTrailQuery = useAuditTrail(isNaN(id) ? undefined : id)
  const [selectedAuditEvent, setSelectedAuditEvent] = useState<AuditEvent | null>(null)

  const order = orderQuery.data

  if (orderQuery.isPending) {
    return (
      <div className="panel">
        <LoadingSequence steps={['Loading order details…']} />
      </div>
    )
  }

  if (orderQuery.isError || !order) {
    return (
      <div>
        <span className="back-link" onClick={() => navigate('/orders')}>
          <Icon name="arrowLeft" /> Orders
        </span>
        <div className="empty-state">Order not found.</div>
      </div>
    )
  }

  const isFailed = order.status === 'failed'
  const isPaid = order.status === 'paid'
  const payoutId = order.payment.payout_id || (order.payment.payout_requested ? 'Requested' : '—')

  const events = auditTrailQuery.data?.entries || []

  const paymentSteps = [
    { label: 'Human approved', done: true, waiting: false },
    { label: 'Payout created', done: true, waiting: false },
    {
      label: isPaid ? 'Payment confirmed' : 'Waiting for Razorpay confirmation',
      done: isPaid,
      waiting: !isPaid,
    },
    {
      label: isPaid ? 'Inventory updated' : 'Waiting for inventory update',
      done: isPaid,
      waiting: !isPaid,
    },
  ]

  return (
    <div>
      <span
        className="back-link"
        id="backToOrders"
        onClick={() => navigate('/orders')}
      >
        <Icon name="arrowLeft" /> Orders
      </span>

      <div className="page-header">
        <div>
          <h1>{isFailed ? 'Payment Failed' : `Order #${order.id}`}</h1>
        </div>
      </div>

      <div className="doc">
        <div className="doc-body">
          {isFailed ? (
            <>
              <div className="fail-banner">
                <Icon name="alert" />
                <div>
                  <div className="t">Supplier payment could not be completed.</div>
                  <div className="d">Inventory has not been updated.</div>
                </div>
              </div>

              <div style={{ marginTop: '20px' }}>
                <div className="kv-row">
                  <span className="k">Order</span>
                  <span className="v mono">#{order.id}</span>
                </div>
                <div className="kv-row">
                  <span className="k">Product</span>
                  <span className="v">{order.product.name}</span>
                </div>
                <div className="kv-row">
                  <span className="k">Supplier</span>
                  <span className="v">{order.supplier.name}</span>
                </div>
                <div className="kv-row">
                  <span className="k">Amount</span>
                  <span className="v mono">{formatInr(order.amount_paise)}</span>
                </div>
                <div className="kv-row">
                  <span className="k">Status</span>
                  <StatusChip status="FAILED" variant="failed" />
                </div>
              </div>

              <div className="payout-id" style={{ marginTop: '18px' }}>
                <div className="l">Razorpay response</div>
                <div className="v">{order.payment.failure_reason || 'Payment could not be processed.'}</div>
              </div>

              <div className="action-row" style={{ justifyContent: 'flex-start' }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  id="btnAltSupplier"
                  onClick={() => navigate(`/orders/${order.id}/alternative`)}
                >
                  Review Alternative Supplier
                </button>
                <button
                  type="button"
                  className="btn btn-ghost"
                  id="btnLeaveFailed"
                  onClick={() => {
                    showToast('Order left as failed')
                    navigate('/orders')
                  }}
                >
                  Leave Order Failed
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="product-box" style={{ marginBottom: '22px' }}>
                <div className="pmark">
                  <Icon name="box" />
                </div>
                <div>
                  <div className="pname">{order.product.name}</div>
                  <div className="prow mono">
                    {order.quantity} units · {order.supplier.name} ·{' '}
                    {formatInr(order.amount_paise)}
                  </div>
                </div>
              </div>

              <div className="stitle">Payment Status</div>
              <div className="checklist">
                {paymentSteps.map((s, idx) => (
                  <div
                    className={`check-row ${s.done ? 'done' : 'waiting'}`}
                    key={idx}
                  >
                    <span className="mark">
                      {s.done ? <Icon name="check" /> : null}
                    </span>
                    <span className="label">{s.label}</span>
                  </div>
                ))}
              </div>

              <div className="payout-id">
                <div className="l">Razorpay Payout ID</div>
                <div className="v">{payoutId}</div>
              </div>

              {!isPaid && (
                <div className="confirm-note" style={{ marginTop: '18px' }}>
                  Payment is still awaiting confirmation. The payout was created, but
                  Razorpay has not confirmed the final result yet.
                </div>
              )}
            </>
          )}

          <div className="stitle" style={{ marginTop: '28px' }}>
            Timeline
          </div>
          <AuditTimeline
            events={events}
            onSelectEvent={(ev) => setSelectedAuditEvent(ev)}
          />
        </div>
      </div>

      <AuditEventModal
        event={selectedAuditEvent}
        onClose={() => setSelectedAuditEvent(null)}
      />
    </div>
  )
}
