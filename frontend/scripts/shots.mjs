/**
 * Screenshot every route and report what actually rendered.
 *
 *   cd frontend
 *   node scripts/shots.mjs                     # expects the dev server on :5173
 *   SHOT_BASE=http://localhost:4173 node scripts/shots.mjs
 *
 * Purpose is verification, not decoration: it launches the real app in a real
 * browser, waits for the network to settle, and asserts that each page mounted
 * and rendered data rather than a blank frame or an error boundary. Console
 * errors and failed requests are collected and reported, because a React app
 * that throws still serves a 200.
 *
 * Once the supplied design is implemented this becomes the page-by-page visual
 * check: compare `screenshots/*.png` against the design for each screen.
 */

import { mkdir, writeFile } from 'node:fs/promises'
import { chromium } from 'playwright'

const BASE = process.env.SHOT_BASE ?? 'http://localhost:5173'
const OUT = 'screenshots'

const ROUTES = [
  { path: '/login', name: 'login', expect: ['Sign in'] },
  { path: '/signup', name: 'signup', expect: ['Create your workspace'] },
  { path: '/onboarding', name: 'onboarding', expect: ['Welcome', 'ReStock AI'] },
  { path: '/', name: 'dashboard', expect: ['Dashboard', 'Good morning'] },
  { path: '/inventory', name: 'inventory', expect: ['Inventory'] },
  { path: '/inventory/1', name: 'product-detail', expect: ['Facts'] },
  { path: '/recommendations', name: 'recommendations', expect: ['Recommendations', 'AI Proposals'] },
  { path: '/orders', name: 'orders', expect: ['Orders'] },
  { path: '/spending', name: 'spending', expect: ['Spending'] },
  { path: '/activity', name: 'activity', expect: ['Activity'] },
  { path: '/settings', name: 'settings', expect: ['Settings', 'Business profile'] },
  { path: '/nope', name: 'not-found', expect: ['Not Found'] },
]

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]

let failures = 0

function check(label, ok, detail) {
  if (!ok) failures += 1
  const suffix = detail === undefined ? '' : `  -> ${detail}`
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${label}${suffix}`)
}

async function main() {
  await mkdir(OUT, { recursive: true })

  const browser = await chromium.launch()
  const summary = []

  for (const viewport of VIEWPORTS) {
    console.log(`\n=== ${viewport.name} (${viewport.width}x${viewport.height}) ===`)

    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      deviceScaleFactor: 1,
    })

    for (const route of ROUTES) {
      const page = await context.newPage()

      const consoleErrors = []
      const failedRequests = []
      page.on('console', (message) => {
        if (message.type() === 'error') consoleErrors.push(message.text())
      })
      page.on('requestfailed', (request) => {
        failedRequests.push(`${request.method()} ${request.url()}`)
      })

      await page.goto(`${BASE}${route.path}`, { waitUntil: 'networkidle' })
      // The placeholders render as soon as their queries settle; give the
      // slowest a beat rather than racing it.
      await page.waitForTimeout(600)

      const file = `${OUT}/${route.name}-${viewport.name}.png`
      await page.screenshot({ path: file, fullPage: true })

      const text = (await page.locator('body').innerText()).replace(/\s+/g, ' ').trim()

      const mounted = text.length > 0
      const lowerText = text.toLowerCase()
      const hasExpected = route.expect.every((token) => lowerText.includes(token.toLowerCase()))
      const noLoadingStuck = !lowerText.includes('loading…') || route.path === '/nope'

      check(
        `${route.path} mounted`,
        mounted && hasExpected,
        mounted ? `"${text.slice(0, 70)}"` : 'BLANK PAGE',
      )
      if (consoleErrors.length) {
        check(`${route.path} console clean`, false, consoleErrors[0].slice(0, 90))
      }
      if (failedRequests.length) {
        check(`${route.path} requests ok`, false, failedRequests[0].slice(0, 90))
      }
      if (!noLoadingStuck) {
        check(`${route.path} finished loading`, false, 'still showing a loading state')
      }

      summary.push({
        route: route.path,
        viewport: viewport.name,
        file,
        chars: text.length,
        consoleErrors: consoleErrors.length,
        failedRequests: failedRequests.length,
        text: text.slice(0, 400),
      })

      await page.close()
    }

    await context.close()
  }

  await browser.close()
  await writeFile(`${OUT}/report.json`, JSON.stringify(summary, null, 2))

  console.log(
    `\n${failures === 0 ? 'ALL PAGES RENDERED' : `${failures} PAGE CHECK(S) FAILED`}` +
      ` — ${summary.length} screenshots in ${OUT}/`,
  )
  process.exit(failures === 0 ? 0 : 1)
}

main().catch((error) => {
  console.error('Screenshot run crashed:', error)
  process.exit(1)
})
