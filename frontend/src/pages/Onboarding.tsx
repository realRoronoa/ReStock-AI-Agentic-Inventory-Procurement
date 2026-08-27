import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Icon, type IconName } from '@/components/icons/Icons'
import { useAuth } from '@/context/AuthContext'
import { useBilling, PLANS } from '@/context/BillingContext'
import { useMerchantSettings } from '@/context/MerchantSettingsContext'
import { useToast } from '@/context/ToastContext'

const ONB_STEPS = ['Welcome', 'Plan', 'Connect data', 'Preferences']

export function Onboarding() {
  const { user } = useAuth()
  const { selectedPlan, setSelectedPlan, billingCycle, setBillingCycle } = useBilling()
  const { prefs, updatePref } = useMerchantSettings()
  const { showToast } = useToast()
  const navigate = useNavigate()

  const [onbStep, setOnbStep] = useState(0)
  const [selectedSource, setSelectedSource] = useState<string>('sample')

  const merchantName = user?.businessName || 'Chennai Central Store'

  const finishSetup = () => {
    updatePref('connectedSource', selectedSource)
    showToast('Workspace ready — welcome in')
    navigate('/')
  }

  const sources: { key: string; name: string; desc: string; icon: IconName }[] = [
    { key: 'shopify', name: 'Shopify', desc: 'Sync products & sales', icon: 'shopify' },
    { key: 'square', name: 'Square POS', desc: 'Sync in-store sales', icon: 'square' },
    { key: 'csv', name: 'Upload CSV', desc: 'Import a stock sheet', icon: 'csv' },
    {
      key: 'sample',
      name: 'Use sample data',
      desc: 'Explore with demo inventory',
      icon: 'database',
    },
  ]

  const flowNodes: [IconName, string, string, string][] = [
    ['box', 'Low stock detected', 'var(--danger-bg)', 'var(--danger)'],
    ['brain', 'AI reasons & recommends', 'var(--agent-bg)', 'var(--agent)'],
    ['handshake', 'You approve', 'var(--human-bg)', 'var(--human)'],
    ['bank', 'Payout & inventory update', 'var(--system-bg)', 'var(--system)'],
  ]

  return (
    <div className="onb-shell">
      {/* Top Header with Steps and Skip */}
      <header className="onb-top">
        <div className="auth-logo">
          <div className="logo-mark" id="logoMarkOnb" style={{ width: '26px', height: '26px' }}>
            <Icon name="sparkles" />
          </div>
          <span className="logo-word" style={{ fontSize: '15.5px' }}>
            ReStock AI
          </span>
        </div>

        <div className="onb-steps" id="onbSteps">
          {ONB_STEPS.map((s, i) => (
            <React.Fragment key={s}>
              <div
                className={`onb-step-pill ${i === onbStep ? 'active' : ''} ${
                  i < onbStep ? 'done' : ''
                }`}
              >
                <span className="circ">{i < onbStep ? '✓' : i + 1}</span>
                <span>{s}</span>
              </div>
              {i < ONB_STEPS.length - 1 && <div className="onb-step-rail" />}
            </React.Fragment>
          ))}
        </div>

        <span className="onb-skip" id="btnSkipOnb" onClick={finishSetup}>
          Skip setup →
        </span>
      </header>

      {/* Wizard Body */}
      <main className="onb-body" id="onbBody">
        {onbStep === 0 && (
          <div className="onb-panel fade-in">
            <div className="eyebrow">Welcome, {merchantName}</div>
            <h2>Here's how ReStock AI works</h2>
            <div className="desc">
              The agent watches your inventory, reasons about what to reorder and from whom,
              and prepares the decision — you stay the only one who can spend.
            </div>

            <div className="onb-diagram">
              {flowNodes.map((n, i) => (
                <React.Fragment key={i}>
                  <div className="onb-node">
                    <div className="nicon" style={{ background: n[2], color: n[3] }}>
                      <Icon name={n[0]} />
                    </div>
                    <div className="nlabel">{n[1]}</div>
                  </div>
                  {i < flowNodes.length - 1 && (
                    <div className="onb-arrow">
                      <Icon name="arrowRight" />
                    </div>
                  )}
                </React.Fragment>
              ))}
            </div>

            <div className="onb-nav">
              <button
                type="button"
                className="btn btn-primary"
                id="onbNext0"
                onClick={() => setOnbStep(1)}
              >
                Get started <Icon name="arrowRight" />
              </button>
            </div>
          </div>
        )}

        {onbStep === 1 && (
          <div className="onb-panel fade-in" style={{ maxWidth: '820px' }}>
            <div className="eyebrow">Step 2 of 4</div>
            <h2>Choose your plan</h2>
            <div className="desc">
              Start on Growth, or pick what fits. You can change plans anytime from Billing.
            </div>

            {/* Plan selector */}
            <div className="plan-selector">
              <div className="plan-toggle">
                <button
                  type="button"
                  className={`pt-btn ${billingCycle === 'monthly' ? 'active' : ''}`}
                  onClick={() => setBillingCycle('monthly')}
                >
                  Monthly
                </button>
                <button
                  type="button"
                  className={`pt-btn ${billingCycle === 'annual' ? 'active' : ''}`}
                  onClick={() => setBillingCycle('annual')}
                >
                  Annual <span className="pt-save">Save 20%</span>
                </button>
              </div>

              <div className="plan-grid">
                {PLANS.map((p) => {
                  const cardPrice =
                    billingCycle === 'annual' ? p.priceAnnual : p.priceMonthly
                  const isSelected = p.key === selectedPlan
                  return (
                    <div
                      key={p.key}
                      className={`plan-card ${isSelected ? 'selected' : ''} ${
                        p.highlight ? 'popular' : ''
                      }`}
                      onClick={() => setSelectedPlan(p.key)}
                    >
                      {p.highlight && <div className="plan-ribbon">Most popular</div>}
                      <div className="plan-name">{p.name}</div>
                      <div className="plan-tagline">{p.tagline}</div>
                      <div className="plan-price">
                        {cardPrice === 0 ? (
                          <span className="pp-num">Free</span>
                        ) : (
                          <>
                            <span className="pp-cur">₹</span>
                            <span className="pp-num">{cardPrice.toLocaleString('en-IN')}</span>
                            <span className="pp-per">/mo</span>
                          </>
                        )}
                      </div>
                      <ul className="plan-features">
                        {p.features.map((f, idx) => (
                          <li key={idx}>
                            <Icon name="check" />
                            <span>{f}</span>
                          </li>
                        ))}
                      </ul>
                      <div className="plan-select-btn">
                        {isSelected ? 'Selected' : p.cta}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            <div className="onb-nav" style={{ marginTop: '24px' }}>
              <button
                type="button"
                className="btn btn-secondary"
                id="onbBack1"
                onClick={() => setOnbStep(0)}
              >
                <Icon name="arrowLeft" /> Back
              </button>
              <button
                type="button"
                className="btn btn-primary"
                id="onbNext1"
                onClick={() => setOnbStep(2)}
              >
                Continue with {PLANS.find((p) => p.key === selectedPlan)?.name}{' '}
                <Icon name="arrowRight" />
              </button>
            </div>
          </div>
        )}

        {onbStep === 2 && (
          <div className="onb-panel fade-in">
            <div className="eyebrow">Step 3 of 4</div>
            <h2>Connect your inventory</h2>
            <div className="desc">
              Choose where ReStock AI should read stock levels and sales history from.
            </div>

            <div className="connect-grid">
              {sources.map((s) => (
                <div
                  key={s.key}
                  className={`connect-card ${selectedSource === s.key ? 'selected' : ''}`}
                  onClick={() => setSelectedSource(s.key)}
                >
                  <div className="cicon">
                    <Icon name={s.icon} />
                  </div>
                  <div className="ctext">
                    <div className="cname">{s.name}</div>
                    <div className="cdesc">{s.desc}</div>
                  </div>
                  <div className="ccheck" />
                </div>
              ))}
            </div>

            <div className="onb-nav">
              <button
                type="button"
                className="btn btn-secondary"
                id="onbBack2"
                onClick={() => setOnbStep(1)}
              >
                <Icon name="arrowLeft" /> Back
              </button>
              <button
                type="button"
                className="btn btn-primary"
                id="onbNext2"
                disabled={!selectedSource}
                onClick={() => setOnbStep(3)}
              >
                Continue <Icon name="arrowRight" />
              </button>
            </div>
          </div>
        )}

        {onbStep === 3 && (
          <div className="onb-panel fade-in">
            <div className="eyebrow">Step 4 of 4</div>
            <h2>Set your defaults</h2>
            <div className="desc">
              You can change these anytime from settings. They only affect how proposals
              are generated — approval is always manual.
            </div>

            <div className="pref-list">
              <div className="pref-row">
                <div>
                  <div className="ptitle">Automatic daily inventory check</div>
                  <div className="pdesc">Scan stock levels every morning at 8:00 AM</div>
                </div>
                <button
                  type="button"
                  className={`toggle ${prefs.autoCheck ? 'on' : ''}`}
                  onClick={() => updatePref('autoCheck', !prefs.autoCheck)}
                >
                  <span className="knob" />
                </button>
              </div>

              <div className="pref-row">
                <div>
                  <div className="ptitle">Require approval for every order</div>
                  <div className="pdesc">The agent can never spend without your sign-off</div>
                </div>
                <button
                  type="button"
                  className="toggle on"
                  disabled
                  style={{ opacity: 0.6, cursor: 'default' }}
                >
                  <span className="knob" />
                </button>
              </div>

              <div className="pref-row">
                <div>
                  <div className="ptitle">Email me when a proposal is ready</div>
                  <div className="pdesc">Get notified as soon as the agent finishes reasoning</div>
                </div>
                <button
                  type="button"
                  className={`toggle ${prefs.notify ? 'on' : ''}`}
                  onClick={() => updatePref('notify', !prefs.notify)}
                >
                  <span className="knob" />
                </button>
              </div>
            </div>

            <div className="onb-nav">
              <button
                type="button"
                className="btn btn-secondary"
                id="onbBack3"
                onClick={() => setOnbStep(2)}
              >
                <Icon name="arrowLeft" /> Back
              </button>
              <button
                type="button"
                className="btn btn-primary"
                id="onbFinish"
                onClick={finishSetup}
              >
                Finish setup <Icon name="arrowRight" />
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
