import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Icon } from '@/components/icons/Icons'
import { LoadingSequence } from '@/components/ui/LoadingSequence'
import { useCreateProposal, useProduct } from '@/hooks'
import { useOrder } from '@/hooks/useOrders'
import { formatInr } from '@/utils/format'
import { useToast } from '@/context/ToastContext'

export function AlternativeSupplier() {
  const { orderId } = useParams<{ orderId: string }>()
  const id = Number(orderId)
  const navigate = useNavigate()
  const { showToast } = useToast()

  const orderQuery = useOrder(isNaN(id) ? undefined : id)
  const order = orderQuery.data
  const productQuery = useProduct(order?.product?.id)
  const product = productQuery.data
  const createProposal = useCreateProposal()
  const [isGenerating, setIsGenerating] = useState(false)

  if (orderQuery.isPending || productQuery.isPending) {
    return (
      <div className="panel">
        <LoadingSequence steps={['Loading supplier options…']} />
      </div>
    )
  }

  if (orderQuery.isError || !order || !product) {
    return (
      <div>
        <span className="back-link" onClick={() => navigate('/orders')}>
          <Icon name="arrowLeft" /> Orders
        </span>
        <div className="empty-state">Order or product not found.</div>
      </div>
    )
  }

  const suppliers = product.suppliers || []
  const original = suppliers.find(
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

  const alternatives = suppliers.filter(
    (s) => s.id !== original.id,
  )

  const handleCreateNewProposal = () => {
    setIsGenerating(true)
    createProposal.mutate(product.id, {
      onSuccess: (newProp) => {
        showToast('New proposal ready')
        setIsGenerating(false)
        navigate(`/recommendations/${newProp.order_id}`)
      },
      onError: (err) => {
        showToast(`Failed: ${err.message}`)
        setIsGenerating(false)
      },
    })
  }

  return (
    <div>
      <span
        className="back-link"
        id="backToOrder"
        onClick={() => navigate(`/orders/${order.id}`)}
      >
        <Icon name="arrowLeft" /> Order #{order.id}
      </span>

      <div className="page-header">
        <div>
          <h1>Alternative Supplier</h1>
        </div>
      </div>

      {isGenerating ? (
        <div className="panel">
          <LoadingSequence
            steps={[
              'Analyzing alternative suppliers...',
              'Evaluating terms & stock risk...',
              'Generating new proposal...',
            ]}
          />
        </div>
      ) : (
        <div className="doc">
          <div className="doc-body">
            <div className="stitle">Original Supplier</div>
            <div className="product-box" style={{ marginBottom: '20px' }}>
              <div className="pmark">
                <Icon name="truck" />
              </div>
              <div>
                <div className="pname" style={{ fontSize: '16px' }}>
                  {original.name}
                </div>
                <div className="prow mono">
                  {formatInr(original.price_per_unit_paise)}/unit · {original.delivery_days} days
                </div>
              </div>
            </div>

            <div className="fail-banner" style={{ marginBottom: '20px' }}>
              <Icon name="alert" />
              <div>
                <div className="t">Payment failed.</div>
              </div>
            </div>

            <div className="stitle">Other Available Suppliers</div>
            {alternatives.length > 0 ? (
              alternatives.map((a) => (
                <div
                  className="supplier-pick"
                  style={{
                    marginBottom: '12px',
                    borderColor: 'var(--line)',
                    background: 'var(--panel)',
                  }}
                  key={a.id}
                >
                  <div className="head" style={{ color: 'var(--text)' }}>
                    {a.name}
                  </div>
                  <div className="terms mono">
                    {formatInr(a.price_per_unit_paise)}/unit · {a.delivery_days} days
                  </div>
                  <div className="action-row" style={{ marginTop: '8px' }}>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      data-newprop={product.id}
                      onClick={handleCreateNewProposal}
                    >
                      Create New Proposal
                    </button>
                  </div>
                </div>
              ))
            ) : (
              <div style={{ color: 'var(--text-soft)', padding: '10px 0' }}>
                No other suppliers configured for this product.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
