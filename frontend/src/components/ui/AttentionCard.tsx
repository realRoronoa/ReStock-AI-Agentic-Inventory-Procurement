import { Icon } from '@/components/icons/Icons'
import { StatusChip } from '@/components/ui/StatusChip'

interface AttentionCardProps {
  name: string
  stock: number
  threshold: number
  unit: string
  onGenerateProposal: () => void
  isGenerating?: boolean
}

export function AttentionCard({
  name,
  stock,
  threshold,
  unit,
  onGenerateProposal,
  isGenerating,
}: AttentionCardProps) {
  return (
    <div className="attention-card">
      <div className="info">
        <div className="attn-icon">
          <Icon name="alert" />
        </div>
        <div>
          <div className="title">{name}</div>
          <div className="meta">
            Stock: <span className="mono">{stock} {unit}</span> &nbsp;·&nbsp; Threshold:{' '}
            <span className="mono">{threshold} {unit}</span>
          </div>
        </div>
      </div>
      <StatusChip status="Low stock" variant="low" />
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        onClick={onGenerateProposal}
        disabled={isGenerating}
      >
        {isGenerating ? 'Generating…' : 'Generate Proposal'}
      </button>
    </div>
  )
}
