import React, { useState, useEffect, useRef } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { Icon, type IconName } from '@/components/icons/Icons'
import { useAuth } from '@/context/AuthContext'
import { useBilling } from '@/context/BillingContext'
import { useProposals, useProducts, useHealth } from '@/hooks'
import { BillingModal } from '@/components/modals/BillingModal'
import { useToast } from '@/context/ToastContext'

interface AppShellProps {
  children?: React.ReactNode
}

interface NavItemDef {
  path: string
  label: string
  icon: IconName
  badgeCount?: number
}

export function AppShell({ children }: AppShellProps) {
  const { user, logout } = useAuth()
  const { currentPlan, openBillingModal } = useBilling()
  const { showToast } = useToast()
  const location = useLocation()
  const navigate = useNavigate()

  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false)
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const userMenuRef = useRef<HTMLDivElement>(null)

  const proposalsQuery = useProposals()
  const productsQuery = useProducts()
  const healthQuery = useHealth()

  const pendingProposalsCount = proposalsQuery.data
    ? proposalsQuery.data.filter((p) => p.status === 'proposed').length
    : 0

  const trackedProductsCount = productsQuery.data?.length || 4

  const navItems: NavItemDef[] = [
    { path: '/', label: 'Dashboard', icon: 'grid' },
    { path: '/inventory', label: 'Inventory', icon: 'box' },
    {
      path: '/recommendations',
      label: 'Recommendations',
      icon: 'fileCheck',
      badgeCount: pendingProposalsCount,
    },
    { path: '/orders', label: 'Orders', icon: 'receipt' },
    { path: '/spending', label: 'Spending', icon: 'wallet' },
    { path: '/activity', label: 'Activity', icon: 'clock' },
    { path: '/settings', label: 'Settings', icon: 'sliders' },
  ]

  // Close user menu on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setIsUserMenuOpen(false)
      }
    }
    document.addEventListener('click', handleClickOutside)
    return () => document.removeEventListener('click', handleClickOutside)
  }, [])

  // Close mobile sidebar on route change
  useEffect(() => {
    setIsSidebarOpen(false)
  }, [location.pathname])

  const getBreadcrumb = () => {
    const p = location.pathname
    if (p === '/') return 'Dashboard'
    if (p.startsWith('/inventory/')) return 'Product Detail'
    if (p === '/inventory') return 'Inventory'
    if (p.startsWith('/recommendations/')) return 'Purchase Proposal'
    if (p === '/recommendations') return 'Recommendations'
    if (p.includes('/alternative')) return 'Alternative Supplier'
    if (p.startsWith('/orders/')) return 'Order Detail'
    if (p === '/orders') return 'Orders'
    if (p === '/spending') return 'Spending'
    if (p === '/activity') return 'Activity'
    if (p === '/settings') return 'Settings'
    return 'Dashboard'
  }

  const handleLogout = () => {
    logout()
    showToast('Signed out')
    navigate('/login')
  }

  const isMockMode =
    !healthQuery.data?.integrations?.razorpayx_payouts_configured ||
    !healthQuery.data?.integrations?.llm_configured

  return (
    <div id="shell">
      {/* Sidebar */}
      <aside id="sidebar" className={isSidebarOpen ? 'open' : ''}>
        <div className="brand">
          <div className="logo-mark">
            <Icon name="sparkles" />
          </div>
          <div className="brand-name">ReStock AI</div>
        </div>

        <div className="nav-group" id="navGroup">
          {navItems.map((item) => {
            const isActive =
              item.path === '/'
                ? location.pathname === '/'
                : location.pathname.startsWith(item.path)
            return (
              <NavLink
                key={item.path}
                to={item.path}
                className={`nav-item ${isActive ? 'active' : ''}`}
              >
                <Icon name={item.icon} />
                <span>{item.label}</span>
                {item.badgeCount && item.badgeCount > 0 ? (
                  <span className="badge">{item.badgeCount}</span>
                ) : null}
              </NavLink>
            )
          })}
        </div>

        <div className="sidebar-agent-status">
          <div className="sas-top">
            <span className="pulse-dot" /> Agent active
          </div>
          <div className="sas-sub">
            Watching {trackedProductsCount} products across 1 store. Continuous monitoring.
          </div>
        </div>

        <div className="sidebar-foot" ref={userMenuRef}>
          <div
            className="user-chip"
            id="userChip"
            onClick={(e) => {
              e.stopPropagation()
              setIsUserMenuOpen((prev) => !prev)
            }}
          >
            <div className="avatar">{user?.initials || 'MC'}</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="uname">{user?.name || 'Meera Chandran'}</div>
              <div className="urole" id="sidebarPlanLabel">
                {currentPlan.name} plan
              </div>
            </div>

            <div className={`user-menu ${isUserMenuOpen ? 'open' : ''}`} id="userMenu">
              <button
                type="button"
                id="btnBilling"
                onClick={() => {
                  setIsUserMenuOpen(false)
                  openBillingModal()
                }}
              >
                <Icon name="creditCard" /> Billing & plan
              </button>
              <button type="button" id="btnLogout" onClick={handleLogout}>
                <Icon name="logout" /> Log out
              </button>
            </div>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <div id="main">
        <header id="topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="icon-btn"
              style={{ display: 'none' }}
              onClick={() => setIsSidebarOpen((prev) => !prev)}
            >
              <Icon name="grid" />
            </button>
            <span className="crumb" id="crumb">
              <b>{getBreadcrumb()}</b>
            </span>
          </div>
          <div className="topbar-right">
            <span className="demo-pill">
              {isMockMode ? 'Demo · mock data' : 'Live mode'}
            </span>
            <button
              type="button"
              className="icon-btn"
              id="topSearchBtn"
              onClick={() => navigate('/inventory')}
              title="Search products"
            >
              <Icon name="search" />
            </button>
          </div>
        </header>

        <main id="content" className="fade-in">
          {children}
        </main>
      </div>

      <BillingModal />
    </div>
  )
}
