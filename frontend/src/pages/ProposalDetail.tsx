import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Icon } from '@/components/icons/Icons'
import { LoadingSequence } from '@/components/ui/LoadingSequence'
import { ApprovalModal } from '@/components/modals/ApprovalModal'
import { useOrder, useRejectOrder } from '@/hooks/useOrders'
import { useProduct } from '@/hooks'
import { formatInr } from '@/utils/format'
import { useToast } from '@/context/ToastContext'

export function ProposalDetail() {
  const { orderId } = useParams<{ orderId: string }>()
  const id = Number(orderId)
  const navigate = useNavigate()
  const { showToast } = useToast()

  const orderQuery = useOrder(isNaN(id) ? undefined : id)
  const order = orderQuery.data
  const productQuery = useProduct(order?.product?.id)
  const product = productQuery.data
  const rejectOrder = useRejectOrder()

  const [isApprovalModalOpen, setIsApprovalModalOpen] = useState(false)

  if (orderQuery.isPending) {
    return (
      <div className="panel">
        <LoadingSequence steps={['Loading proposal dossier…']} />
      </div>
    )
  }

  if (orderQuery.isError || !order) {
    return (
      <div>
        <span className="back-link" onClick={() => navigate('/recommendations')}>
          <Icon name="arrowLeft" /> Proposals
        </span>
        <div className="empty-state">Proposal not found.</div>
      </div>
    )
  }

  const handleReject = () => {
    rejectOrder.mutate(
      { orderId: order.id, input: { reason: 'Merchant declined proposal' } },
      {
        onSuccess: () => {
          showToast('Proposal rejected — no order was created')
          navigate('/recommendations')
        },
        onError: (err) => {
          showToast(`Rejection failed: ${err.message}`)
        },
      },
    )
  }

  const alternatives = product?.suppliers || [
    {
      id: order.supplier.id,
      product_id: order.product.id,
      name: order.supplier.name,
      price_per_unit_paise: order.unit_price_paise,
      price_per_unit: order.unit_price,
      delivery_days: order.supplier.delivery_days || 2,
      razorpay_fund_account_id: null,
      has_fund_account: order.supplier.has_fund_account,
    },
  ]

  const chosenSupplier = alternatives.find(
    (s) => s.id === order.supplier.id || s.name === order.supplier.name,
  ) || {
    id: order.supplier.id,
    product_id: order.product.id,
    name: order.supplier.name,
    price_per_unit_paise: order.unit_price_paise,
    price_per_unit: order.unit_price,
    delivery_days: order.supplier.delivery_days || 2,
    razorpay_fund_account_id: null,
    has_fund_account: order.supplier.has_fund_account,
  }

  const otherSuppliers = alternatives.filter(
    (s) => s.id !== chosenSupplier.id,
  )

  const demandReasoning =
    order.forecast_reasoning ||
    `Recent sales show an average daily demand pattern. Current inventory is below the normal operating level, so the agent recommends replenishing ${order.quantity} units to cover expected demand during the next cycle.`

  const supplierReasoning =
    order.supplier_reasoning ||
    `${chosenSupplier.name} offers the optimal balance of unit price and delivery lead time among available suppliers.`

  return (
    <div>
      <span
        className="back-link"
        id="backToProposals"
        onClick={() => navigate('/recommendations')}
      >
        <Icon name="arrowLeft" /> Proposals
      </span>

      <div className="page-header">
        <div>
          <h1>Purchase Proposal</h1>
        </div>
      </div>

      <div className="doc" id="proposalDoc">
        <div className="doc-banner">
          <Icon name="alert" /> Action required — this purchase requires your approval.
        </div>

        <div className="doc-body">
          {/* Product Section */}
          <div className="doc-section">
            <div className="stitle">Product</div>
            <div className="product-box">
              <div className="pmark">
                <Icon name="box" />
              </div>
              <div>
                <div className="pname">{order.product.name}</div>
                <div className="prow">
                  Current stock: {order.product.current_stock} {order.product.unit}{' '}
                  &nbsp;·&nbsp; Reorder threshold: {order.product.reorder_threshold}{' '}
                  {order.product.unit}
                </div>
              </div>
            </div>
          </div>

          {/* AI Demand Forecast */}
          <div className="doc-section">
            <div className="stitle">
              <Icon name="brain" /> AI Demand Forecast
            </div>
            <div className="forecast-num">
              <div className="n">{order.quantity}</div>
              <div className="u">{order.product.unit} recommended</div>
            </div>
            <div className="why-label">Why?</div>
            <div className="reason-box">{demandReasoning}</div>
          </div>

          {/* Supplier Recommendation */}
          <div className="doc-section">
            <div className="stitle">
              <Icon name="truck" /> Supplier Recommendation
            </div>
            <div className="supplier-pick">
              <div className="head">
                <Icon name="check" /> {chosenSupplier.name}
              </div>
              <div className="terms mono">
                {formatInr(chosenSupplier.price_per_unit_paise)} / unit · Delivery:{' '}
                {chosenSupplier.delivery_days} days
              </div>
              <div className="why-label">Why this supplier?</div>
              <div className="reason-box plain">{supplierReasoning}</div>
            </div>
          </div>

          {/* Supplier Comparison */}
          {otherSuppliers.length > 0 && (
            <div className="doc-section">
              <div className="stitle">Supplier Comparison</div>
              <div className="panel" style={{ padding: '4px 20px' }}>
                {alternatives.map((a) => (
                  <div className="supplier-row" key={a.id}>
                    <span className="name">
                      {a.name}
                      {a.id === chosenSupplier.id && (
                        <> &nbsp;<Icon name="check" style={{ display: 'inline', width: 14, height: 14 }} /></>
                      )}
                    </span>
                    <span className="terms mono">
                      {formatInr(a.price_per_unit_paise)}/unit · {a.delivery_days} days
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="divider" />

          {/* Order Summary */}
          <div className="doc-section">
            <div className="stitle">Order Summary</div>
            <div className="order-summary-row">
              <span>Quantity</span>
              <b className="mono">
                {order.quantity} {order.product.unit}
              </b>
            </div>
            <div className="order-summary-row">
              <span>Price per unit</span>
              <b className="mono">{formatInr(order.unit_price_paise)}</b>
            </div>
            <div className="order-summary-row total">
              <span>Total</span>
              <span className="mono">{formatInr(order.amount_paise)}</span>
            </div>
          </div>

          {/* Human Approval Notice */}
          <div className="approval-notice">
            <span className="glyph">
              <Icon name="alert" />
            </span>
            <div>
              <div className="t">Human approval required</div>
              <div className="d">
                The AI cannot authorize this payment. You decide whether this purchase happens.
              </div>
            </div>
          </div>

          {/* Action Row */}
          <div className="action-row">
            <button
              type="button"
              className="btn btn-secondary"
              id="btnReject"
              onClick={handleReject}
              disabled={rejectOrder.isPending}
            >
              {rejectOrder.isPending ? 'Rejecting…' : 'Reject'}
            </button>
            <button
              type="button"
              className="btn btn-approve"
              id="btnApprove"
              onClick={() => setIsApprovalModalOpen(true)}
            >
              <Icon name="handshake" /> Approve Purchase
            </button>
          </div>
        </div>
      </div>

      {isApprovalModalOpen && (
        <ApprovalModal
          orderId={order.id}
          productName={order.product.name}
          quantity={order.quantity}
          unit={order.product.unit}
          supplierName={order.supplier.name}
          amountPaise={order.amount_paise}
          onClose={() => setIsApprovalModalOpen(false)}
          onApproved={(approvedOrderId) => {
            setIsApprovalModalOpen(false)
            navigate(`/orders/${approvedOrderId}`)
          }}
        />
      )}
    </div>
  )
}
