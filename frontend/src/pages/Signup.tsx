import React, { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Icon } from '@/components/icons/Icons'
import { useAuth } from '@/context/AuthContext'
import { useToast } from '@/context/ToastContext'

export function Signup() {
  const { signup } = useAuth()
  const { showToast } = useToast()
  const navigate = useNavigate()

  const [businessName, setBusinessName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsSubmitting(true)
    try {
      await signup(businessName, email, password)
      showToast('Workspace created')
      navigate('/onboarding')
    } catch {
      showToast('Signup failed')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="auth-shell">
      {/* Brand Panel */}
      <div className="auth-brand-panel">
        <div className="auth-logo">
          <div className="logo-mark" id="logoMarkSignup">
            <Icon name="sparkles" />
          </div>
          <span className="logo-word">ReStock AI</span>
        </div>
        <div className="auth-hero">
          <div className="eyebrow">Set up in minutes</div>
          <h1>
            The agent<br />
            never <em>spends</em><br />
            alone.
          </h1>
          <p>
            Every recommendation carries its reasoning — the demand forecast, the
            supplier trade-off, the cost — so approval is a decision, not a leap of faith.
          </p>
        </div>
        <div className="auth-foot">© 2026 ReStock AI · Merchant workspace</div>
      </div>

      {/* Form Panel */}
      <div className="auth-form-panel">
        <div className="auth-card fade-in">
          <div className="kicker">Get started</div>
          <h2>Create your workspace</h2>
          <div className="sub">Takes about a minute. No card required for the demo.</div>

          <form onSubmit={handleSubmit} id="signupForm">
            <div className="field">
              <label>Business name</label>
              <div className="field-input-wrap">
                <Icon name="building" />
                <input
                  type="text"
                  id="signupBiz"
                  placeholder="Chennai Central Store"
                  value={businessName}
                  onChange={(e) => setBusinessName(e.target.value)}
                  required
                />
              </div>
            </div>

            <div className="field">
              <label>Work email</label>
              <div className="field-input-wrap">
                <Icon name="mail" />
                <input
                  type="email"
                  id="signupEmail"
                  placeholder="you@business.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </div>
            </div>

            <div className="field">
              <label>Password</label>
              <div className="field-input-wrap">
                <Icon name="lock" />
                <input
                  type="password"
                  id="signupPass"
                  placeholder="Create a password"
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
                disabled={isSubmitting}
              >
                {isSubmitting ? 'Creating…' : 'Create workspace'}{' '}
                <Icon name="arrowRight" />
              </button>
            </div>
          </form>

          <div className="terms-note">
            By continuing you agree to the Terms of Service and Privacy Policy.
          </div>

          <div className="auth-switch">
            Already have a workspace? <Link to="/login">Sign in</Link>
          </div>
        </div>
      </div>
    </div>
  )
}
