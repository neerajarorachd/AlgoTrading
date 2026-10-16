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
// Graph only (2026-10-15) is a saved per-user setting that another open
// session can turn on mid-run -- every layout test starts with it off
async function graphOnlyOff(card) {
  const b = card.locator('[data-chart-tool="graphonly"]').first()
  if ((await b.count()) && (await b.getAttribute('aria-pressed')) === 'true') {
    await b.click()
    await new Promise((r) => setTimeout(r, 400))
  }
}

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
      await graphOnlyOff(card)
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
      await graphOnlyOff(card2)
      await page.waitForTimeout(500)
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

      // E10: "Graph only" hides the panels -- RSI/MACD/Stoch panes, Depth,
      // Indicators window -- leaves the price graph as it is, and turning it
      // off brings back exactly the layout before (user, 2026-10-15)
      const layout = (c) => c.evaluate((el) => ({
        panes: [...el.querySelectorAll('[data-chart-stack] [data-pane]')].map((p) => !p.hidden),
        right: !!el.querySelector('[data-right-column]'),
        rightDepth: !!el.querySelector('[data-right-column] [data-side-panel]'),
        rightIndicators: !!el.querySelector('[data-right-column] [data-indicators-window]'),
        below: !!el.querySelector('[data-bottom-row]'),
        belowDepth: !!el.querySelector('[data-bottom-row] [data-side-panel]'),
        belowIndicators: !!el.querySelector('[data-bottom-row] [data-indicators-window]'),
        dots: el.querySelector('[data-diagrams]')?.getAttribute('data-dot-count'),
      }))
      const tool = (name) => card2.locator(`[data-chart-tool="${name}"]`).first()
      // the mixed layout: RSI off, MACD + Stoch on, Depth below, Indicators right
      if (await tool('rsi').getAttribute('aria-pressed') === 'true') await tool('rsi').click()
      for (const k of ['macd', 'stoch']) if (await tool(k).getAttribute('aria-pressed') !== 'true') await tool(k).click()
      await setMode(card2, 'depth', depthMode, 'below')
      await setMode(card2, 'indicators', indicatorsMode, 'right')
      await page.waitForTimeout(500)
      const before = await layout(card2)
      await tool('graphonly').click()
      await page.waitForTimeout(500)
      const during = await layout(card2)
      checkTrue('E10 Chart: Graph only hides every panel (panes, Depth, Indicators)',
        during.panes.every((v) => !v) && !during.right && !during.below, during)
      checkTrue('E10 Chart: ...the price graph stays as it was',
        await card2.locator('[data-chart-stack] canvas').first().isVisible() && Math.abs(Number(during.dots) - Number(before.dots)) <= 1, { before: before.dots, during: during.dots })
      checkTrue('E10 Chart: ...and the panel buttons are inactive meanwhile',
        await tool('depth').isDisabled() && await tool('indicators').isDisabled() && await tool('rsi').isDisabled())
      await tool('graphonly').click()
      await page.waitForTimeout(600)
      const after = await layout(card2)
      checkTrue('E10 Chart: turning Graph only off restores exactly the layout before',
        JSON.stringify({ ...after, dots: null }) === JSON.stringify({ ...before, dots: null }), { before, after })

      // E11: Graph only is remembered across a reload; turning it off then
      // still restores the same layout (Depth's place is per page, so only
      // the saved parts are compared: panes and Indicators)
      await tool('graphonly').click()
      await page.waitForTimeout(1000) // saved
      await page.reload({ waitUntil: 'load' })
      await page.waitForFunction(() => document.querySelectorAll('[data-instrument-grid] tbody tr').length > 1, null, { timeout: 30000 })
      const card3 = page.locator('.chart-grid .chart-card').first()
      await card3.locator('[data-chart-stack] canvas').first().waitFor({ timeout: 25000 })
      await page.waitForTimeout(1500)
      const reloaded = await layout(card3)
      checkTrue('E11 Chart: Graph only is still on after a reload',
        await card3.locator('[data-chart-tool="graphonly"]').first().getAttribute('aria-pressed') === 'true' && reloaded.panes.every((v) => !v) && !reloaded.right && !reloaded.below, reloaded)
      await card3.locator('[data-chart-tool="graphonly"]').first().click()
      await page.waitForTimeout(600)
      const restored = await layout(card3)
      checkTrue('E11 Chart: ...and turning it off restores the saved panes and Indicators',
        JSON.stringify(restored.panes) === JSON.stringify(before.panes) && restored.rightIndicators === before.rightIndicators, { before, restored })

      // back to defaults
      await graphOnlyOff(card3)
      const t3 = (name) => card3.locator(`[data-chart-tool="${name}"]`).first()
      if (await t3('rsi').getAttribute('aria-pressed') !== 'true') await t3('rsi').click()
      await setMode(card3, 'depth', depthMode, 'hidden')
      await setMode(card3, 'indicators', indicatorsMode, 'hidden')
      await page.close()
    }

    // ==================== chart: indicators strip + Signals / Patterns / Strategies (2026-10-16) ====================
    {
      const page = await context.newPage()
      await page.goto(`${BASE}/`, { waitUntil: 'load' })
      await page.waitForFunction(() => document.querySelectorAll('[data-instrument-grid] tbody tr').length > 1, null, { timeout: 30000 })
      if (!(await page.locator('.chart-grid .chart-card').count())) await page.locator('[data-instrument-grid] tbody tr').first().click()
      const card = page.locator('.chart-grid .chart-card').first()
      await card.locator('[data-chart-stack] canvas').first().waitFor({ timeout: 25000 })
      await page.waitForTimeout(1500)
      await graphOnlyOff(card)
      await setMode(card, 'indicators', indicatorsMode, 'hidden')

      // E12: the indicators are a strip ABOVE the graph (below the toolbar),
      // all 13 in this order, each with its current value
      const strip = card.locator('[data-indicator-strip]')
      const sb = await box(strip)
      const tb = await box(card.locator('[data-chart-toolbar]'))
      const gb = await box(card.locator('[data-chart-stack]'))
      checkTrue('E12 Chart: indicator strip sits above the graph, below the toolbar',
        sb && tb && gb && sb.y >= tb.y + tb.height - 1 && sb.y + sb.height <= gb.y + 1, { sb, tb, gb })
      const chips = await strip.locator('[data-indicator]').evaluateAll((els) => els.map((e) => ({ key: e.dataset.indicator, readout: !!e.querySelector('[data-readout]') })))
      checkTrue('E12 Chart: the strip lists the 13 indicators in order, each with its value',
        chips.map((c) => c.key).join(',') === 'volume,vwap,bb,ema5,ema14,ema21,ema50,ma21,ma50,pivots,rsi,macd,stoch' && chips.every((c) => c.readout),
        chips.map((c) => c.key))

      // E13: the window's tabs are exactly Signals, Patterns, Strategies;
      // Signals opens first
      await setMode(card, 'indicators', indicatorsMode, 'right')
      const win = card.locator('[data-indicators-window]')
      const tabs = await win.locator('[data-panel-tab]').evaluateAll((els) => els.map((e) => e.dataset.panelTab))
      checkTrue('E13 Chart: window tabs are Signals, Patterns, Strategies (in that order)', tabs.join(',') === 'signals,patterns,strategies', tabs)
      checkTrue('E13 Chart: Signals is the tab that opens first',
        (await win.locator('[data-panel-tab="signals"]').getAttribute('aria-selected')) === 'true')

      // E14: Signals = raw single signals, grouped; no indicators in it;
      // none ticked by default
      await win.locator('[data-signal-option]').first().waitFor({ timeout: 15000 }).catch(() => {})
      const groups = await win.locator('[data-signal-group]').evaluateAll((els) => els.map((e) => e.dataset.signalGroup))
      checkTrue('E14 Signals: groups are Candles, Indicator crossovers, Price action, Structure, Chart formations',
        groups.join(',') === 'candles,crossovers,price_action,structure,formations', groups)
      const sigs = await win.locator('[data-signal-option]').evaluateAll((els) => els.map((e) => ({ code: e.dataset.signalOption, on: e.querySelector('input').checked })))
      const codes = sigs.map((x) => x.code)
      const must = ['doji', 'hammer', 'shooting_star', 'bullish_engulfing', 'bearish_engulfing', 'macd_bullish_cross', 'stoch_bullish_cross', 'rsi_cross_above_60', 'bb_squeeze', 'swing_high', 'double_top']
      checkTrue('E14 Signals: lists the raw signals (candles, crossovers, price action, structure, formations)',
        must.every((c) => codes.includes(c)), must.filter((c) => !codes.includes(c)))
      checkTrue('E14 Signals: holds no indicators (VWAP, EMA, BB lines, RSI pane...)',
        !codes.some((c) => ['volume', 'vwap', 'bb', 'ema5', 'ema14', 'ema21', 'ema50', 'ma21', 'ma50', 'pivots', 'rsi', 'macd', 'stoch'].includes(c))
          && (await win.locator('[data-readout]').count()) === 0)
      checkTrue('E14 Signals: nothing ticked by default', sigs.length > 0 && sigs.every((x) => !x.on), sigs.filter((x) => x.on).map((x) => x.code))

      // E15: Patterns = the pattern table, grouped by type, each with its
      // weight; every enabled pattern ticked by default
      await win.locator('[data-panel-tab="patterns"]').click()
      await win.locator('[data-pattern-option]').first().waitFor({ timeout: 15000 }).catch(() => {})
      const ptitles = await win.locator('[data-pattern-group] p').allInnerTexts()
      checkTrue('E15 Patterns: grouped Bullish, Bearish, Caution, Avoid',
        ptitles.map((t) => t.toLowerCase().split(' ')[0]).join(',') === 'bullish,bearish,caution,avoid', ptitles)
      const pats = await win.locator('[data-pattern-option]').evaluateAll((els) => els.map((e) => ({ key: e.dataset.patternOption, on: e.querySelector('input').checked, weight: e.querySelector('.num')?.innerText.trim() })))
      checkTrue('E15 Patterns: lists the patterns (e.g. MACD departure, sideways chop) each with a weight',
        ['macd_departure_bull', 'macd_departure_bear', 'sideways_chop'].every((k) => pats.some((x) => x.key === k)) && pats.every((x) => x.weight !== undefined && x.weight !== ''), pats.length)
      checkTrue('E15 Patterns: every enabled pattern ticked by default', pats.length > 0 && pats.every((x) => x.on), pats.filter((x) => !x.on).map((x) => x.key))

      // E16: Strategies = the strategy list (or its empty note)
      await win.locator('[data-panel-tab="strategies"]').click()
      await page.waitForTimeout(500)
      checkTrue('E16 Strategies: the tab shows the strategy list',
        (await win.locator('[data-strategy-picker], [data-panel="strategies"]').count()) > 0)

      // E17: ticking ONE signal marks exactly it (plus the ticked patterns'
      // dots) -- on 1min, where the markers are compared one-to-one
      const tf1 = card.locator('[data-chart-tf="1min"]')
      if ((await tf1.getAttribute('aria-pressed')) !== 'true') { await tf1.click(); await page.waitForTimeout(3000) }
      const sym = (await card.locator('strong').first().innerText()).replace(/[^A-Z0-9&-]/g, '')
      const expectFor = (code) => page.evaluate(async ([s, code]) => {
        const c = await (await fetch(`/api/candles?symbol=${s}&exchange_segment=NSE_EQ&timeframe=1min&days=21`)).json()
        const m = (await (await fetch(`/api/candles/markers?symbol=${s}&exchange_segment=NSE_EQ&timeframe=1min&from=${c.candles[0].ts}`)).json()).markers
        return new Set(m.filter((x) => x.activity_type === 'pattern' || (code && x.activity === code)).map((x) => x.ts)).size
      }, [sym, code])
      const dots = async () => Number(await card.locator('[data-diagrams]').getAttribute('data-dot-count'))
      await win.locator('[data-panel-tab="signals"]').click()
      const base = await expectFor(null)
      let okBase = false
      for (let i = 0; i < 20 && !okBase; i++) { okBase = (await dots()) === base; if (!okBase) await page.waitForTimeout(500) }
      await win.locator('[data-signal-option="doji"] input').check()
      const withDoji = await expectFor('doji')
      let okDoji = false
      for (let i = 0; i < 20 && !okDoji; i++) { okDoji = (await dots()) === withDoji; if (!okDoji) await page.waitForTimeout(500) }
      checkTrue('E17 Signals: ticking one signal (doji) marks exactly its candles', okBase && okDoji && withDoji >= base, { base, withDoji, shown: await dots() })
      await win.locator('[data-signal-option="doji"] input').uncheck()

      // E20: every tab has "all" boxes -- Signals and Patterns per group,
      // Strategies for its list; ticking one ticks the whole group, unticking
      // clears it
      const groupAllWorks = async (prefix, optionAttr) => {
        const all = win.locator(`[data-group-all^="${prefix}"]`).first()
        if (!(await all.count())) return 'no all box'
        const group = all.locator('xpath=ancestor::div[2]')
        const items = group.locator(`[${optionAttr}] input`)
        const before = await items.evaluateAll((els) => els.map((e) => e.checked))
        await all.check(); await page.waitForTimeout(150)
        const allOn = await items.evaluateAll((els) => els.every((e) => e.checked))
        await all.uncheck(); await page.waitForTimeout(150)
        const allOff = await items.evaluateAll((els) => els.every((e) => !e.checked))
        // put the group back as it was
        const now = await items.all()
        for (let i = 0; i < now.length; i++) if (before[i]) await now[i].check()
        return allOn && allOff
      }
      await win.locator('[data-panel-tab="signals"]').click()
      const sigAll = await groupAllWorks('signal-', 'data-signal-option')
      await win.locator('[data-panel-tab="patterns"]').click()
      await win.locator('[data-pattern-option]').first().waitFor({ timeout: 10000 }).catch(() => {})
      const patAll = await groupAllWorks('pattern-', 'data-pattern-option')
      const patGroups = await win.locator('[data-group-all^="pattern-"]').count()
      await win.locator('[data-panel-tab="strategies"]').click()
      await page.waitForTimeout(400)
      const hasStrategies = (await win.locator('[data-strategy-option]').count()) > 0
      const strAll = hasStrategies ? await groupAllWorks('strategies', 'data-strategy-option') : (await win.locator('[data-group-all="strategies"]').count()) >= 0
      checkTrue('E20 Tabs: Signals and Patterns have an "all" box per group, Strategies one for its list -- each ticks/clears its group',
        sigAll === true && patAll === true && patGroups === 4 && strAll === true, { sigAll, patAll, patGroups, hasStrategies, strAll })

      // E21: every tab has a "Show on graph" box at the top; off hides that
      // tab's marks but keeps its ticks (checkboxes disabled in their state);
      // on brings the same marks back
      const showBox = (t) => win.locator(`[data-tab-show="${t}"] input`)
      const tabsHaveShow = []
      for (const t of ['signals', 'patterns', 'strategies']) {
        await win.locator(`[data-panel-tab="${t}"]`).click()
        await page.waitForTimeout(250)
        const sb2 = await box(win.locator(`[data-tab-show="${t}"]`))
        const first = await box(win.locator('[data-signal-option], [data-pattern-option], [data-strategy-option], [data-group-all]').first())
        tabsHaveShow.push((await showBox(t).count()) === 1 && (await showBox(t).isChecked()) && (!first || (sb2 && sb2.y <= first.y)))
      }
      checkTrue('E21 Tabs: each tab has a "Show on graph" box above its list, on by default', tabsHaveShow.every(Boolean), tabsHaveShow)
      await win.locator('[data-panel-tab="patterns"]').click()
      const patTicks = () => win.locator('[data-pattern-option] input').evaluateAll((els) => els.map((e) => [e.checked, e.disabled]))
      const dotsBefore = await dots()
      const ticksBefore = await patTicks()
      await showBox('patterns').uncheck()
      await page.waitForTimeout(500)
      const ticksOff = await patTicks()
      const dotsOff = await dots()
      checkTrue('E21 Patterns: Show off hides the pattern dots and locks the ticks as they are',
        dotsOff <= dotsBefore && (base === 0 || dotsOff < dotsBefore) && ticksOff.every(([on, dis], i) => dis && on === ticksBefore[i][0]), { dotsBefore, dotsOff })
      await showBox('patterns').check()
      await page.waitForTimeout(500)
      checkTrue('E21 Patterns: Show on brings the same dots back, ticks editable again',
        (await dots()) === dotsBefore && (await patTicks()).every(([on, dis], i) => !dis && on === ticksBefore[i][0]), { dotsBefore, now: await dots() })

      await setMode(card, 'indicators', indicatorsMode, 'hidden')
      await page.close()
    }

    // ==================== chart: candle width measured by x-axis time (2026-10-16) ====================
    // E18: zoomed fully in, the x-axis shows one time label per candle (10:12,
    // 10:15, 10:18 on 3-min; user, 2026-10-16) -- a 66 px candle slot here (the
    // least this chart library labels every candle at) -- on 1/3/5 min and in
    // fullscreen; never wider.
    // E19: each candle body fills ~60% of its slot (the library's own ~80% was
    // too wide; user, 2026-10-16: body half its old width).
    {
      const page = await context.newPage()
      await page.goto(`${BASE}/`, { waitUntil: 'load' })
      await page.waitForFunction(() => document.querySelectorAll('[data-instrument-grid] tbody tr').length > 1, null, { timeout: 30000 })
      if (!(await page.locator('.chart-grid .chart-card').count())) await page.locator('[data-instrument-grid] tbody tr').first().click()
      const card = page.locator('.chart-grid .chart-card').first()
      await card.locator('[data-chart-stack] canvas').first().waitFor({ timeout: 25000 })
      await page.waitForTimeout(2000)
      await graphOnlyOff(card)
      const MAX_SLOT = 66
      // waits until the chart has data (live loads 21 sessions over the tunnel, slower than the test stack)
      const per15 = async (scope) => {
        for (let i = 0; i < 40; i++) {
          const v = await scope.locator('[data-diagrams]').first().evaluate((el) => Number(el.measureView?.().barSpacing)).catch(() => NaN)
          if (Number.isFinite(v) && v > 0) return v
          await page.waitForTimeout(500)
        }
        return NaN
      }
      const zoomIn = async (scope) => {
        // the chart can sit below the window (13 rows on live) -- wheel turns must land on it
        await scope.locator('[data-chart-stack]').first().scrollIntoViewIfNeeded()
        const b = await scope.locator('[data-chart-stack] canvas').first().boundingBox()
        await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2)
        for (let i = 0; i < 45; i++) { await page.mouse.wheel(0, -400); await page.waitForTimeout(40) }
        await page.waitForTimeout(400)
      }
      const seen = {}
      for (const tf of ['1min', '3min', '5min']) {
        await card.locator(`[data-chart-tf="${tf}"]`).click()
        await page.waitForTimeout(1000)
        seen[`${tf} open`] = Math.round(await per15(card))
        await zoomIn(card)
        seen[`${tf} zoomed`] = Math.round(await per15(card))
      }
      checkTrue('E18 Chart: zoomed fully in, a candle slot is 66 px (one axis label per candle) on 1/3/5 min',
        Object.values(seen).every((v) => v > 0 && v <= MAX_SLOT + 1) && ['1min', '3min', '5min'].every((tf) => seen[`${tf} zoomed`] >= MAX_SLOT - 2), seen)
      await card.locator('button[title="Fullscreen"]').click()
      await page.waitForTimeout(1500)
      await per15(page) // loaded
      await zoomIn(page)
      const fs = Math.round(await per15(page))
      checkTrue('E18 Chart: ...and the same in fullscreen', fs >= MAX_SLOT - 2 && fs <= MAX_SLOT + 1, fs)
      const body = await page.locator('[data-diagrams]').first().evaluate((el) => {
        const d = el.measureView()
        return { ratio: Number(d.bodyRatio), body: Number(d.candleBody), slot: Number(d.barSpacing) }
      })
      checkTrue('E19 Chart: a candle body fills about 60% of its slot (not the library default ~80%)',
        Math.abs(body.ratio - 0.6) <= 0.02 && body.slot > 0 && Math.abs(body.body / body.slot - 0.6) <= 0.02, body)
      await page.locator('button', { hasText: 'Back' }).first().click().catch(() => {})
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
