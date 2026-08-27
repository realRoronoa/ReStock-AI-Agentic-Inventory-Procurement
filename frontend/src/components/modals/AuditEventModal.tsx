import type { AuditEvent } from '@/types/api'
import { auditActionLabel, formatTime } from '@/utils/format'

interface AuditEventModalProps {
  event: AuditEvent | null
  onClose: () => void
}

export function AuditEventModal({ event, onClose }: AuditEventModalProps) {
  if (!event) return null

  const actorLower = event.actor.toLowerCase()
  const reasoning =
    event.reasoning_text ||
    (typeof event.metadata?.reasoning === 'string'
      ? event.metadata.reasoning
      : typeof event.metadata?.llm_reasoning === 'string'
        ? event.metadata.llm_reasoning
        : null)

  const metaEntries = Object.entries(event.metadata || {}).filter(
    ([k]) => k !== 'reasoning' && k !== 'llm_reasoning' && k !== 'description',
  )

  const orderId =
    event.related_order_id ||
    (typeof event.metadata?.order_id === 'number' ? event.metadata.order_id : null)

  const description =
    typeof event.metadata?.description === 'string'
      ? event.metadata.description
      : auditActionLabel(event.action)

  return (
    <div
      className="overlay"
      id="ovAudit"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="modal fade-in-fast" style={{ maxWidth: '460px' }}>
        <div className="modal-head">
          <h3>{event.action.replace(/_/g, ' ')}</h3>
        </div>
        <div className="modal-body">
          <div className="kv-row">
            <span className="k">Actor</span>
            <span className={`actor-tag ${actorLower}`}>{event.actor}</span>
          </div>
          <div className="kv-row">
            <span className="k">Timestamp</span>
            <span className="v mono">{formatTime(event.timestamp)}</span>
          </div>
          {orderId && (
            <div className="kv-row">
              <span className="k">Related Order</span>
              <span className="v mono">#{orderId}</span>
            </div>
          )}
          <div className="kv-row">
            <span className="k">Description</span>
            <span className="v" style={{ textAlign: 'right', maxWidth: '65%' }}>
              {description}
            </span>
          </div>

          {reasoning && (
            <div style={{ marginTop: '14px' }}>
              <div className="why-label">Reasoning</div>
              <div className="reason-box">{reasoning}</div>
            </div>
          )}

          {metaEntries.length > 0 && (
            <div style={{ marginTop: '16px' }}>
              <div className="why-label">Metadata</div>
              {metaEntries.map(([k, v]) => (
                <div className="kv-row" key={k}>
                  <span className="k">{k.replace(/_/g, ' ')}</span>
                  <span className="v mono">{String(v)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button
            type="button"
            className="btn btn-secondary"
            id="btnCloseAudit"
            onClick={onClose}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
