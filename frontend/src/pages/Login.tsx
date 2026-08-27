import React, { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Icon } from '@/components/icons/Icons'
import { useAuth } from '@/context/AuthContext'
import { useToast } from '@/context/ToastContext'

export function Login() {
  const { login, demoUser } = useAuth()
  const { showToast } = useToast()
  const navigate = useNavigate()

  const [email, setEmail] = useState('merchant@restockai.com')
  const [password, setPassword] = useState('demo1234')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleDemoFill = () => {
    setEmail(demoUser.email)
    setPassword(demoUser.pass)
    showToast('Demo credentials filled')
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsSubmitting(true)
    try {
      await login(email, password)
      showToast('Signed in')
      navigate('/')
    } catch {
      showToast('Sign in failed')
    } finally {
      setIsSubmitting(false)
    }
  }

  const steps = [
    { label: 'Low stock detected', sub: 'Agent scans inventory', color: 'var(--danger)' },
    { label: 'Forecast & supplier compared', sub: 'Reasoning generated', color: 'var(--agent)' },
    { label: 'You approve', sub: 'Human authorization', color: 'var(--human)' },
    { label: 'Payout confirmed', sub: 'Inventory updated', color: 'var(--system)' },
  ]

  return (
    <div className="auth-shell">
      {/* Brand Panel */}
      <div className="auth-brand-panel">
        <div className="auth-logo">
          <div className="logo-mark" id="logoMarkLogin">
            <Icon name="sparkles" />
          </div>
          <span className="logo-word">ReStock AI</span>
        </div>
        <div className="auth-hero">
          <div className="eyebrow">AI-powered procurement</div>
          <h1>
            Procurement,<br />
            <em>reasoned</em>.
          </h1>
          <p>
            Your inventory is watched continuously. Every reorder is reasoned through,
            priced, and compared — but only you can authorize the spend.
          </p>
          <div className="flow-diagram" id="flowDiagramLogin">
            {steps.map((s, i) => (
              <div className="flow-step" key={i}>
                {i < steps.length - 1 && <div className="flow-rail" />}
                <span className="num">0{i + 1}</span>
                <span className="fdot" style={{ background: s.color }} />
                <div>
                  <div className="flabel">{s.label}</div>
                  <div className="fsub">{s.sub}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="auth-foot">© 2026 ReStock AI · Merchant workspace</div>
      </div>

      {/* Form Panel */}
      <div className="auth-form-panel">
        <div className="auth-card fade-in">
          <div className="kicker">Welcome back</div>
          <h2>Sign in to your workspace</h2>
          <div className="sub">Enter your details to continue to your dashboard.</div>

          <form onSubmit={handleSubmit} id="loginForm">
            <div className="field">
              <label>Email address</label>
              <div className="field-input-wrap">
                <Icon name="mail" />
                <input
                  type="email"
                  id="loginEmail"
                  placeholder="you@business.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </div>
            </div>

            <div className="field">
              <div className="field-row-between">
                <label>Password</label>
                <span
                  className="link-sm"
                  title="Password reset is not configured in this demo"
                  onClick={() => showToast('Demo account password reset is disabled')}
                >
                  Forgot password?
                </span>
              </div>
              <div className="field-input-wrap">
                <Icon name="lock" />
                <input
                  type="password"
                  id="loginPass"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </div>
            </div>

            <div className="auth-submit">
              <button
                type="submit"
                className="btn btn-primary btn-block"
                id="btnLoginSubmit"
                disabled={isSubmitting}
              >
                {isSubmitting ? 'Signing in…' : 'Sign in'} <Icon name="arrowRight" />
              </button>
            </div>
          </form>

          <div className="auth-divider">or</div>

          <button
            type="button"
            className="demo-fill"
            id="btnDemoFill"
            onClick={handleDemoFill}
          >
            <Icon name="sparkles" /> Use demo account
          </button>

          <div className="auth-switch">
            Don't have a workspace? <Link to="/signup">Create one</Link>
          </div>
        </div>
      </div>
    </div>
  )
}
