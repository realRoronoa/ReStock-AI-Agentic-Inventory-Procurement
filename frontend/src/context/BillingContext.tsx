import { createContext, useContext, useState, type ReactNode } from 'react'
import type { IconName } from '@/components/icons/Icons'

export interface Plan {
  key: string
  name: string
  tagline: string
  priceMonthly: number
  priceAnnual: number
  cta: string
  highlight: boolean
  icon: IconName
  features: string[]
}

export interface PaymentMethod {
  brand: string
  last4: string
  expiry: string
}

export interface Invoice {
  id: string
  date: string
  amount: number
  status: string
}

export const PLANS: Plan[] = [
  {
    key: 'starter',
    name: 'Starter',
    tagline: 'For a single store finding its footing.',
    priceMonthly: 0,
    priceAnnual: 0,
    cta: 'Start free',
    highlight: false,
    icon: 'box',
    features: [
      'Up to 25 tracked products',
      'Manual inventory checks',
      'AI proposals with reasoning',
      'Email support',
    ],
  },
  {
    key: 'growth',
    name: 'Growth',
    tagline: 'For stores ready to automate reordering.',
    priceMonthly: 2499,
    priceAnnual: 1999,
    cta: 'Choose Growth',
    highlight: true,
    icon: 'sparkles',
    features: [
      'Up to 250 tracked products',
      'Automatic daily inventory checks',
      'Multi-supplier comparison',
      'RazorpayX payouts & full audit trail',
      'Priority support',
    ],
  },
  {
    key: 'scale',
    name: 'Scale',
    tagline: 'For multi-location merchants at volume.',
    priceMonthly: 6999,
    priceAnnual: 5499,
    cta: 'Choose Scale',
    highlight: false,
    icon: 'crown',
    features: [
      'Unlimited products & locations',
      'Custom approval workflows',
      'Dedicated account manager',
      'API access',
      'SLA-backed support',
    ],
  },
]

interface BillingContextValue {
  selectedPlan: string
  setSelectedPlan: (plan: string) => void
  currentPlan: Plan
  billingCycle: 'monthly' | 'annual'
  setBillingCycle: (cycle: 'monthly' | 'annual') => void
  paymentMethod: PaymentMethod
  setPaymentMethod: (pm: PaymentMethod) => void
  invoices: Invoice[]
  isBillingModalOpen: boolean
  openBillingModal: (showPlanPicker?: boolean) => void
  closeBillingModal: () => void
  showPlanPickerInModal: boolean
}

const BillingContext = createContext<BillingContextValue | null>(null)

export function BillingProvider({ children }: { children: ReactNode }) {
  const [selectedPlan, setSelectedPlan] = useState<string>('growth')
  const [billingCycle, setBillingCycle] = useState<'monthly' | 'annual'>('annual')
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>({
    brand: 'Visa',
    last4: '4242',
    expiry: '09/28',
  })
  const [invoices] = useState<Invoice[]>([
    { id: 'INV-1042', date: 'Aug 1, 2026', amount: 1999, status: 'Paid' },
    { id: 'INV-1041', date: 'Jul 1, 2026', amount: 1999, status: 'Paid' },
    { id: 'INV-1040', date: 'Jun 1, 2026', amount: 2499, status: 'Paid' },
  ])

  const [isBillingModalOpen, setIsBillingModalOpen] = useState(false)
  const [showPlanPickerInModal, setShowPlanPickerInModal] = useState(false)

  const openBillingModal = (showPlanPicker = false) => {
    setShowPlanPickerInModal(showPlanPicker)
    setIsBillingModalOpen(true)
  }

  const closeBillingModal = () => {
    setIsBillingModalOpen(false)
  }

  const currentPlan: Plan = PLANS.find((p) => p.key === selectedPlan) ?? PLANS[1]!

  return (
    <BillingContext.Provider
      value={{
        selectedPlan,
        setSelectedPlan,
        currentPlan,
        billingCycle,
        setBillingCycle,
        paymentMethod,
        setPaymentMethod,
        invoices,
        isBillingModalOpen,
        openBillingModal,
        closeBillingModal,
        showPlanPickerInModal,
      }}
    >
      {children}
    </BillingContext.Provider>
  )
}

export function useBilling() {
  const ctx = useContext(BillingContext)
  if (!ctx) {
    throw new Error('useBilling must be used within a BillingProvider')
  }
  return ctx
}
