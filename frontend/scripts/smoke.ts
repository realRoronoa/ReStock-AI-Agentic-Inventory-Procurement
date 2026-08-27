/**
 * End-to-end smoke test of the API layer against a running backend.
 *
 *   cd frontend
 *   npm run smoke                       # expects the backend on :8000
 *   SMOKE_BASE_URL=... npm run smoke
 *
 * This exercises the **real** `api/client.ts` and `api/endpoints.ts` — the same
 * code the React hooks call — so a mistake in a path, a query parameter, or the
 * error envelope shows up here rather than in the browser.
 *
 * It also walks the whole procurement flow, which is the thing worth testing:
 * sale -> low stock -> proposal -> approval -> webhook -> paid, plus the
 * duplicate-approval and duplicate-webhook protections.
 *
 * It is destructive: it records sales and creates orders. Run it against a
 * development database, never anything real.
 */

import { createHmac } from 'node:crypto'

import { ApiError } from '@/api/client'
import { api } from '@/api/endpoints'
import { isAwaitingSettlement, isTerminal } from '@/hooks/useOrders'
import { formatInr, orderStatusLabel } from '@/utils/format'
import { toFriendlyError } from '@/utils/errors'
import type { OrderStatus } from '@/types/api'

const BASE_URL = process.env.SMOKE_BASE_URL ?? 'http://127.0.0.1:8000'
const WEBHOOK_SECRET = process.env.SMOKE_WEBHOOK_SECRET ?? 'fe_webhook_secret'

let failures = 0
let checks = 0

function check(label: string, condition: boolean, detail?: unknown): void {
  checks += 1
  if (!condition) failures += 1
  const mark = condition ? 'PASS' : 'FAIL'
  const suffix = detail === undefined ? '' : `  -> ${String(detail)}`
  console.log(`  [${mark}] ${label}${suffix}`)
}

function section(title: string): void {
  console.log(`\n=== ${title} ===`)
}

/** Post a signed webhook, imitating RazorpayX. Not part of the app. */
async function sendWebhook(
  event: string,
  payoutId: string,
  eventId: string,
): Promise<{ status: number; body: Record<string, unknown> }> {
  const status = event.split('.').pop() ?? 'processed'
  const body = {
    entity: 'event',
    account_id: 'acc_SMOKE',
    event,
    contains: ['payout'],
    payload: {
      payout: {
        entity: {
          id: payoutId,
          entity: 'payout',
          amount: 360000,
          currency: 'INR',
          status,
          utr: '523223155921',
          mode: 'IMPS',
          failure_reason: null,
          status_details: {
            reason: status,
            description: `Payout ${status}.`,
            source: 'beneficiary_bank',
          },
        },
      },
    },
    created_at: 1755693679,
  }

  const raw = JSON.stringify(body)
  const signature = createHmac('sha256', WEBHOOK_SECRET).update(raw).digest('hex')

  const response = await fetch(`${BASE_URL}/api/webhooks/razorpayx`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Razorpay-Signature': signature,
      'X-Razorpay-Event-Id': eventId,
    },
    body: raw,
  })

  return {
    status: response.status,
    body: (await response.json()) as Record<string, unknown>,
  }
}

async function expectApiError(
  label: string,
  operation: () => Promise<unknown>,
  expected: { status?: number; code?: string },
): Promise<ApiError | undefined> {
  try {
    await operation()
    check(label, false, 'expected an error, got success')
    return undefined
  } catch (error) {
    if (!(error instanceof ApiError)) {
      check(label, false, `expected ApiError, got ${String(error)}`)
      return undefined
    }
    const statusOk = expected.status === undefined || error.status === expected.status
    const codeOk = expected.code === undefined || error.code === expected.code
    check(label, statusOk && codeOk, `${error.status} ${error.code}`)
    return error
  }
}

async function stockOf(name: string): Promise<number> {
  const products = await api.products.list()
  const product = products.find((candidate) => candidate.name === name)
  if (!product) throw new Error(`Product ${name} not seeded`)
  return product.current_stock
}

async function main(): Promise<void> {
  // The API client's base URL is inlined by Vite from VITE_API_BASE_URL when
  // this script is bundled (see the `smoke` npm script). BASE_URL below is used
  // only for the raw webhook posts, which deliberately bypass the client.
  console.log(`Backend: ${BASE_URL}`)

  section('1. health and settings')
  const health = await api.meta.health()
  check('health ok', health.status === 'ok', health.status)
  check('database up', health.database === 'up')
  console.log(`       integrations: ${JSON.stringify(health.integrations)}`)

  const settings = await api.meta.settings()
  check('settings are read-only', settings.editable === false)
  check(
    'daily cap present',
    settings.guardrails.max_daily_spend_paise > 0,
    formatInr(settings.guardrails.max_daily_spend_paise),
  )
  check(
    'no secret in settings',
    !JSON.stringify(settings).toLowerCase().includes('secret'),
  )

  section('2. products and inventory')
  const products = await api.products.list()
  check('products seeded', products.length === 4, products.length)

  const milk = products.find((product) => product.name === 'Milk')
  if (!milk) throw new Error('Milk not seeded — reseed the backend')

  const lowStockOnly = await api.products.list({ lowStock: true })
  check(
    'low_stock filter works',
    lowStockOnly.every((product) => product.is_low_stock),
    lowStockOnly.map((product) => product.name).join(', '),
  )

  const detail = await api.products.detail(milk.id, { salesDays: 28 })
  check('suppliers returned', detail.suppliers.length >= 2, detail.suppliers.length)
  check('sales history returned', detail.recent_sales.length === 28, detail.recent_sales.length)
  check(
    'suppliers are cheapest first',
    detail.suppliers.every(
      (supplier, index) =>
        index === 0 ||
        supplier.price_per_unit_paise >=
          (detail.suppliers[index - 1]?.price_per_unit_paise ?? 0),
    ),
  )

  const lowStock = await api.inventory.lowStock()
  check('low-stock list works', lowStock.some((item) => item.name === 'Milk'))

  const sweep = await api.inventory.check()
  check('sweep counted every product', sweep.products_checked === 4, sweep.products_checked)

  section('3. record a sale (the loop that drives everything)')
  const before = await stockOf('Milk')
  const sale = await api.sales.record({ product_id: milk.id, quantity: 5 })
  check('stock decreased by 5', sale.stock_after === before - 5, sale.stock_after)
  check('sale echoes the product', sale.product_name === 'Milk')
  const afterSale = await stockOf('Milk')
  check('list reflects the sale', afterSale === before - 5, afterSale)

  await expectApiError(
    'oversized sale refused',
    () => api.sales.record({ product_id: milk.id, quantity: 999_999 }),
    { status: 400, code: 'INSUFFICIENT_STOCK' },
  )
  check('stock unchanged after refusal', (await stockOf('Milk')) === afterSale)

  section('4. proposal (real LLM provider path)')
  const proposal = await api.proposals.create(milk.id)
  check('proposal created', proposal.status === 'proposed', proposal.status)
  check(
    'amount = quantity x db price',
    proposal.total_amount_paise ===
      proposal.recommended_quantity * proposal.unit_price_paise,
    formatInr(proposal.total_amount_paise),
  )
  check('forecast reasoning present', proposal.forecast_reasoning.length > 20)
  check('supplier reasoning present', proposal.supplier_reasoning.length > 20)
  check('rupee field is a string', typeof proposal.total_amount === 'string')
  console.log(
    `       order #${proposal.order_id}: ${proposal.recommended_quantity} ` +
      `${proposal.unit} from ${proposal.supplier_name} = ` +
      `${formatInr(proposal.total_amount_paise)}`,
  )

  const proposalList = await api.proposals.list()
  check(
    'appears in the proposals list',
    proposalList.some((order) => order.id === proposal.order_id),
  )

  section('5. guardrail and precondition errors surface correctly')
  const coffee = products.find((product) => product.name === 'Coffee Beans')
  const rice = products.find((product) => product.name === 'Rice')
  if (coffee) {
    const error = await expectApiError(
      'coffee breaches the per-order cap',
      () => api.proposals.create(coffee.id),
      { status: 422, code: 'ORDER_SPEND_LIMIT_EXCEEDED' },
    )
    if (error) {
      const friendly = toFriendlyError(error)
      check('friendly error is not retryable', friendly.retryable === false)
      console.log(`       "${friendly.title}" — ${friendly.action ?? ''}`)
    }
  }
  if (rice) {
    await expectApiError(
      'rice is not low stock',
      () => api.proposals.create(rice.id),
      { status: 400, code: 'PRODUCT_NOT_LOW_STOCK' },
    )
  }
  await expectApiError(
    'unknown product is 404',
    () => api.products.detail(999_999),
    { status: 404, code: 'PRODUCT_NOT_FOUND' },
  )

  section('6. reject a proposal')
  const toReject = await api.proposals.create(milk.id)
  const rejected = await api.orders.reject(toReject.order_id, {
    reason: 'Smoke test rejection.',
  })
  check('order is rejected', rejected.status === 'rejected', rejected.status)
  const rejectError = await expectApiError(
    'cannot reject twice',
    () => api.orders.reject(toReject.order_id),
    { status: 409, code: 'ORDER_NOT_REJECTABLE' },
  )
  check('rejection conflict reads as benign', toFriendlyError(rejectError).benign === true)
  await expectApiError(
    'rejected order cannot be approved',
    () => api.orders.approve(toReject.order_id),
    { status: 409 },
  )

  section('7. approve (real RazorpayX provider path)')
  const stockBeforeApproval = await stockOf('Milk')
  const approval = await api.orders.approve(proposal.order_id)
  check('order is approved', approval.order.status === 'approved', approval.order.status)
  check('order is NOT paid', (approval.order.status as OrderStatus) !== 'paid')
  check('payout id stored', Boolean(approval.payout_id), approval.payout_id)
  check('awaiting settlement', isAwaitingSettlement(approval.order) === true)
  check('not terminal yet', isTerminal(approval.order.status) === false)
  check(
    'label reads as processing',
    orderStatusLabel(approval.order.status) === 'Payment processing',
    orderStatusLabel(approval.order.status),
  )
  check('stock unchanged by approval', (await stockOf('Milk')) === stockBeforeApproval)

  const payoutId = approval.payout_id
  if (!payoutId) throw new Error('no payout id returned')

  section('8. duplicate approval is refused, not paid twice')
  const dupe = await expectApiError(
    'second approval is 409',
    () => api.orders.approve(proposal.order_id),
    { status: 409, code: 'PAYOUT_ALREADY_REQUESTED' },
  )
  check('duplicate reads as benign', toFriendlyError(dupe).benign === true)

  section('9. spending reflects the approval')
  const spending = await api.spending.summary({ historyDays: 7 })
  check(
    'committed spend includes the order',
    spending.committed_today_paise >= approval.amount_paise,
    formatInr(spending.committed_today_paise),
  )
  check('history has 7 days', spending.history.length === 7, spending.history.length)
  check('exactly one day marked today', spending.history.filter((d) => d.is_today).length === 1)
  check(
    'counted statuses are approved+paid',
    [...spending.counted_statuses].sort().join(',') === 'approved,paid',
    spending.counted_statuses.join(','),
  )

  section('10. intermediate webhook must not settle')
  const queued = await sendWebhook('payout.queued', payoutId, 'smoke_queued')
  check('queued accepted', queued.status === 200, queued.status)
  check('outcome acknowledged', queued.body.outcome === 'acknowledged', queued.body.outcome)
  check('order still approved', queued.body.order_status === 'approved')
  check('stock still unchanged', (await stockOf('Milk')) === stockBeforeApproval)

  section('11. payout.processed settles and raises stock')
  const processed = await sendWebhook('payout.processed', payoutId, 'smoke_processed')
  check('processed applied', processed.body.outcome === 'applied', processed.body.outcome)
  check('order is paid', processed.body.order_status === 'paid')

  const settled = await api.orders.detail(proposal.order_id)
  check('detail says paid', settled.status === 'paid', settled.status)
  check('terminal now', isTerminal(settled.status) === true)
  check('no longer awaiting settlement', isAwaitingSettlement(settled) === false)
  const stockAfterPaid = await stockOf('Milk')
  check(
    'stock rose by the order quantity',
    stockAfterPaid === stockBeforeApproval + settled.quantity,
    `${stockBeforeApproval} -> ${stockAfterPaid}`,
  )

  section('12. duplicate webhook must not double-increment')
  const again = await sendWebhook('payout.processed', payoutId, 'smoke_processed')
  check('flagged duplicate', again.body.duplicate === true)
  check('stock unchanged', (await stockOf('Milk')) === stockAfterPaid)

  const freshId = await sendWebhook('payout.processed', payoutId, 'smoke_processed_2')
  check('fresh event id -> already_applied', freshId.body.outcome === 'already_applied')
  check('stock still unchanged', (await stockOf('Milk')) === stockAfterPaid)

  section('13. audit trail reconstructs the decision')
  const trail = await api.audit.trail(proposal.order_id)
  const actions = trail.entries.map((entry) => entry.action)
  for (const expected of [
    'PROPOSAL_CREATED',
    'ORDER_APPROVED',
    'RAZORPAY_PAYOUT_REQUESTED',
    'RAZORPAY_PAYOUT_CREATED',
    'PAYOUT_PROCESSED',
    'INVENTORY_UPDATED',
  ]) {
    check(`trail contains ${expected}`, actions.includes(expected))
  }
  check(
    'inventory updated exactly once',
    actions.filter((action) => action === 'INVENTORY_UPDATED').length === 1,
  )
  check('trail is oldest-first', actions.indexOf('PROPOSAL_CREATED') < actions.indexOf('PAYOUT_PROCESSED'))

  const agentEvents = await api.audit.list({ actor: 'agent' })
  check('agent reasoning preserved', agentEvents.length >= 2, agentEvents.length)
  check(
    'agent entries carry reasoning text',
    agentEvents.every((entry) => (entry.reasoning_text ?? '').length > 10),
  )

  section('14. failed payout leaves stock alone')
  await api.sales.record({ product_id: milk.id, quantity: 80 })
  const failing = await api.proposals.create(milk.id)
  const failingApproval = await api.orders.approve(failing.order_id)
  const stockBeforeFailure = await stockOf('Milk')
  const failed = await sendWebhook(
    'payout.failed',
    failingApproval.payout_id ?? '',
    'smoke_failed',
  )
  check('order failed', failed.body.order_status === 'failed', failed.body.order_status)
  check('stock unchanged', (await stockOf('Milk')) === stockBeforeFailure)
  const failedDetail = await api.orders.detail(failing.order_id)
  check('failure reason surfaced', Boolean(failedDetail.payment.failure_reason))
  await expectApiError(
    'failed order cannot be re-approved',
    () => api.orders.approve(failing.order_id),
    { status: 409 },
  )

  section('15. unsigned webhook is refused')
  const forged = await fetch(`${BASE_URL}/api/webhooks/razorpayx`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event: 'payout.processed' }),
  })
  check('401 without signature', forged.status === 401, forged.status)

  console.log(
    `\n${failures === 0 ? 'ALL SMOKE CHECKS PASSED' : 'SMOKE CHECKS FAILED'} ` +
      `(${checks - failures}/${checks})`,
  )
  process.exit(failures === 0 ? 0 : 1)
}

main().catch((error) => {
  console.error('\nSmoke test crashed:', error)
  process.exit(1)
})
