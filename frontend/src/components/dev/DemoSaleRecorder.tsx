import { useState } from 'react'
import { Icon } from '@/components/icons/Icons'
import { useRecordSale } from '@/hooks'
import { useToast } from '@/context/ToastContext'
import type { Product } from '@/types/api'

interface DemoSaleRecorderProps {
  products: Product[]
}

export function DemoSaleRecorder({ products }: DemoSaleRecorderProps) {
  const [selectedProductId, setSelectedProductId] = useState<number>(
    products[0]?.id || 1,
  )
  const [qty, setQty] = useState<number>(2)
  const recordSale = useRecordSale()
  const { showToast } = useToast()

  const handleRecord = () => {
    if (!selectedProductId || qty < 1) return
    const prod = products.find((p) => p.id === selectedProductId)
    const wasLowStock = prod?.is_low_stock

    recordSale.mutate(
      {
        product_id: selectedProductId,
        quantity: qty,
      },
      {
        onSuccess: (res) => {
          if (res.is_low_stock && !wasLowStock) {
            showToast(`Sale recorded — ${res.product_name} is now low stock`)
          } else {
            showToast(`Sale recorded: ${qty} units of ${res.product_name}`)
          }
        },
        onError: (err) => {
          showToast(`Sale failed: ${err.message || 'Insufficient stock'}`)
        },
      },
    )
  }

  return (
    <div className="dev-panel">
      <div className="dp-head">
        <Icon name="beaker" />
        <span className="dp-title">Demo sales — development only</span>
      </div>
      <div className="dp-desc">
        No live POS is connected yet. Use this to simulate a sale and see stock,
        low-stock detection, and recommendations react in real time.
      </div>
      <div className="dev-form-row">
        <div className="field">
          <label style={{ fontSize: '11.5px' }}>Product</label>
          <select
            id="demoSaleProduct"
            value={selectedProductId}
            onChange={(e) => setSelectedProductId(Number(e.target.value))}
          >
            {products.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ maxWidth: '120px' }}>
          <label style={{ fontSize: '11.5px' }}>Quantity sold</label>
          <input
            type="number"
            id="demoSaleQty"
            value={qty}
            min={1}
            onChange={(e) => setQty(Math.max(1, parseInt(e.target.value) || 1))}
          />
        </div>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          id="btnRecordSale"
          onClick={handleRecord}
          disabled={recordSale.isPending}
        >
          {recordSale.isPending ? 'Recording…' : 'Record Sale'}
        </button>
      </div>
    </div>
  )
}
