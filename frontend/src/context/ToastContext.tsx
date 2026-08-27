import React, { createContext, useCallback, useContext, useState } from 'react'
import { Icon } from '@/components/icons/Icons'

interface ToastContextValue {
  showToast: (message: string) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toastMessage, setToastMessage] = useState<string | null>(null)
  const [isVisible, setIsVisible] = useState(false)
  const timerRef = React.useRef<number | null>(null)

  const showToast = useCallback((message: string) => {
    setToastMessage(message)
    setIsVisible(true)

    if (timerRef.current) {
      window.clearTimeout(timerRef.current)
    }

    timerRef.current = window.setTimeout(() => {
      setIsVisible(false)
    }, 2800)
  }, [])

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div className={`toast ${isVisible ? 'show' : ''}`} id="toast" role="status" aria-live="polite">
        <Icon name="check" />
        <span>{toastMessage}</span>
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    throw new Error('useToast must be used within a ToastProvider')
  }
  return ctx
}
