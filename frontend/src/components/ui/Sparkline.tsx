interface SparklineProps {
  data: number[] | [string, number][]
  color?: string
  width?: number
  height?: number
}

export function Sparkline({
  data,
  color = 'var(--system)',
  width = 54,
  height = 20,
}: SparklineProps) {
  if (!data || data.length === 0) {
    return <span className="spark" style={{ width, height, display: 'inline-block' }} />
  }

  const vals = data.map((d) => (Array.isArray(d) ? d[1] : d))
  const max = Math.max(...vals, 1)
  const min = Math.min(...vals, 0)
  const n = vals.length

  const pts = vals
    .map((v, i) => {
      const x = n > 1 ? (i / (n - 1)) * width : width / 2
      const y = height - ((v - min) / (max - min || 1)) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  return (
    <svg
      className="spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: 'inline-block', verticalAlign: 'middle', marginLeft: '8px' }}
    >
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
