import { useEffect, useState } from 'react'

interface LoadingSequenceProps {
  steps: string[]
  onDone?: () => void
  stepDurationMs?: number
  text?: string
}

export function LoadingSequence({
  steps,
  onDone,
  stepDurationMs = 600,
  text,
}: LoadingSequenceProps) {
  const [currentStepIndex, setCurrentStepIndex] = useState(0)

  useEffect(() => {
    if (!steps || steps.length === 0) return

    const interval = setInterval(() => {
      setCurrentStepIndex((prev) => {
        if (prev < steps.length - 1) {
          return prev + 1
        } else {
          clearInterval(interval)
          if (onDone) onDone()
          return prev
        }
      })
    }, stepDurationMs)

    return () => clearInterval(interval)
  }, [steps, stepDurationMs, onDone])

  const displayText = text || (steps && steps[currentStepIndex]) || 'Loading…'

  return (
    <div className="loading-block">
      <div className="spinner" />
      <div className="loading-text">{displayText}</div>
    </div>
  )
}
