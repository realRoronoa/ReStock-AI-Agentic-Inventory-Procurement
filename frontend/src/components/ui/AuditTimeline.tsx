import { AuditEvent } from '@/types/api'
import { auditActionLabel, formatTime } from '@/utils/format'

interface AuditTimelineProps {
  events: AuditEvent[]
  onSelectEvent?: (event: AuditEvent) => void
}

export function AuditTimeline({ events, onSelectEvent }: AuditTimelineProps) {
  if (!events || events.length === 0) {
    return (
      <div className="empty-state" style={{ padding: '24px' }}>
        <div className="h">No audit events yet</div>
      </div>
    )
  }

  return (
    <div className="timeline">
      {events.map((ev) => {
        const actorLower = ev.actor.toLowerCase() as 'agent' | 'human' | 'system'
        const reasoning =
          ev.reasoning_text ||
          (typeof ev.metadata?.reasoning === 'string'
            ? ev.metadata.reasoning
            : typeof ev.metadata?.llm_reasoning === 'string'
              ? ev.metadata.llm_reasoning
              : null)

        const orderId =
          ev.related_order_id ||
          (typeof ev.metadata?.order_id === 'number' ? ev.metadata.order_id : null)

        const timeStr = formatTime(ev.timestamp)
        const desc =
          typeof ev.metadata?.description === 'string'
            ? ev.metadata.description
            : auditActionLabel(ev.action)

        return (
          <div key={ev.id} className={`tl-item actor-${actorLower}`}>
            <div className="rail">
              <span className="node" />
              <span className="line" />
            </div>
            <div
              className="tl-content"
              onClick={() => onSelectEvent && onSelectEvent(ev)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onSelectEvent && onSelectEvent(ev)
                }
              }}
            >
              <div className="tl-top">
                <span className={`actor-tag ${actorLower}`}>{ev.actor}</span>
                <span className="tl-time mono">{timeStr}</span>
                {orderId && (
                  <span className="tl-time mono">· Order #{orderId}</span>
                )}
              </div>
              <div className="tl-title">{ev.action.replace(/_/g, ' ')}</div>
              <div className="tl-desc">{desc}</div>
              {reasoning && <div className="tl-reason">{reasoning}</div>}
            </div>
          </div>
        )
      })}
    </div>
  )
}
