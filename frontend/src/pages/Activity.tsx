import { useState } from 'react'
import { AuditTimeline } from '@/components/ui/AuditTimeline'
import { AuditEventModal } from '@/components/modals/AuditEventModal'
import { LoadingSequence } from '@/components/ui/LoadingSequence'
import { useAudit } from '@/hooks'
import type { AuditEvent } from '@/types/api'

export function Activity() {
  const auditQuery = useAudit({ limit: 50 })
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null)

  if (auditQuery.isPending) {
    return (
      <div className="panel">
        <LoadingSequence steps={['Loading activity feed…']} />
      </div>
    )
  }

  const events = auditQuery.data || []

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Activity</h1>
          <div className="page-sub">
            Every event across your business — agent reasoning, human decisions, system
            events.
          </div>
        </div>
      </div>

      <AuditTimeline events={events} onSelectEvent={(ev) => setSelectedEvent(ev)} />

      <AuditEventModal event={selectedEvent} onClose={() => setSelectedEvent(null)} />
    </div>
  )
}
