import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { StatusChip } from '@/components/ui/StatusChip'
import { LoadingSequence } from '@/components/ui/LoadingSequence'
import { useOrders } from '@/hooks'
import { formatInr } from '@/utils/format'

export function Orders() {
  const navigate = useNavigate()
  const [filter, setFilter] = useState<string>('All')

  const ordersQuery = useOrders()

  if (ordersQuery.isPending) {
    return (
      <div className="panel">
        <LoadingSequence steps={['Loading procurement orders…']} />
      </div>
    )
  }

  const allOrders = ordersQuery.data || []
  const filteredOrders = allOrders.filter((o) => {
    if (filter === 'All') return true
    return o.status.toLowerCase() === filter.toLowerCase()
  })

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Orders</h1>
          <div className="page-sub">
            All procurement orders and their payment state.
          </div>
        </div>
      </div>

      <div className="toolbar">
        <select
          className="select-input"
          id="orderFilterSel"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          {['All', 'Proposed', 'Approved', 'Paid', 'Failed', 'Reversed'].map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Order</th>
              <th>Product</th>
              <th>Supplier</th>
              <th>Amount</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {filteredOrders.length > 0 ? (
              filteredOrders.map((o) => (
                <tr
                  key={o.id}
                  className="clickable"
                  data-order={o.id}
                  onClick={() => navigate(`/orders/${o.id}`)}
                >
                  <td className="mono">#{o.id}</td>
                  <td className="cell-strong">{o.product_name}</td>
                  <td>{o.supplier_name}</td>
                  <td className="mono">{formatInr(o.amount_paise)}</td>
                  <td>
                    <StatusChip status={o.status} />
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td
                  colSpan={5}
                  style={{
                    textAlign: 'center',
                    color: 'var(--text-soft)',
                    padding: '30px',
                  }}
                >
                  No orders in this state.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
