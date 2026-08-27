import React from 'react'
import { Icon, type IconName } from '@/components/icons/Icons'

interface StatCardProps {
  label: string
  value: string
  trend?: string
  icon: IconName
  accent?: 'agent' | 'human' | 'system'
  iconStyle?: React.CSSProperties
}

export function StatCard({
  label,
  value,
  trend,
  icon,
  accent,
  iconStyle,
}: StatCardProps) {
  const accentClass = accent ? `accent-${accent}` : ''

  return (
    <div className={`stat-card ${accentClass}`}>
      <div className="top-row">
        <div className="label">{label}</div>
        <div className="sicon" style={iconStyle}>
          <Icon name={icon} />
        </div>
      </div>
      <div className="value">{value}</div>
      {trend && <div className="trend">{trend}</div>}
    </div>
  )
}
