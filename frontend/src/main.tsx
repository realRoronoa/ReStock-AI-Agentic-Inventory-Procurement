/**
 * Application entry point.
 *
 * Sets up the query client, auth, billing, settings, toast providers and mounts the router.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { ApiError } from '@/api/client'
import App from '@/App'
import { ToastProvider } from '@/context/ToastContext'
import { AuthProvider } from '@/context/AuthContext'
import { BillingProvider } from '@/context/BillingContext'
import { MerchantSettingsProvider } from '@/context/MerchantSettingsContext'

import '@/index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: true,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.isBusinessRule) return false
        return failureCount < 2
      },
    },
    mutations: {
      retry: false,
    },
  },
})

const container = document.getElementById('root')
if (!container) {
  throw new Error('Root element #root not found in index.html')
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AuthProvider>
          <BillingProvider>
            <MerchantSettingsProvider>
              <BrowserRouter>
                <App />
              </BrowserRouter>
            </MerchantSettingsProvider>
          </BillingProvider>
        </AuthProvider>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
)
