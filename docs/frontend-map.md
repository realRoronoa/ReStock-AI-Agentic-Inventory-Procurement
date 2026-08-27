# Frontend implementation map

`docs/main.html` is the visual source of truth. This maps every screen in it to
components, hooks and endpoints, and records exactly which parts needed new
backend work.

Design → Component → Hook → Endpoint → Real data.

---

## Top-level screens

| Design screen | Route | Component | Backend |
| --- | --- | --- | --- |
| `#screen-login` | `/login` | `pages/Login` | `POST /api/auth/login` **(new)** |
| `#screen-signup` | `/signup` | `pages/Signup` | `POST /api/auth/signup` **(new)** |
| `#screen-onboarding` | `/onboarding` | `pages/Onboarding` (4 steps) | plans + settings **(new)** |
| `#screen-app` | `/*` | `components/layout/AppShell` | — |

## App views

| Design view (`route.view`) | Route | Component | Hooks | Endpoints |
| --- | --- | --- | --- | --- |
| `dashboard` | `/` | `pages/Dashboard` | `useDashboardMetrics`, `useLowStock`, `useAudit`, `useInventoryCheck`, `useCreateProposal` | `/api/metrics/dashboard` **(new)**, `/api/inventory/low-stock`, `/api/audit`, `/api/inventory/check`, `/api/proposals/product/{id}` |
| `inventory` | `/inventory` | `pages/Inventory` | `useProducts`, `useRecordSale`, `useInventoryCheck` | `/api/products?include_sales=true` **(extended)**, `/api/sales`, `/api/inventory/check` |
| `productDetail` | `/inventory/:id` | `pages/ProductDetail` | `useProduct`, `useCreateProposal` | `/api/products/{id}` |
| `proposals` | `/recommendations` | `pages/Recommendations` | `useProposals` | `/api/proposals` |
| `proposalDetail` | `/recommendations/:orderId` | `pages/ProposalDetail` | `useOrder`, `useProduct`, `useApproveOrder`, `useRejectOrder` | `/api/orders/{id}`, `/api/products/{id}`, `/approve`, `/reject` |
| `orders` | `/orders` | `pages/Orders` | `useOrders` | `/api/orders?status=` |
| `orderDetail` | `/orders/:id` | `pages/OrderDetail` | `useOrder` (polling), `useAuditTrail` | `/api/orders/{id}`, `/api/audit/{id}` |
| `altSupplier` | `/orders/:id/alternative` | `pages/AlternativeSupplier` | `useOrder`, `useProduct`, `useCreateProposal` | `/api/orders/{id}`, `/api/products/{id}` |
| `spending` | `/spending` | `pages/Spending` | `useSpending`, `useOrders` | `/api/spending` |
| `settings` | `/settings` | `pages/Settings` | `useSettings`, `useUpdateSettings`, `useAuth` | `/api/settings`, `PUT /api/settings` **(new)** |
| `audit` | `/activity` | `pages/Activity` | `useAudit` | `/api/audit` |

## Modals

| Design | Component | Backend |
| --- | --- | --- |
| Approval confirm → processing pipeline | `components/recommendations/ApprovalModal` | `POST /api/orders/{id}/approve` |
| Audit event detail | `components/activity/AuditEventModal` | (data already loaded) |
| Billing (+ plan picker, payment-method edit) | `components/billing/BillingModal` | `/api/billing/*` **(new)** |

---

## Backend work this required

Everything below was added because the design needs it. Nothing in the design
is satisfied with invented data.

### 1. Authentication — `POST /api/auth/{signup,login,logout}`, `GET /api/auth/me`
The design opens on a login screen with a "Use demo account" button. Sessions are
opaque tokens in an httpOnly cookie, revocable server-side.

**Single shared workspace, not multi-tenant.** Signup creates a user account;
all users see the same inventory. The design says "1 store" and true tenancy
would mean `workspace_id` on six tables.

### 2. Billing — `/api/billing/{plans,subscription,invoices,payment-method}`
Real tables and state. **No subscription charge is ever fabricated:** changing
plan records the change and issues an invoice row, and the payment-method form
stores brand/last4/expiry only — never a card number. Actually collecting money
for a plan needs a collection-side Razorpay integration, which is a separate
decision; the boundary is marked in `billing_service.py`.

### 3. Writable settings — `PUT /api/settings`
The design's Settings screen has an editable daily-spend input and automation
toggles. Limits move to a `merchant_settings` singleton row that overrides the
environment defaults, gated behind auth and audited on every change.

Guardrails now resolve limits through `resolve_limits(db, config)`: DB row if
present, environment otherwise. `MAX_*_INR` env vars remain the bootstrap
defaults.

### 4. Dashboard metrics — `GET /api/metrics/dashboard`
The design's four stat cards need sales revenue, purchases, inventory value and
pending procurement. Purchases and pending come from orders. The other two
needed:

* **`products.selling_price_paise`** (new column) — the schema only had supplier
  *cost* price, so "Sales this month" in rupees was not computable at all.
  Seeded with realistic retail prices.
* **Inventory value** = `Σ stock × cheapest supplier cost`, labelled "at cost"
  exactly as the design does.

### 5. Sales in the product list — `GET /api/products?include_sales=true&sales_days=7`
The inventory table draws a sparkline per row. Opt-in so the plain list stays
cheap.

---

## Design elements with no backend, and what I did

| Element | Decision |
| --- | --- |
| Onboarding "Connect your inventory" (Shopify / Square / CSV / sample) | No integrations exist. The choice is **persisted** to `merchant_settings.connected_source` and shown in Settings, exactly as the design does. Selecting Shopify does not sync anything, and the UI says so. |
| Sidebar "Next scan in 12m" | There is no scheduler. Replaced with the real last-check time from the audit trail (`INVENTORY_CHECK_COMPLETED`), and the product count is real. Same component, truthful content. |
| Topbar "Demo · mock data" pill | Shown only when integrations are unconfigured, driven by `GET /health`. It is accurate rather than decorative. |
| Inventory "simulate a network error" button | Kept — it is a genuine error-state demo, and it now forces a real failed refetch rather than a flag. |
| `.status-chip` had no `rejected` variant | Added one rule using existing tokens (`--paper-deep` / `--text-soft`), matching `.reversed`. No token or theme change. |
| "What changed" feed | Derived from real audit events (`SALE_RECORDED`, `LOW_STOCK_DETECTED`, `PAYOUT_FAILED`, `PAYOUT_PROCESSED`). No new endpoint. |
| Spend by product / by supplier | Derived client-side from `spending.orders_today`. No new endpoint. |
| Forgot password | No endpoint. Link is present per the design but disabled with a title explaining it is not implemented, rather than silently doing nothing. |
