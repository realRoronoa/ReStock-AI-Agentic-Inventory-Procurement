import React from 'react'
import { Icon, type IconName } from '@/components/icons/Icons'

interface EmptyStateProps {
  glyph?: IconName
  title: string
  description?: string
  children?: React.ReactNode
}

export function EmptyState({
  glyph = 'check',
  title,
  description,
  children,
}: EmptyStateProps) {
  return (
    <div className="empty-state">
      <div className="glyph">
        <Icon name={glyph} />
      </div>
      <div className="h">{title}</div>
      {description && <div>{description}</div>}
      {children}
    </div>
  )
}
