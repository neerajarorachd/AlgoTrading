// UI ELEMENT TEST SUITE -- presence and position of UI components.
//
// LOCKED. Tests here are added or changed ONLY when the user asks for that
// specific test case. Feature work never edits this file: if a feature
// seems to need a change here, stop and ask the user
// "Change in UI Element Position Test Case" (which test, and why) first.
// A failure here means a feature broke the approved layout -- fix the
// feature, don't adjust the test.
//
// Functional checks live in ui_test_suite.cjs; this file is only "is it
// there, and is it where it belongs".
//
// Needs both dev servers running. Run:
//   node ui_element_tests.cjs                              (live, 5173)
//   UI_BASE=http://localhost:5174 node ui_element_tests.cjs (test stack)
const { chromium } = require('playwright-core')

const BASE = process.env.UI_BASE || 'http://localhost:5173'
const results = []
function checkTrue(name, cond, note) {
  const pass = !!cond
  results.push({ name, pass, note })
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${pass || note === undefined ? '' : `  (${JSON.stringify(note)})`}`)
}

// The user's saved preferences (2026-10-15) are snapshotted, cleared so the
// layout starts from defaults, and put back after the run -- on live they
// are the user's own.
async function prefs(method, body) {
  try {
    const r = await fetch(`${BASE}/api/preferences`, method === 'GET' ? {} : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    return await r.json()
  } catch {
    return null
  }
}
const nullsFor = (obj) => Object.fromEntries(Object.keys(obj ?? {}).map((k) => [k, null]))

// chart panel modes, read from the layout itself
async function depthMode(card) {
  if (await card.locator('[data-right-column] [data-side-panel]').count()) return 'right'
  if (await card.locator('[data-bottom-row] [data-side-panel]').count()) return 'below'
  return 'hidden'
}
async function indicatorsMode(card) {
  return (await card.locator('[data-indicators-window]').first().getAttribute('data-indicators-window').catch(() => null)) ?? 'hidden'
}
async function setMode(card, tool, read, mode) {
  for (let i = 0; i < 3 && (await read(card)) !== mode; i++) {
    await card.locator(`[data-chart-tool="${tool}"]`).first().click()
    await new Promise((r) => setTimeout(r, 400))
  }
  return (await read(card)) === mode
}
const box = async (loc) => ((await loc.count()) ? loc.first().boundingBox() : null)

;(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1400, height: 1000 } })
  const savedPrefs = await prefs('GET')
  if (savedPrefs) await prefs('PUT', nullsFor(savedPrefs))
  try {
    // ==================== Workspace ( / ) ====================
    {
      const page = await context.newPage()
      await page.goto(`${BASE}/`, { waitUntil: 'load' })
      await page.waitForTimeout(3000) // StrictMode double-mount settles (see ui_test_suite.cjs)

      // E1 (2026-10-08): "+" button is visible in the Tab pane.
      const pane = page.locator('[data-tab-pane]').first()
      const plus = pane.locator('[data-add-tab]')
      checkTrue('E1 Workspace: + button is visible in the Tab pane',
        (await plus.count()) === 1 && (await plus.isVisible()))

      // E2 (2026-10-15): the screen timeframe bar (1m / 3m / 5m / All) is
      // visible just above the instruments grid.
      const bar = page.locator('[data-timeframe-bar]')
      const grid = page.locator('[data-instrument-grid]').first()
      await grid.waitFor({ timeout: 25000 }).catch(() => {})
      const barBox = (await bar.count()) === 1 ? await bar.boundingBox() : null
      const gridBox = (await grid.count()) === 1 ? await grid.boundingBox() : null
      checkTrue('E2 Workspace: timeframe bar is visible above the grid',
        barBox && gridBox && (await bar.isVisible()) && barBox.y + barBox.height <= gridBox.y,
        { barBox, gridBox })

      await page.close()
    }

    // ==================== chart: Depth + Indicators panels (2026-10-15) ====================
    {
      const page = await context.newPage()
      await page.goto(`${BASE}/`, { waitUntil: 'load' })
      await page.waitForFunction(() => document.querySelectorAll('[data-instrument-grid] tbody tr').length > 1, null, { timeout: 30000 })
      if (!(await page.locator('.chart-grid .chart-card').count())) await page.locator('[data-instrument-grid] tbody tr').first().click()
      const card = page.locator('.chart-grid .chart-card').first()
      await card.locator('[data-chart-stack] canvas').first().waitFor({ timeout: 25000 })
      await page.waitForTimeout(1500)
      await setMode(card, 'depth', depthMode, 'hidden')
      await setMode(card, 'indicators', indicatorsMode, 'hidden')
      const stack = card.locator('[data-chart-stack]')

      // E3: Depth button cycles hidden -> right -> below -> hidden; right is
      // beside the chart, below is under it
      const depthSeen = []
      let depthRightOk = false
      let depthBelowOk = false
      for (let i = 0; i < 3; i++) {
        await card.locator('[data-chart-tool="depth"]').first().click()
        await page.waitForTimeout(500)
        const m = await depthMode(card)
        depthSeen.push(m)
        const s = await box(stack)
        const d = await box(card.locator('[data-side-panel]'))
        if (m === 'right') depthRightOk = !!(s && d && d.x >= s.x + s.width - 1)
        if (m === 'below') depthBelowOk = !!(s && d && d.y >= s.y + s.height - 1)
      }
      checkTrue('E3 Chart: Depth button cycles hidden -> right -> below -> hidden', depthSeen.join(',') === 'right,below,hidden', depthSeen)
      checkTrue('E3 Chart: Depth "right" is beside the chart, "below" is under it', depthRightOk && depthBelowOk, { depthRightOk, depthBelowOk })

      // E4: Indicators button cycles hidden -> right -> below -> hidden; same places
      const indSeen = []
      let indRightOk = false
      let indBelowOk = false
      for (let i = 0; i < 3; i++) {
        await card.locator('[data-chart-tool="indicators"]').first().click()
        await page.waitForTimeout(500)
        const m = await indicatorsMode(card)
        indSeen.push(m)
        const s = await box(stack)
        const w = await box(card.locator('[data-indicators-window]'))
        if (m === 'right') indRightOk = !!(s && w && w.x >= s.x + s.width - 1)
        if (m === 'below') indBelowOk = !!(s && w && w.y >= s.y + s.height - 1)
      }
      checkTrue('E4 Chart: Indicators button cycles hidden -> right -> below -> hidden', indSeen.join(',') === 'right,below,hidden', indSeen)
      checkTrue('E4 Chart: Indicators "right" is beside the chart, "below" is under it', indRightOk && indBelowOk, { indRightOk, indBelowOk })

      // E5 + E6: both on the right -- one column, Depth on top, Indicators
      // under it, left edges aligned; the column starts at the chart's top
      // and is exactly the chart's height, never taller
      await setMode(card, 'depth', depthMode, 'right')
      await setMode(card, 'indicators', indicatorsMode, 'right')
      await page.waitForTimeout(400)
      {
        const s = await box(stack)
        const col = await box(card.locator('[data-right-column]'))
        const d = await box(card.locator('[data-right-column] [data-side-panel]'))
        const w = await box(card.locator('[data-right-column] [data-indicators-window]'))
        checkTrue('E5 Chart: Depth and Indicators on the right share one column, Depth on top',
          (await card.locator('[data-right-column]').count()) === 1 && d && w && d.y + d.height <= w.y + 1 && Math.abs(d.x - w.x) <= 1,
          { d, w })
        checkTrue('E6 Chart: the right column starts at the chart top and matches its height',
          s && col && Math.abs(col.y - s.y) <= 1 && Math.abs(col.height - s.height) <= 1, { s, col })
        checkTrue('E6 Chart: nothing in the right column goes below the chart',
          s && d && w && d.y + d.height <= s.y + s.height + 1 && w.y + w.height <= s.y + s.height + 1, { s, d, w })
      }
      // Indicators alone on the right: still exactly the chart's height
      await setMode(card, 'depth', depthMode, 'hidden')
      await page.waitForTimeout(300)
      {
        const s = await box(stack)
        const w = await box(card.locator('[data-right-column] [data-indicators-window]'))
        checkTrue('E6 Chart: Indicators alone on the right is the chart height, not taller',
          s && w && Math.abs(w.y - s.y) <= 1 && w.y + w.height <= s.y + s.height + 1 && w.height >= s.height - 2, { s, w })
      }

      // E7 + E8: both below -- one row under the chart, Depth first then
      // Indicators, tops aligned; Indicators below is 320px tall
      await setMode(card, 'depth', depthMode, 'below')
      await setMode(card, 'indicators', indicatorsMode, 'below')
      await page.waitForTimeout(400)
      {
        const s = await box(stack)
        const d = await box(card.locator('[data-bottom-row] [data-side-panel]'))
        const w = await box(card.locator('[data-bottom-row] [data-indicators-window]'))
        checkTrue('E7 Chart: Depth and Indicators below share one row under the chart, Depth first',
          s && d && w && d.y >= s.y + s.height - 1 && w.y >= s.y + s.height - 1 && d.x + d.width <= w.x + 1 && Math.abs(d.y - w.y) <= 1,
          { s, d, w })
        checkTrue('E8 Chart: Indicators below is 320px tall', w && Math.abs(w.height - 320) <= 2, w)
        const c = await box(card)
        checkTrue('E8 Chart: the row below stays inside the chart card', c && w && w.y + w.height <= c.y + c.height + 1 && w.x + w.width <= c.x + c.width + 1, { c, w })
      }

      // E9: the page OPENS with Indicators saved on the right (then Depth is
      // added) -- the chart keeps its own height (no empty band under the
      // time axis) and the column matches it. The user's screenshot,
      // 2026-10-15: the column stretched the chart frame on load.
      await setMode(card, 'depth', depthMode, 'hidden')
      await setMode(card, 'indicators', indicatorsMode, 'right')
      await page.waitForTimeout(1000) // the setting is saved
      await page.reload({ waitUntil: 'load' })
      await page.waitForFunction(() => document.querySelectorAll('[data-instrument-grid] tbody tr').length > 1, null, { timeout: 30000 })
      const card2 = page.locator('.chart-grid .chart-card').first()
      await card2.locator('[data-chart-stack] canvas').first().waitFor({ timeout: 25000 })
      await page.waitForTimeout(2500)
      const fit = async () => card2.evaluate((el) => {
        const stack = el.querySelector('[data-chart-stack]').getBoundingClientRect()
        const last = [...el.querySelectorAll('[data-chart-stack] canvas')].reduce((m, c) => Math.max(m, c.getBoundingClientRect().bottom), 0)
        const col = el.querySelector('[data-right-column]')?.getBoundingClientRect()
        return { stackH: Math.round(stack.height), emptyBelowCharts: Math.round(stack.bottom - last), colH: col && Math.round(col.height), colTopGap: col && Math.round(col.top - stack.top) }
      })
      let f = await fit()
      checkTrue('E9 Chart: opening with Indicators on the right keeps the chart its own height',
        (await indicatorsMode(card2)) === 'right' && f.emptyBelowCharts <= 4 && Math.abs(f.colH - f.stackH) <= 1 && Math.abs(f.colTopGap) <= 1, f)
      await setMode(card2, 'depth', depthMode, 'right')
      await page.waitForTimeout(600)
      f = await fit()
      checkTrue('E9 Chart: ...and adding Depth on top does not grow it either',
        f.emptyBelowCharts <= 4 && Math.abs(f.colH - f.stackH) <= 1, f)

      await setMode(card2, 'depth', depthMode, 'hidden')
      await setMode(card2, 'indicators', indicatorsMode, 'hidden')
      await page.close()
    }
  } catch (err) {
    console.log('SUITE CRASHED:', err)
    results.push({ name: 'suite ran to completion', pass: false })
  }

  const failed = results.filter((r) => !r.pass)
  console.log('\n================ UI ELEMENT TEST SUITE (locked) ================')
  console.log(`${results.length - failed.length}/${results.length} passed`)
  await browser.close()
  if (savedPrefs) {
    const now = await prefs('GET')
    await prefs('PUT', { ...nullsFor(now), ...savedPrefs })
  }
  process.exit(failed.length ? 1 : 0)
})()
