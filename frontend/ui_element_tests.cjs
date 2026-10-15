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

;(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1400, height: 1000 } })
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
  } catch (err) {
    console.log('SUITE CRASHED:', err)
    results.push({ name: 'suite ran to completion', pass: false })
  }

  const failed = results.filter((r) => !r.pass)
  console.log('\n================ UI ELEMENT TEST SUITE (locked) ================')
  console.log(`${results.length - failed.length}/${results.length} passed`)
  await browser.close()
  process.exit(failed.length ? 1 : 0)
})()
