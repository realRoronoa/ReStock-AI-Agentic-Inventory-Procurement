import React, { createContext, useContext, useState, useEffect } from 'react'

export interface MerchantPrefs {
  autoCheck: boolean
  requireApproval: boolean
  notify: boolean
  dailyLimit: number
  connectedSource: string
}

interface SettingsContextValue {
  prefs: MerchantPrefs
  updatePref: <K extends keyof MerchantPrefs>(key: K, value: MerchantPrefs[K]) => void
  setAllPrefs: (prefs: Partial<MerchantPrefs>) => void
}

const DEFAULT_PREFS: MerchantPrefs = {
  autoCheck: true,
  requireApproval: true,
  notify: false,
  dailyLimit: 25000,
  connectedSource: 'sample',
}

const MerchantSettingsContext = createContext<SettingsContextValue | null>(null)

export function MerchantSettingsProvider({ children }: { children: React.ReactNode }) {
  const [prefs, setPrefs] = useState<MerchantPrefs>(() => {
    try {
      const saved = localStorage.getItem('restock_merchant_prefs')
      return saved ? { ...DEFAULT_PREFS, ...JSON.parse(saved) } : DEFAULT_PREFS
    } catch {
      return DEFAULT_PREFS
    }
  })

  useEffect(() => {
    localStorage.setItem('restock_merchant_prefs', JSON.stringify(prefs))
  }, [prefs])

  const updatePref = <K extends keyof MerchantPrefs>(key: K, value: MerchantPrefs[K]) => {
    setPrefs((prev) => ({ ...prev, [key]: value }))
  }

  const setAllPrefs = (newPrefs: Partial<MerchantPrefs>) => {
    setPrefs((prev) => ({ ...prev, ...newPrefs }))
  }

  return (
    <MerchantSettingsContext.Provider value={{ prefs, updatePref, setAllPrefs }}>
      {children}
    </MerchantSettingsContext.Provider>
  )
}

export function useMerchantSettings() {
  const ctx = useContext(MerchantSettingsContext)
  if (!ctx) {
    throw new Error('useMerchantSettings must be used within a MerchantSettingsProvider')
  }
  return ctx
}
