import { Route, Routes } from 'react-router-dom'

import { AppShell } from '@/components/layout/AppShell'
import { Login } from '@/pages/Login'
import { Signup } from '@/pages/Signup'
import { Onboarding } from '@/pages/Onboarding'
import { Dashboard } from '@/pages/Dashboard'
import { Inventory } from '@/pages/Inventory'
import { ProductDetail } from '@/pages/ProductDetail'
import { Recommendations } from '@/pages/Recommendations'
import { ProposalDetail } from '@/pages/ProposalDetail'
import { Orders } from '@/pages/Orders'
import { OrderDetail } from '@/pages/OrderDetail'
import { AlternativeSupplier } from '@/pages/AlternativeSupplier'
import { Spending } from '@/pages/Spending'
import { Activity } from '@/pages/Activity'
import { Settings } from '@/pages/Settings'
import { NotFound } from '@/pages/NotFound'

export default function App() {
  return (
    <Routes>
      {/* Standalone Auth & Onboarding Screens */}
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/onboarding" element={<Onboarding />} />

      {/* Main App Workspace Shell */}
      <Route
        path="/"
        element={
          <AppShell>
            <Dashboard />
          </AppShell>
        }
      />
      <Route
        path="/inventory"
        element={
          <AppShell>
            <Inventory />
          </AppShell>
        }
      />
      <Route
        path="/inventory/:productId"
        element={
          <AppShell>
            <ProductDetail />
          </AppShell>
        }
      />
      <Route
        path="/recommendations"
        element={
          <AppShell>
            <Recommendations />
          </AppShell>
        }
      />
      <Route
        path="/recommendations/:orderId"
        element={
          <AppShell>
            <ProposalDetail />
          </AppShell>
        }
      />
      <Route
        path="/orders"
        element={
          <AppShell>
            <Orders />
          </AppShell>
        }
      />
      <Route
        path="/orders/:orderId"
        element={
          <AppShell>
            <OrderDetail />
          </AppShell>
        }
      />
      <Route
        path="/orders/:orderId/alternative"
        element={
          <AppShell>
            <AlternativeSupplier />
          </AppShell>
        }
      />
      <Route
        path="/spending"
        element={
          <AppShell>
            <Spending />
          </AppShell>
        }
      />
      <Route
        path="/activity"
        element={
          <AppShell>
            <Activity />
          </AppShell>
        }
      />
      <Route
        path="/settings"
        element={
          <AppShell>
            <Settings />
          </AppShell>
        }
      />
      <Route
        path="*"
        element={
          <AppShell>
            <NotFound />
          </AppShell>
        }
      />
    </Routes>
  )
}
