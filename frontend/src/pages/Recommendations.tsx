import { useNavigate } from 'react-router-dom'
import { StatusChip } from '@/components/ui/StatusChip'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSequence } from '@/components/ui/LoadingSequence'
import { useProposals } from '@/hooks'
import { formatInr } from '@/utils/format'

export function Recommendations() {
  const navigate = useNavigate()
  const proposalsQuery = useProposals()

  if (proposalsQuery.isPending) {
    return (
      <div className="panel">
        <LoadingSequence steps={['Loading recommendations…']} />
      </div>
    )
  }

  const proposals = proposalsQuery.data || []
  const pendingProposals = proposals.filter((p) => p.status === 'proposed')

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>AI Proposals</h1>
          <div className="page-sub">
            Purchases the agent recommends, awaiting your decision.
          </div>
        </div>
      </div>

      {pendingProposals.length > 0 ? (
        pendingProposals.map((p) => (
          <div className="prop-card" key={p.id}>
            <div className="top">
              <h3>{p.product_name}</h3>
              <StatusChip status="Waiting for approval" variant="proposed" />
            </div>
            <div className="fields">
              <div>
                Current stock: <b>{p.quantity} units</b>
              </div>
              <div>
                Recommended: <b>{p.quantity} units</b>
              </div>
              <div>
                Supplier: <b>{p.supplier_name}</b>
              </div>
              <div>
                Total: <b>{formatInr(p.amount_paise)}</b>
              </div>
            </div>
            <div className="foot">
              <span />
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                data-review={p.id}
                onClick={() => navigate(`/recommendations/${p.id}`)}
              >
                Review Proposal
              </button>
            </div>
          </div>
        ))
      ) : (
        <EmptyState
          glyph="fileCheck"
          title="No pending proposals"
          description="When the agent identifies a replenishment opportunity, it will appear here."
        />
      )}
    </div>
  )
}
