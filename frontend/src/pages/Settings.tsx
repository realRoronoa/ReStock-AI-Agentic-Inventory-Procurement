import { useNavigate } from 'react-router-dom'
import { Icon } from '@/components/icons/Icons'
import { useAuth } from '@/context/AuthContext'
import { useMerchantSettings } from '@/context/MerchantSettingsContext'
import { useToast } from '@/context/ToastContext'

export function Settings() {
  const { user, logout } = useAuth()
  const { prefs, updatePref } = useMerchantSettings()
  const { showToast } = useToast()
  const navigate = useNavigate()

  const sourceNames: Record<string, string> = {
    shopify: 'Shopify',
    square: 'Square POS',
    csv: 'Uploaded CSV',
    sample: 'Sample data',
  }

  const handleLogout = () => {
    logout()
    showToast('Signed out')
    navigate('/login')
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Settings</h1>
          <div className="page-sub">
            Workspace, automation, and spending controls.
          </div>
        </div>
      </div>

      {/* Business profile */}
      <div className="settings-section">
        <div className="stitle-lg">Business profile</div>
        <div className="stitle-sub">Read-only in this demo.</div>
        <div className="panel">
          <div className="kv-row">
            <span className="k">Business name</span>
            <span className="v">{user?.businessName || 'Chennai Central Store'}</span>
          </div>
          <div className="kv-row">
            <span className="k">Owner</span>
            <span className="v">{user?.name || 'Meera Chandran'}</span>
          </div>
          <div className="kv-row">
            <span className="k">Email</span>
            <span className="v">{user?.email || 'merchant@restockai.com'}</span>
          </div>
          <div className="kv-row">
            <span className="k">Connected data source</span>
            <span className="v">
              {sourceNames[prefs.connectedSource] || 'Sample data'}
            </span>
          </div>
        </div>
      </div>

      {/* Automation */}
      <div className="settings-section">
        <div className="stitle-lg">Automation</div>
        <div className="stitle-sub">
          Controls how the agent behaves. Approval is never optional.
        </div>
        <div className="panel">
          <div className="settings-row">
            <div>
              <div className="sr-label">Automatic daily inventory check</div>
              <div className="sr-desc">Scan stock levels every morning at 8:00 AM</div>
            </div>
            <button
              type="button"
              className={`toggle ${prefs.autoCheck ? 'on' : ''}`}
              onClick={() => {
                updatePref('autoCheck', !prefs.autoCheck)
                showToast('Preference updated')
              }}
            >
              <span className="knob" />
            </button>
          </div>
          <div className="settings-row">
            <div>
              <div className="sr-label">Require approval for every order</div>
              <div className="sr-desc">
                The agent can never spend without your sign-off
              </div>
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
          <div className="settings-row">
            <div>
              <div className="sr-label">Email me when a proposal is ready</div>
              <div className="sr-desc">
                Get notified as soon as the agent finishes reasoning
              </div>
            </div>
            <button
              type="button"
              className={`toggle ${prefs.notify ? 'on' : ''}`}
              onClick={() => {
                updatePref('notify', !prefs.notify)
                showToast('Preference updated')
              }}
            >
              <span className="knob" />
            </button>
          </div>
        </div>
      </div>

      {/* Spending limit */}
      <div className="settings-section">
        <div className="stitle-lg">Spending limit</div>
        <div className="stitle-sub">
          The backend enforces this — the agent cannot propose past it.
        </div>
        <div className="panel">
          <div className="settings-row">
            <div>
              <div className="sr-label">Daily spending limit</div>
              <div className="sr-desc">
                Maximum procurement spend allowed per day (₹)
              </div>
            </div>
            <input
              className="settings-input"
              id="dailyLimitInput"
              type="number"
              value={prefs.dailyLimit}
              step={500}
              min={0}
              onChange={(e) => {
                const val = Math.max(0, parseInt(e.target.value) || 0)
                updatePref('dailyLimit', val)
              }}
              onBlur={() => showToast('Daily spending limit updated')}
            />
          </div>
        </div>
      </div>

      {/* Account danger zone */}
      <div className="settings-section">
        <div className="stitle-lg">Account</div>
        <div className="danger-zone">
          <div
            style={{
              fontWeight: 700,
              color: 'var(--danger)',
              fontSize: '13.5px',
              marginBottom: '4px',
            }}
          >
            Log out of this workspace
          </div>
          <div style={{ fontSize: '12.5px', color: '#7a2c2c', marginBottom: '14px' }}>
            You'll need to sign in again to access ReStock AI.
          </div>
          <button
            type="button"
            className="btn btn-danger btn-sm"
            id="btnSettingsLogout"
            onClick={handleLogout}
          >
            <Icon name="logout" /> Log out
          </button>
        </div>
      </div>
    </div>
  )
}
