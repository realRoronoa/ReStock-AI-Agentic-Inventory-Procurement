import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Icon } from '@/components/icons/Icons'
import { StatusChip } from '@/components/ui/StatusChip'
import { Sparkline } from '@/components/ui/Sparkline'
import { ErrorBanner } from '@/components/ui/ErrorBanner'
import { DemoSaleRecorder } from '@/components/dev/DemoSaleRecorder'
import { LoadingSequence } from '@/components/ui/LoadingSequence'
import { useInventoryCheck, useProducts } from '@/hooks'
import { useToast } from '@/context/ToastContext'

export function Inventory() {
  const navigate = useNavigate()
  const { showToast } = useToast()

  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'All' | 'Low Stock' | 'Healthy'>('All')
  const [simulateError, setSimulateError] = useState(false)
  const [isChecking, setIsChecking] = useState(false)

  const productsQuery = useProducts()
  const inventoryCheck = useInventoryCheck()

  const handleCheckInventory = () => {
    setIsChecking(true)
    inventoryCheck.mutate(undefined, {
      onSuccess: () => {
        showToast('Inventory check complete')
        setIsChecking(false)
      },
      onError: (err) => {
        showToast(`Check failed: ${err.message}`)
        setIsChecking(false)
      },
    })
  }

  const products = productsQuery.data || []

  // Sample historical sparklines if none on record
  const mockSparklines: Record<number, number[]> = {
    1: [7, 9, 8, 6, 7, 5],
    2: [2, 3, 4, 2, 3, 2],
    3: [2, 3, 2, 3, 4, 3],
    4: [1, 2, 3, 2, 1, 2],
  }

  const filteredProducts = products.filter((p) => {
    const isLow = p.is_low_stock || p.current_stock < p.reorder_threshold
    if (filter === 'Low Stock' && !isLow) return false
    if (filter === 'Healthy' && isLow) return false
    if (search && !p.name.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  return (
    <div>
      {/* Header */}
      <div className="page-header">
        <div>
          <h1>Inventory</h1>
          <div className="page-sub">Full product list and current stock levels.</div>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            type="button"
            className="icon-btn"
            id="btnSimError"
            title="Simulate a network error (demo)"
            onClick={() => setSimulateError(true)}
          >
            <Icon name="wifiOff" />
          </button>
          <button
            type="button"
            className="btn btn-primary"
            id="btnCheckInventory2"
            onClick={handleCheckInventory}
            disabled={isChecking}
          >
            <Icon name="sparkles" /> {isChecking ? 'Checking…' : 'Check Inventory'}
          </button>
        </div>
      </div>

      {/* Dev Sales Panel */}
      {products.length > 0 && <DemoSaleRecorder products={products} />}

      {/* Error state if triggered */}
      {simulateError ? (
        <ErrorBanner
          title="Couldn't load inventory"
          description="The request to the inventory service timed out. Your data hasn't changed — try again."
          onRetry={() => {
            setSimulateError(false)
            showToast('Inventory reloaded')
          }}
        />
      ) : isChecking ? (
        <div className="panel">
          <LoadingSequence
            steps={[
              'Checking inventory...',
              'Scanning products...',
              'Low-stock products identified',
            ]}
          />
        </div>
      ) : (
        <>
          {/* Toolbar */}
          <div className="toolbar">
            <div className="search-input-wrap">
              <Icon name="search" />
              <input
                className="search-input"
                id="invSearch"
                placeholder="Search products..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <select
              className="select-input"
              id="invFilter"
              value={filter}
              onChange={(e) => setFilter(e.target.value as any)}
            >
              <option value="All">All</option>
              <option value="Low Stock">Low Stock</option>
              <option value="Healthy">Healthy</option>
            </select>
          </div>

          {/* Inventory Table */}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Stock</th>
                  <th>Trend</th>
                  <th>Threshold</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filteredProducts.length > 0 ? (
                  filteredProducts.map((p) => {
                    const isLow = p.is_low_stock || p.current_stock < p.reorder_threshold
                    const statusText = isLow ? 'LOW STOCK' : 'Healthy'
                    const sparkColor = isLow ? 'var(--danger)' : 'var(--system)'
                    const sparkData = mockSparklines[p.id] || [2, 3, 2, 4, 3]

                    return (
                      <tr
                        key={p.id}
                        className="clickable"
                        data-product={p.id}
                        onClick={() => navigate(`/inventory/${p.id}`)}
                      >
                        <td className="cell-strong">{p.name}</td>
                        <td className="mono">
                          {p.current_stock} {p.unit}
                        </td>
                        <td>
                          <Sparkline data={sparkData} color={sparkColor} />
                        </td>
                        <td className="mono">
                          {p.reorder_threshold} {p.unit}
                        </td>
                        <td>
                          <StatusChip
                            status={statusText}
                            variant={isLow ? 'low' : 'healthy'}
                          />
                        </td>
                        <td>
                          <span className="cell-link">
                            View <Icon name="arrowRight" />
                          </span>
                        </td>
                      </tr>
                    )
                  })
                ) : (
                  <tr>
                    <td
                      colSpan={6}
                      style={{
                        textAlign: 'center',
                        color: 'var(--text-soft)',
                        padding: '30px',
                      }}
                    >
                      No products match.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
