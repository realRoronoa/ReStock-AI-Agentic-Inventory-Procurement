import { Link } from 'react-router-dom'
import { Icon } from '@/components/icons/Icons'
import { EmptyState } from '@/components/ui/EmptyState'

export function NotFound() {
  return (
    <div style={{ padding: '60px 0' }}>
      <EmptyState
        glyph="alert"
        title="Page Not Found"
        description="The page you requested could not be found."
      >
        <div style={{ marginTop: '20px' }}>
          <Link to="/" className="btn btn-primary btn-sm">
            <Icon name="arrowLeft" /> Back to dashboard
          </Link>
        </div>
      </EmptyState>
    </div>
  )
}
