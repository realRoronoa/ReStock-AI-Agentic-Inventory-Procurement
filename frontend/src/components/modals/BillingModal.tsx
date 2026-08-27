import React, { useState } from 'react'
import { Icon } from '@/components/icons/Icons'
import { useBilling, PLANS } from '@/context/BillingContext'
import { useToast } from '@/context/ToastContext'

export function BillingModal() {
  const {
    isBillingModalOpen,
    closeBillingModal,
    showPlanPickerInModal,
    selectedPlan,
    setSelectedPlan,
    currentPlan,
    billingCycle,
    setBillingCycle,
    paymentMethod,
    setPaymentMethod,
    invoices,
  } = useBilling()

  const [isPickingPlan, setIsPickingPlan] = useState(showPlanPickerInModal)
  const [isEditingPayment, setIsEditingPayment] = useState(false)
  const [cardInput, setCardInput] = useState({
    number: '4242 4242 4242 4242',
    exp: '09/28',
    cvc: '123',
  })
  const { showToast } = useToast()

  if (!isBillingModalOpen) return null

  const handleConfirmPlan = () => {
    showToast(`You're now on the ${currentPlan.name} plan`)
    setIsPickingPlan(false)
  }

  const handleSavePayment = (e: React.FormEvent) => {
    e.preventDefault()
    setPaymentMethod({
      brand: 'Visa',
      last4: cardInput.number.replace(/\s+/g, '').slice(-4) || '4242',
      expiry: cardInput.exp || '09/28',
    })
    setIsEditingPayment(false)
    showToast('Payment method updated')
  }

  const price =
    billingCycle === 'annual' ? currentPlan.priceAnnual : currentPlan.priceMonthly

  return (
    <div
      className="overlay"
      id="ovBilling"
      onClick={(e) => {
        if (e.target === e.currentTarget) closeBillingModal()
      }}
    >
      <div
        className="modal fade-in-fast"
        style={{ maxWidth: isPickingPlan ? '860px' : '480px' }}
      >
        <div
          className="modal-head"
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
        >
          <h3>{isPickingPlan ? 'Change plan' : 'Billing'}</h3>
          <button
            type="button"
            className="icon-btn"
            id="btnCloseBillingX"
            style={{ border: 'none' }}
            onClick={closeBillingModal}
          >
            <Icon name="x" />
          </button>
        </div>

        <div className="modal-body" id="billingBody" style={{ maxHeight: '75vh', overflowY: 'auto' }}>
          {isPickingPlan ? (
            <div>
              <div
                className="desc"
                style={{ margin: '0 0 20px 0', color: 'var(--text-soft)', fontSize: '13px' }}
              >
                Changes apply immediately. You won't be charged again until your next
                billing date.
              </div>

              {/* Plan Toggle */}
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

              {/* Plan Cards Grid */}
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
                        {p.features.map((f, i) => (
                          <li key={i}>
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

              <div className="onb-nav" style={{ marginTop: '24px' }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  id="btnCancelPlanChange"
                  onClick={() => setIsPickingPlan(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  id="btnConfirmPlanChange"
                  onClick={handleConfirmPlan}
                >
                  Confirm {currentPlan.name} plan
                </button>
              </div>
            </div>
          ) : (
            <div>
              <div className="billing-plan-card">
                <div className="bp-left">
                  <div className="bp-icon">
                    <Icon name={currentPlan.icon} />
                  </div>
                  <div>
                    <div className="bp-name">{currentPlan.name} plan</div>
                    <div className="bp-sub">
                      {price === 0
                        ? 'Free'
                        : `₹${price.toLocaleString('en-IN')}/mo · billed ${billingCycle}`}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  id="btnChangePlan"
                  onClick={() => setIsPickingPlan(true)}
                >
                  Change plan
                </button>
              </div>

              <div className="why-label">Payment method</div>
              <div className="payment-method-row">
                <div className="pm-card-icon">
                  <Icon name="creditCard" />
                </div>
                <div className="pm-details">
                  <div className="pm-brand">
                    {paymentMethod.brand} •••• {paymentMethod.last4}
                  </div>
                  <div className="pm-exp">Expires {paymentMethod.expiry}</div>
                </div>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  id="btnEditPayment"
                  onClick={() => setIsEditingPayment((prev) => !prev)}
                >
                  Edit
                </button>
              </div>

              {isEditingPayment && (
                <form onSubmit={handleSavePayment} className="panel" style={{ marginTop: '10px' }}>
                  <div className="field">
                    <label>Card number</label>
                    <div className="field-input-wrap">
                      <Icon name="creditCard" />
                      <input
                        type="text"
                        placeholder="4242 4242 4242 4242"
                        value={cardInput.number}
                        onChange={(e) =>
                          setCardInput({ ...cardInput, number: e.target.value })
                        }
                      />
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: '12px' }}>
                    <div className="field" style={{ flex: 1 }}>
                      <label>Expiry</label>
                      <input
                        style={{
                          width: '100%',
                          padding: '11px 14px',
                          borderRadius: '9px',
                          border: '1.5px solid var(--line)',
                          fontSize: '13.5px',
                        }}
                        placeholder="MM/YY"
                        value={cardInput.exp}
                        onChange={(e) =>
                          setCardInput({ ...cardInput, exp: e.target.value })
                        }
                      />
                    </div>
                    <div className="field" style={{ flex: 1 }}>
                      <label>CVC</label>
                      <input
                        style={{
                          width: '100%',
                          padding: '11px 14px',
                          borderRadius: '9px',
                          border: '1.5px solid var(--line)',
                          fontSize: '13.5px',
                        }}
                        placeholder="•••"
                        value={cardInput.cvc}
                        onChange={(e) =>
                          setCardInput({ ...cardInput, cvc: e.target.value })
                        }
                      />
                    </div>
                  </div>
                  <button
                    type="submit"
                    className="btn btn-primary btn-block btn-sm"
                    id="btnSavePayment"
                  >
                    Save payment method
                  </button>
                </form>
              )}

              <div className="why-label" style={{ marginTop: '22px' }}>
                Billing history
              </div>
              <div className="panel" style={{ padding: '6px 18px' }}>
                {invoices.map((iv) => (
                  <div className="invoice-row" key={iv.id}>
                    <span className="iv-id">{iv.id}</span>
                    <span className="iv-date">{iv.date}</span>
                    <span className="iv-amt">₹{iv.amount.toLocaleString('en-IN')}</span>
                    <span className="status-chip paid">{iv.status}</span>
                    <span
                      className="cell-link iv-dl"
                      onClick={() => showToast(`Downloaded invoice ${iv.id}`)}
                    >
                      <Icon name="download" />
                    </span>
                  </div>
                ))}
              </div>

              <div
                className="confirm-note"
                style={{
                  marginTop: '18px',
                  display: 'flex',
                  gap: '10px',
                  alignItems: 'flex-start',
                }}
              >
                <span style={{ color: 'var(--brand)', flexShrink: 0 }}>
                  <Icon name="shield" />
                </span>
                <span>
                  Payments are processed securely via RazorpayX. This is a demo — no
                  real card is ever charged.
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
