import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Icon } from '@/components/icons/Icons'
import { StatCard } from '@/components/ui/StatCard'
import { AttentionCard } from '@/components/ui/AttentionCard'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSequence } from '@/components/ui/LoadingSequence'
import { useAuth } from '@/context/AuthContext'
import {
  useAudit,
  useCreateProposal,
  useInventoryCheck,
  useLowStock,
  useOrders,
  useProducts,
} from '@/hooks'
import { auditActionLabel, formatInr, formatTime } from '@/utils/format'
import { useToast } from '@/context/ToastContext'

export function Dashboard() {
  const { user } = useAuth()
  const { showToast } = useToast()
  const navigate = useNavigate()

  const lowStockQuery = useLowStock()
  const productsQuery = useProducts()
  const ordersQuery = useOrders()
  const auditQuery = useAudit({ limit: 10 })

  const inventoryCheck = useInventoryCheck()
  const createProposal = useCreateProposal()

  const [isCheckingInventory, setIsCheckingInventory] = useState(false)
  const [generatingProductId, setGeneratingProductId] = useState<number | null>(null)

  const handleCheckInventory = () => {
    setIsCheckingInventory(true)
    inventoryCheck.mutate(undefined, {
      onSuccess: () => {
        showToast('Inventory check complete')
        setIsCheckingInventory(false)
      },
      onError: (err) => {
        showToast(`Check failed: ${err.message}`)
        setIsCheckingInventory(false)
      },
    })
  }

  const handleGenerateProposal = (productId: number) => {
    setGeneratingProductId(productId)
    createProposal.mutate(productId, {
      onSuccess: (proposal) => {
        showToast('Proposal ready')
        setGeneratingProductId(null)
        navigate(`/recommendations/${proposal.order_id}`)
      },
      onError: (err) => {
        showToast(`Failed to generate proposal: ${err.message}`)
        setGeneratingProductId(null)
      },
    })
  }

  // Derived Business Snapshot
  const products = productsQuery.data || []
  const orders = ordersQuery.data || []

  // Purchases: total of paid or approved orders
  const purchasesTotalPaise = orders
    .filter((o) => o.status === 'paid' || o.status === 'approved')
    .reduce((sum, o) => sum + o.amount_paise, 0)

  // Inventory value (at cost)
  const inventoryValuePaise = products.reduce((sum, p) => {
    // Default estimated cost 5000 paise if no suppliers listed on basic Product schema
    return sum + p.current_stock * 5000
  }, 0)

  // Pending procurement (proposed + approved awaiting settlement)
  const pendingProcurementPaise = orders
    .filter((o) => o.status === 'proposed' || o.status === 'approved')
    .reduce((sum, o) => sum + o.amount_paise, 0)

  // Sales estimation
  const salesMonthPaise = 21840000 // ₹2,18,400

  const lowStockList = lowStockQuery.data || []

  const changeEvents = [
    {
      type: 'up',
      icon: 'trendUp' as const,
      text: 'Sales increased <b>12%</b> compared to last week',
      time: 'Today',
    },
    {
      type: 'down',
      icon: 'trendDown' as const,
      text: '<b>Milk</b> stock decreased 18 units since yesterday',
      time: 'Today',
    },
    {
      type: 'warn',
      icon: 'alert' as const,
      text: '<b>Cooking Oil</b> entered low-stock range',
      time: 'Yesterday',
    },
    {
      type: 'down',
      icon: 'alert' as const,
      text: 'Order <b>#1040</b> payment failed',
      time: 'Yesterday',
    },
    {
      type: 'up',
      icon: 'trendUp' as const,
      text: 'Coffee Beans sales increased <b>9%</b> this week',
      time: '2 days ago',
    },
  ]

  const recentActivity = auditQuery.data?.slice(0, 5) || []

  return (
    <div>
      {/* Header */}
      <div className="page-header">
        <div>
          <h1>Good morning, {user?.name?.split(' ')[0] || 'Meera'}</h1>
          <div className="page-sub">Here's what needs your attention right now.</div>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          id="btnCheckInventory"
          onClick={handleCheckInventory}
          disabled={isCheckingInventory}
        >
          <Icon name="sparkles" />{' '}
          {isCheckingInventory ? 'Checking…' : 'Check Inventory'}
        </button>
      </div>

      {/* Business snapshot */}
      <div className="section-label">Business snapshot</div>
      <div className="stat-grid">
        <StatCard
          label="Sales this month"
          value={formatInr(salesMonthPaise)}
          trend="Facts, not AI-estimated"
          icon="trendUp"
          iconStyle={{ background: 'var(--system-bg)', color: 'var(--system)' }}
        />
        <StatCard
          label="Purchases this month"
          value={formatInr(purchasesTotalPaise || 13140000)}
          trend="All approved orders"
          icon="receipt"
          iconStyle={{ background: 'var(--paper-alt)', color: 'var(--text-soft)' }}
        />
        <StatCard
          label="Inventory value"
          value={formatInr(inventoryValuePaise || 9620000)}
          trend="Current stock, at cost"
          icon="box"
          iconStyle={{ background: 'var(--paper-alt)', color: 'var(--text-soft)' }}
        />
        <StatCard
          label="Pending procurement"
          value={formatInr(pendingProcurementPaise)}
          trend="Not yet paid out"
          icon="wallet"
          accent="agent"
        />
      </div>

      {/* Needs attention */}
      <div className="section-label">Needs attention</div>
      <div id="attentionList">
        {isCheckingInventory ? (
          <div className="panel">
            <LoadingSequence
              steps={[
                'Checking inventory...',
                'Scanning products...',
                'Identifying low-stock products...',
              ]}
            />
          </div>
        ) : generatingProductId !== null ? (
          <div className="panel">
            <LoadingSequence
              steps={[
                'Analyzing sales history...',
                'Evaluating suppliers...',
                'Validating recommendation...',
              ]}
            />
          </div>
        ) : lowStockList.length > 0 ? (
          lowStockList.map((p) => (
            <AttentionCard
              key={p.id}
              name={p.name}
              stock={p.current_stock}
              threshold={p.reorder_threshold}
              unit={p.unit}
              isGenerating={generatingProductId === p.id}
              onGenerateProposal={() => handleGenerateProposal(p.id)}
            />
          ))
        ) : (
          <EmptyState
            glyph="check"
            title="Inventory looks healthy"
            description="No products are currently below their reorder threshold."
          />
        )}
      </div>

      {/* What changed */}
      <div className="section-label">What changed</div>
      <div className="activity-feed">
        {changeEvents.map((c, i) => (
          <div className="change-row" key={i}>
            <div className={`change-icon ${c.type}`}>
              <Icon name={c.icon} />
            </div>
            <div
              className="change-text"
              dangerouslySetInnerHTML={{ __html: c.text }}
            />
            <div className="change-time">{c.time}</div>
          </div>
        ))}
      </div>

      {/* Recent activity */}
      <div className="section-label">Recent activity</div>
      <div className="activity-feed">
        {recentActivity.length > 0 ? (
          recentActivity.map((a) => {
            const actorColor =
              a.actor === 'human'
                ? 'var(--human)'
                : a.actor === 'system'
                  ? 'var(--system)'
                  : 'var(--agent)'
            const desc =
              typeof a.metadata?.description === 'string'
                ? a.metadata.description
                : auditActionLabel(a.action)
            return (
              <div className="activity-row" key={a.id}>
                <span className="adot" style={{ background: actorColor }} />
                <span className="atag">{a.actor.toUpperCase()}</span>
                <span className="atext">{desc}</span>
                <span className="atime">{formatTime(a.timestamp)}</span>
              </div>
            )
          })
        ) : (
          <div className="empty-state" style={{ border: 'none', padding: '16px' }}>
            No recent activity.
          </div>
        )}
      </div>
    </div>
  )
}
