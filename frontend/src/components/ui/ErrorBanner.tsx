import { Icon } from '@/components/icons/Icons'

interface ErrorBannerProps {
  title: string
  description: string
  onRetry?: () => void
}

export function ErrorBanner({ title, description, onRetry }: ErrorBannerProps) {
  return (
    <div className="error-banner">
      <div className="eb-icon">
        <Icon name="wifiOff" />
      </div>
      <div className="eb-text">
        <div className="eb-title">{title}</div>
        <div className="eb-desc">{description}</div>
      </div>
      {onRetry && (
        <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>
          <Icon name="refresh" /> Retry
        </button>
      )}
    </div>
  )
}
