import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Icon } from '@/components/icons/Icons'
import { StatusChip } from '@/components/ui/StatusChip'
import { LoadingSequence } from '@/components/ui/LoadingSequence'
import { useCreateProposal, useProduct } from '@/hooks'
import { formatInr } from '@/utils/format'
import { useToast } from '@/context/ToastContext'

export function ProductDetail() {
  const { productId } = useParams<{ productId: string }>()
  const id = Number(productId)
  const navigate = useNavigate()
  const { showToast } = useToast()

  const productQuery = useProduct(isNaN(id) ? undefined : id)
  const createProposal = useCreateProposal()
  const [isGenerating, setIsGenerating] = useState(false)

  const product = productQuery.data

  if (productQuery.isPending) {
    return (
      <div className="panel">
        <LoadingSequence steps={['Loading product details...']} />
      </div>
    )
  }

  if (productQuery.isError || !product) {
    return (
      <div>
        <span className="back-link" onClick={() => navigate('/inventory')}>
          <Icon name="arrowLeft" /> Inventory
        </span>
        <div className="empty-state">Product not found.</div>
      </div>
    )
  }

  const isLowStock =
    product.is_low_stock || product.current_stock < product.reorder_threshold
  const statusText = isLowStock ? 'LOW STOCK' : 'Healthy'

  const sales = product.recent_sales || [
    { date: '2026-08-26', quantity_sold: 5 },
    { date: '2026-08-25', quantity_sold: 7 },
    { date: '2026-08-24', quantity_sold: 6 },
    { date: '2026-08-23', quantity_sold: 8 },
    { date: '2026-08-22', quantity_sold: 9 },
    { date: '2026-08-21', quantity_sold: 7 },
  ]

  const totalSold = sales.reduce((sum, s) => sum + s.quantity_sold, 0)
  const avgDaily = sales.length ? +(totalSold / sales.length).toFixed(1) : null
  const coverage =
    avgDaily && avgDaily > 0 ? +(product.current_stock / avgDaily).toFixed(1) : null

  const handleGenerateProposal = () => {
    setIsGenerating(true)
    createProposal.mutate(product.id, {
      onSuccess: (proposal) => {
        showToast('Proposal ready')
        setIsGenerating(false)
        navigate(`/recommendations/${proposal.order_id}`)
      },
      onError: (err) => {
        showToast(`Failed to generate proposal: ${err.message}`)
        setIsGenerating(false)
      },
    })
  }

  return (
    <div>
      <span
        className="back-link"
        id="backToInv"
        onClick={() => navigate('/inventory')}
      >
        <Icon name="arrowLeft" /> Inventory
      </span>

      <div className="page-header">
        <div>
          <h1>{product.name}</h1>
        </div>
      </div>

      {isGenerating ? (
        <div className="panel">
          <LoadingSequence
            steps={[
              'Analyzing sales history...',
              'Evaluating suppliers...',
              'Validating recommendation...',
            ]}
          />
        </div>
      ) : (
        <div className="detail-grid">
          {/* Left Column: Facts & Sales History */}
          <div>
            <div className="section-label" style={{ marginTop: 0 }}>
              Facts
            </div>
            <div className="panel" style={{ marginBottom: '16px' }}>
              <div className="kv-row">
                <span className="k">Current Stock</span>
                <span className="v mono">
                  {product.current_stock} {product.unit}
                </span>
              </div>
              <div className="kv-row">
                <span className="k">Reorder Threshold</span>
                <span className="v mono">
                  {product.reorder_threshold} {product.unit}
                </span>
              </div>
              <div className="kv-row">
                <span className="k">Status</span>
                <StatusChip
                  status={statusText}
                  variant={isLowStock ? 'low' : 'healthy'}
                />
              </div>
              <div className="kv-row">
                <span className="k">Average daily sales</span>
                <span className="v mono">
                  {avgDaily !== null
                    ? `${avgDaily} ${product.unit}/day`
                    : 'Not enough data'}
                </span>
              </div>
              <div className="kv-row">
                <span className="k">Estimated stock coverage</span>
                <span className="v mono">
                  {coverage !== null ? `${coverage} days` : 'Not enough data'}
                </span>
              </div>
            </div>

            <div className="section-label">Sales history</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Units Sold</th>
                  </tr>
                </thead>
                <tbody>
                  {sales.map((s, i) => (
                    <tr key={i}>
                      <td>{s.date}</td>
                      <td className="mono">{s.quantity_sold}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Right Column: Suppliers & Proposal Trigger */}
          <div>
            <div className="section-label" style={{ marginTop: 0 }}>
              Suppliers
            </div>
            <div className="panel">
              {product.suppliers && product.suppliers.length > 0 ? (
                product.suppliers.map((s) => (
                  <div className="supplier-row" key={s.id}>
                    <span className="name">{s.name}</span>
                    <span className="terms mono">
                      {formatInr(s.price_per_unit_paise)}/unit · {s.delivery_days} days
                    </span>
                  </div>
                ))
              ) : (
                <div style={{ color: 'var(--text-soft)', padding: '8px 0' }}>
                  No suppliers configured.
                </div>
              )}
            </div>

            <div style={{ marginTop: '16px' }}>
              <button
                type="button"
                className="btn btn-primary btn-block"
                id="btnGenProposal"
                onClick={handleGenerateProposal}
                disabled={isGenerating}
              >
                <Icon name="sparkles" /> Generate AI Proposal
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
