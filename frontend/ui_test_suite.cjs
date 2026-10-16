// Real-browser regression suite for the actual React app (not the Claude
// Artifact preview -- see memory/artifact_vs_real_app_divergence.md for why
// that distinction matters). Requires both dev servers running:
//   backend: python run.py (from backend/, port 5000)
//   frontend: npm run dev (from frontend/, port 5173)
// Run: node ui_test_suite.cjs
//
// Real assertions (pass/fail), not printed values to eyeball -- see
// memory/ui_test_suite_mandatory.md for why this replaced one-off scripts.
// Covers BOTH real-app surfaces post-merge (market_watch_grid_redesign_plan.md):
//   - /market-watch-classic -- the pre-merge page, frozen, must stay unchanged
//   - /                     -- the new merged design (grid + tabs + below-grid charts)
const { chromium } = require('playwright-core');

const BASE = process.env.UI_BASE || 'http://localhost:5173';  // test stack: UI_BASE=http://localhost:5174
const results = [];
function check(name, actual, expected) {
  const pass = JSON.stringify(actual) === JSON.stringify(expected);
  results.push({ name, pass, actual, expected });
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}`);
}
// Polls `fn` until it returns true or `ms` passes -- for checks right after
// a click on the live stack, where live data can re-render the grid mid-check
// (two checks failed intermittently on live, 2026-10-08, with a fixed wait).
// The chart's Indicators window has 3 states (hidden -> right -> below,
// 2026-10-15); step the toolbar button until it is in `mode`.
async function setIndicatorsMode(scope, mode) {
  const btn = scope.locator('[data-chart-tool="indicators"]').first();
  for (let i = 0; i < 3; i++) {
    const current = await scope.locator('[data-indicators-window]').first().getAttribute('data-indicators-window').catch(() => null) ?? 'hidden';
    if (current === mode) return true;
    await btn.click();
    await new Promise((r) => setTimeout(r, 350));
  }
  return (await scope.locator('[data-indicators-window]').first().getAttribute('data-indicators-window').catch(() => null) ?? 'hidden') === mode;
}

// Saved preferences land on the server after a batch delay plus a DB write
// (over a second on live, through the SSH tunnel) -- poll until `ok(prefs)`.
async function waitForSavedPrefs(page, ok, ms = 8000) {
  let last = {};
  const end = Date.now() + ms;
  for (;;) {
    last = await page.evaluate(() => fetch('/api/preferences').then((r) => r.json()));
    if (ok(last) || Date.now() > end) return last;
    await new Promise((r) => setTimeout(r, 300));
  }
}

async function eventually(fn, ms = 3000) {
  const end = Date.now() + ms
  for (;;) {
    if (await fn()) return true
    if (Date.now() > end) return false
    await new Promise((r) => setTimeout(r, 100))
  }
}
function checkTrue(name, cond, note) {
  const pass = !!cond;
  results.push({ name, pass, actual: cond, expected: true, note });
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${pass || note === undefined ? '' : `  note: ${JSON.stringify(note)}`}`);
}

// Saved user preferences (2026-10-15) would carry one run's choices into the
// next -- every section that checks defaults starts from a clean slate.
async function resetPrefs() {
  try {
    const cur = await (await fetch(`${BASE}/api/preferences`)).json();
    const keys = Object.keys(cur);
    if (keys.length) {
      await fetch(`${BASE}/api/preferences`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.fromEntries(keys.map((k) => [k, null]))),
      });
    }
  } catch (e) {
    console.log('could not reset preferences:', e.message);
  }
}

// On live these are the user's real preferences: snapshot them first and put
// them back at the end, whatever the run changed.
let savedPrefs = null;
async function restorePrefs() {
  if (savedPrefs === null) return;
  try {
    const now = await (await fetch(`${BASE}/api/preferences`)).json();
    const body = { ...Object.fromEntries(Object.keys(now).map((k) => [k, null])), ...savedPrefs };
    await fetch(`${BASE}/api/preferences`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) {
    console.log('could not restore preferences:', e.message);
  }
}

(async () => {
  try { savedPrefs = await (await fetch(`${BASE}/api/preferences`)).json(); } catch { savedPrefs = null; }
  await resetPrefs();
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1400, height: 1400 } });

  // ==================== /market-watch-classic (frozen) ====================
  {
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));

    await page.goto(`${BASE}/market-watch-classic`, { waitUntil: 'load' });
    // Real timing, not a tight Playwright-speed click -- React 18 StrictMode
    // double-invokes effects on mount (chart create -> dispose -> recreate),
    // and clicking a row before that settles can catch the chart mid-recreate,
    // silently dropping that render (a real StrictMode race, not a feature
    // bug -- confirmed 2026-10-05 while verifying the candle-fallback fix,
    // see market_watch_grid_redesign_plan.md). Give it real time to settle.
    await page.waitForTimeout(4000);
    checkTrue('[classic] no page errors on load', errors.length === 0, errors);

    const h1 = await page.locator('h1').innerText();
    check('[classic] heading unchanged', h1, 'Market Watch (Classic)');

    const rowCount = await page.locator('table tbody tr').count();
    checkTrue('[classic] symbol rows rendered', rowCount > 0, rowCount);

    const firstRow = page.locator('table tbody tr').first();
    const firstSymbol = await firstRow.locator('td').first().innerText();
    await firstRow.click();
    await page.waitForTimeout(3000);

    const chartGridVisible = await page.locator('.chart-grid').count();
    checkTrue('[classic] chart-grid appears after clicking a row', chartGridVisible > 0);

    const canvasWidth = await page.locator('.chart-grid canvas').first().evaluate((el) => el.clientWidth).catch(() => 0);
    checkTrue('[classic] chart canvas has real width', canvasWidth > 0, canvasWidth);

    const apiResult = await page.evaluate(async (symbol) => {
      const rowCell = [...document.querySelectorAll('table tbody tr')]
        .find((tr) => tr.querySelector('td')?.innerText === symbol);
      return rowCell ? 'found-row' : 'no-row';
    }, firstSymbol);
    checkTrue('[classic] clicked row still identifiable', apiResult === 'found-row');

    // old binary Depth toggle must still work exactly as before -- this is
    // the whole point of keeping CandleChart's depthOpen/onToggleDepth path
    // untouched while depthMode/onCycleDepth is additive
    const depthBtn = page.locator('.chart-grid [data-chart-tool="depth"]').first();
    if (await depthBtn.count() > 0) {
      await depthBtn.click();
      await page.waitForTimeout(300);
      // DepthPanel renders "No depth data yet." until a live depth payload
      // arrives (this dev environment has no authenticated broker feed), so
      // assert the panel container showed up at all, not real field values.
      const depthPanelVisible = await page.locator('.chart-grid').first().locator('text=/Best bid|No depth data yet/').count();
      checkTrue('[classic] Depth button still opens a simple side panel', depthPanelVisible > 0);
    }

    await page.close();
  }

  // ==================== / (new merged design) ====================
  {
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    // ALGO-36: live updates over one WebSocket, no long-polling
    const socketWs = [];
    let pollRequests = 0;
    page.on('websocket', (w) => { if (w.url().includes('/socket.io/')) socketWs.push(w.url()); });
    page.on('request', (r) => { if (r.url().includes('/socket.io/') && r.url().includes('transport=polling')) pollRequests += 1; });

    await page.goto(`${BASE}/`, { waitUntil: 'load' });
    await page.waitForTimeout(4000);
    checkTrue('[new] no page errors on load', errors.length === 0, errors);
    check('[ALGO-36] live updates use exactly one WebSocket', socketWs.length, 1);
    check('[ALGO-36] no long-polling requests', pollRequests, 0);

    const h1 = await page.locator('h1').innerText();
    check('[new] grid tab heading', h1.toLowerCase(), 'workspace');

    const tabBarText = await page.locator('button', { hasText: 'Market Watch' }).first().innerText();
    checkTrue('[new] pinned grid tab present', tabBarText.includes('Market Watch'));

    // "+" add-tab menu (2026-10-08): lists the tab kinds, nothing else yet
    await page.locator('[data-tab-pane] [data-add-tab]').click();
    const addOptions = await page.locator('[data-add-tab-menu] [data-add-tab-option]').allInnerTexts();
    check('[new] + opens the add-tab options', addOptions.map((t) => t.split('\n')[0]), ['Watchlist', 'Sector Watch']);
    await page.keyboard.press('Escape');
    checkTrue('[new] add-tab menu closes on Escape', await page.locator('[data-add-tab-menu]').count() === 0);

    const rowCount = await page.locator('table tbody tr').count();
    checkTrue('[new] symbol rows rendered', rowCount > 0, rowCount);

    // ---- sortable sticky header ----
    const theadPosition = await page.locator('thead').first().evaluate((el) => getComputedStyle(el).position);
    check('[new] grid header is sticky', theadPosition, 'sticky');

    const symbolHeader = page.locator('th', { hasText: 'Symbol' }).first();
    const firstSymbolBefore = await page.locator('table tbody tr').first().locator('td').first().innerText();
    await symbolHeader.click(); // first click: desc
    await page.waitForTimeout(200);
    const firstSymbolAfterDesc = await page.locator('table tbody tr').first().locator('td').first().innerText();
    await symbolHeader.click(); // second click: asc
    await page.waitForTimeout(200);
    const firstSymbolAfterAsc = await page.locator('table tbody tr').first().locator('td').first().innerText();
    // a one-instrument DB (the seeded test stack) has nothing to reorder
    if (rowCount < 2) {
      console.log('SKIP  [new] clicking Symbol header actually reorders rows  (only 1 row)');
    } else {
      checkTrue(
        '[new] clicking Symbol header actually reorders rows',
        firstSymbolAfterDesc !== firstSymbolAfterAsc || firstSymbolAfterDesc !== firstSymbolBefore,
        { firstSymbolBefore, firstSymbolAfterDesc, firstSymbolAfterAsc },
      );
    }
    await symbolHeader.click(); // third click: clear back to default order

    // ---- merged Actions column ----
    const actionsHeaderCount = await page.locator('th', { hasText: 'Actions' }).count();
    checkTrue('[new] merged Actions column header present', actionsHeaderCount > 0);
    const removeButtonsInOldColumns = await page.locator('th', { hasText: 'Trade' }).count();
    checkTrue('[new] old separate Trade column is gone (merged into Actions)', removeButtonsInOldColumns === 0);

    // ---- page header, as finalized in the preview (ported 2026-10-06) ----
    const eyebrow = page.locator('text=Live snapshot · Workspace');
    checkTrue('[new] header eyebrow present', await eyebrow.count() > 0);
    const paramsText = await page.locator('text=/instruments registered/').first().innerText().catch(() => '');
    const totalRows = await page.evaluate(() => fetch('/api/symbols').then((r) => r.json()).then((rows) => rows.length));
    checkTrue('[new] header shows the real registered-instrument count', paramsText.includes(String(totalRows)), { paramsText, totalRows });
    const headerToggle = page.locator('button[title="Collapse header"]');
    await headerToggle.click();
    await page.waitForTimeout(150);
    checkTrue('[new] header collapse hides eyebrow + params', await eyebrow.count() === 0 && await page.locator('text=/instruments registered/').count() === 0);
    check('[new] header collapse keeps the title', (await page.locator('h1').innerText()).toLowerCase(), 'workspace');
    await page.locator('button[title="Expand header"]').click();
    await page.waitForTimeout(150);
    checkTrue('[new] header expand restores eyebrow', await eyebrow.count() > 0);

    // live-feed health badge (GET /api/feed/status) -- whatever the state,
    // it must be shown, never silently absent like the old "—"-only failure
    await page.locator('[data-feed-status]').first().waitFor({ timeout: 20000 }).catch(() => {});
    const feedTone = await page.locator('[data-feed-status]').first().getAttribute('data-feed-status').catch(() => null);
    checkTrue('[new] live-feed status badge shown', ['good', 'warning', 'critical', 'faint'].includes(feedTone), feedTone);

    // ---- INSTRUMENTS card head ----
    checkTrue('[new] INSTRUMENTS card title present', await page.locator('span', { hasText: /^Instruments$/ }).count() > 0);
    checkTrue('[new] Sort dropdown present', await page.locator('select[aria-label="Sort instruments"]').count() > 0);
    checkTrue('[new] compact "+" add button present', await page.locator('button[aria-label="Add instrument"]').count() > 0);
    const multi = page.locator('label', { hasText: 'Multi' }).locator('input[type="checkbox"]');
    check('[new] Multi checkbox defaults to off', await multi.isChecked(), false);
    checkTrue('[new] old "Hide list" / "Multiple charts" buttons are gone (moved into the card head)',
      await page.locator('button', { hasText: /^(Hide list|Multiple charts)$/ }).count() === 0);

    // ---- color tiles (watch-scores) inside the card ----
    const tiles = page.locator('button[title*="click to filter"]');
    // /api/watch-scores is ~0.6s against the VM since batching (was ~7s) -- still wait for it, not a fixed sleep
    await tiles.first().waitFor({ timeout: 25000 }).catch(() => {});
    check('[ALGO-33] all 7 colour tiles render (dark green ... red)', await tiles.count(), 7);

    // ---- "Showing X of N" footer + Show all ----
    const footer = page.locator('text=/^Showing \\d+ of \\d+$/');
    const footerText = await footer.first().innerText().catch(() => '');
    check('[new] footer counts every row', footerText, `Showing ${Math.min(5, totalRows)} of ${totalRows}`);
    const fullyVisibleRows = await page.locator('table').first().evaluate((table) => {
      const box = table.parentElement.getBoundingClientRect();
      return [...table.querySelectorAll('tbody tr')].filter((tr) => tr.getBoundingClientRect().bottom <= box.bottom + 1).length;
    });
    check('[new] exactly as many rows are fully visible as the footer claims', fullyVisibleRows, Math.min(5, totalRows));
    if (totalRows > 5) {
      await page.locator('button', { hasText: 'Show all' }).click();
      await page.waitForTimeout(200);
      check('[new] Show all lists every row', await footer.first().innerText(), `Showing ${totalRows} of ${totalRows}`);
      const scrollMax = await page.locator('table').first().evaluate((t) => getComputedStyle(t.parentElement).maxHeight);
      check('[new] Show all removes the 5-row height cap', scrollMax, 'none');
      await page.locator('button', { hasText: 'Show less' }).click();
      await page.waitForTimeout(200);
    }

    // ---- Sort dropdown: status color, and mutual exclusivity with header sort ----
    await page.locator('th', { hasText: 'Symbol' }).first().click(); // column sort on
    await page.selectOption('select[aria-label="Sort instruments"]', 'status');
    await page.waitForTimeout(200);
    checkTrue('[new] picking a dropdown sort clears the column-sort indicator',
      !(await page.locator('th', { hasText: 'Symbol' }).first().innerText()).match(/[▲▼]/));
    await page.locator('th', { hasText: 'Symbol' }).first().click();
    await page.waitForTimeout(200);
    check('[new] clicking a column header resets the dropdown', await page.locator('select[aria-label="Sort instruments"]').inputValue(), '');
    await page.locator('th', { hasText: 'Symbol' }).first().click();
    await page.locator('th', { hasText: 'Symbol' }).first().click(); // back to default order

    // ---- card collapse (replaces "Hide list") ----
    await page.locator('button[title="Collapse"]').click();
    await page.waitForTimeout(150);
    checkTrue('[new] card collapse hides the grid, tiles and footer',
      // grid rows only -- the live events panel has its own table, which has rows on live
      await eventually(async () => await page.locator('[data-instrument-grid] tbody tr').count() === 0 && await tiles.count() === 0 && await footer.count() === 0));
    await page.locator('button[title="Expand"]').click();
    await page.waitForTimeout(150);
    checkTrue('[new] card expand restores the grid', await page.locator('table tbody tr').count() > 0);

    // ---- live patterns & indicators list (2026-10-07) ----
    checkTrue('[new] live patterns & indicators panel shown', await page.locator('[data-live-events]').count() === 1);
    // the Events toggle beside the timeframe bar shows/hides that panel (2026-10-15), saved per user
    {
      const evToggle = page.locator('[data-panel-toggle="events"]');
      const tb = await page.locator('[data-timeframe-bar]').boundingBox();
      const eb = await evToggle.boundingBox();
      checkTrue('[EV] Events toggle sits beside the timeframe bar, on by default',
        eb && tb && Math.abs((eb.y + eb.height / 2) - (tb.y + tb.height / 2)) < 6 && eb.x + eb.width <= tb.x && await evToggle.getAttribute('aria-pressed') === 'true', { eb, tb });
      await evToggle.click();
      checkTrue('[EV] Events toggle off hides the panel', await eventually(async () => await page.locator('[data-live-events]').count() === 0, 2000));
      const saved = await waitForSavedPrefs(page, (x) => x['workspace.showEventsPanel'] === false);
      checkTrue('[EV] ...and the choice is saved for the user', saved['workspace.showEventsPanel'] === false, saved['workspace.showEventsPanel']);
      await evToggle.click();
      checkTrue('[EV] Events toggle on shows it again', await eventually(async () => await page.locator('[data-live-events]').count() === 1, 2000));
    }

    // ---- Actions column, as finalized in the preview (ported 2026-10-06) ----
    await page.locator('button', { hasText: 'Show all' }).click().catch(() => {});
    await page.waitForTimeout(200);
    // the grid re-fetches colours at most every 15 s while activity arrives --
    // on live a colour can change mid-check, so retry for up to 20 s
    const readActionCheck = () => page.evaluate(async () => {
      const [symbols, scores] = await Promise.all([
        fetch('/api/symbols').then((r) => r.json()),
        fetch('/api/watch-scores?timeframe=3min').then((r) => r.json()),
      ]);
      const scoreBySymbol = Object.fromEntries(symbols.map((s) => [s.symbol, scores.scores.find((x) => x.instrument_id === s.id)]));
      const bucketBySymbol = Object.fromEntries(Object.entries(scoreBySymbol).map(([k, v]) => [k, v?.bucket]));
      // ALGO-33: green -> Buy/Sell by direction, orange/yellow -> Hold, white/pink/red -> none
      const expectedFor = (sc) => {
        if (!sc) return null;
        if (sc.bucket === 'dark_green' || sc.bucket === 'light_green') return sc.direction === 'bull' ? 'buy' : sc.direction === 'bear' ? 'sell' : null;
        if (sc.bucket === 'orange' || sc.bucket === 'yellow') return 'hold';
        return null;
      };
      const expected = (b, symbol) => expectedFor(scoreBySymbol[symbol]);
      const mismatches = [];
      for (const tr of document.querySelectorAll('[data-instrument-grid] tbody tr')) {
        const symbol = tr.cells[0].innerText.split('\n')[0];
        const shown = tr.querySelector('[data-row-action]')?.dataset.rowAction ?? null;
        if (shown !== expected(bucketBySymbol[symbol], symbol)) mismatches.push({ symbol, bucket: bucketBySymbol[symbol], shown });
      }
      return mismatches;
    });
    let actionCheck = await readActionCheck();
    await eventually(async () => (actionCheck = await readActionCheck()).length === 0, 20000);
    checkTrue('[ALGO-33] every row\'s action follows the colour rule (green=Buy/Sell by direction, orange/yellow=Hold, else none)', actionCheck.length === 0, actionCheck);
    // ---- screen timeframe bar (2026-10-14) ----
    const tfBar = page.locator('[data-timeframe-bar]');
    checkTrue('[TF] timeframe bar shows 1m / 3m / 5m / All', await tfBar.locator('[data-timeframe]').allInnerTexts().then((t) => t.join(',') === '1m,3m,5m,All'));
    const barBox = await tfBar.boundingBox();
    const gridBox = await page.locator('[data-instrument-grid]').boundingBox();
    checkTrue('[TF] timeframe bar sits above the grid', barBox && gridBox && barBox.y + barBox.height <= gridBox.y, { barBox, gridBox });
    checkTrue('[TF] 3m is selected by default', await tfBar.locator('[data-timeframe="3min"]').getAttribute('aria-checked') === 'true');
    const scoreCalls = [];
    const onScoreReq = (req) => { if (req.url().includes('/api/watch-scores')) scoreCalls.push(req.url()); };
    page.on('request', onScoreReq);
    await tfBar.locator('[data-timeframe="1min"]').click();
    checkTrue('[TF] picking 1m re-fetches the colours for 1min', await eventually(async () => scoreCalls.some((u) => u.includes('timeframe=1min'))), scoreCalls);
    const tileTotal1 = await eventually(async () => {
      const [n, rows] = await Promise.all([
        page.$$eval('[data-bucket]', (els) => els.reduce((s, e) => s + Number(e.innerText.trim() || 0), 0)),
        page.locator('[data-instrument-grid] tbody tr').count(),
      ]);
      return n > 0 && n === (await page.evaluate(() => fetch('/api/symbols').then((r) => r.json()).then((s) => s.length)));
    });
    checkTrue('[TF] on 1m every instrument is counted in exactly one colour tile', tileTotal1);

    // each row's colour dot(s) vs the API for that timeframe (2026-10-15)
    const rowColours = (tf) => page.evaluate(async (tf) => {
      const [symbols, scores] = await Promise.all([
        fetch('/api/symbols').then((r) => r.json()),
        fetch(`/api/watch-scores?timeframe=${tf}`).then((r) => r.json()),
      ]);
      const bySym = Object.fromEntries(symbols.map((s) => [s.symbol, scores.scores.find((x) => x.instrument_id === s.id)]));
      const bad = [];
      for (const tr of document.querySelectorAll('[data-instrument-grid] tbody tr')) {
        const sym = tr.cells[0]?.innerText.split('\n')[0];
        const sc = bySym[sym];
        if (!sc) continue;
        if (tf === 'all') {
          const shown = [...tr.querySelectorAll('[data-row-colours="all"] [data-row-colour]')].map((d) => d.dataset.rowColour);
          const want = ['1min', '3min', '5min'].map((t) => sc.by_timeframe?.[t]?.colour ?? 'none');
          if (shown.join() !== want.join()) bad.push({ sym, shown, want });
        } else {
          const shown = tr.querySelector('[data-row-colour]')?.dataset.rowColour;
          if (tr.querySelectorAll('[data-row-colour]').length !== 1 || shown !== sc.bucket) bad.push({ sym, shown, want: sc.bucket });
        }
      }
      return bad;
    }, tf);
    let rowBad = [];
    await eventually(async () => (rowBad = await rowColours('1min')).length === 0, 6000);
    checkTrue('[TF] on 1m each row\'s dot is its 1m colour', rowBad.length === 0, rowBad);
    await tfBar.locator('[data-timeframe="all"]').click();
    checkTrue('[TF] on All every colour tile shows three counts (1m · 3m · 5m)', await eventually(async () => {
      const texts = await page.$$eval('[data-bucket]', (els) => els.map((e) => e.innerText.trim()));
      return texts.length === 7 && texts.every((t) => /^\d+ · \d+ · \d+$/.test(t));
    }));
    checkTrue('[TF] All asks the API for timeframe=all', scoreCalls.some((u) => u.includes('timeframe=all')), scoreCalls);
    // on All a tile counts each timeframe, so its filter keeps stocks with that
    // colour on ANY timeframe (2026-10-16: a 0 · 1 · 0 tile showed 0 rows)
    {
      const expectRows = await page.evaluate(async () => {
        const [symbols, scores] = await Promise.all([fetch('/api/symbols').then((r) => r.json()), fetch('/api/watch-scores?timeframe=all').then((r) => r.json())]);
        const out = {};
        for (const c of ['dark_green', 'light_green', 'orange', 'yellow', 'white', 'pink', 'red']) {
          out[c] = symbols.filter((s) => { const sc = scores.scores.find((x) => x.instrument_id === s.id); return ['1min', '3min', '5min'].some((t) => sc?.by_timeframe?.[t]?.colour === c); }).length;
        }
        return out;
      });
      const bad = [];
      for (const [colour, n] of Object.entries(expectRows)) {
        const tile = page.locator(`[data-bucket="${colour}"]`);
        const tileSum = (await tile.innerText()).split('·').reduce((t, x) => t + Number(x.trim() || 0), 0);
        await tile.click();
        await page.waitForTimeout(300);
        const rows = await page.locator('[data-instrument-grid] tbody tr').evaluateAll((trs) => trs.filter((tr) => tr.cells.length > 1).length);
        if (rows !== n || (tileSum > 0 && rows === 0)) bad.push({ colour, tileSum, expected: n, rows });
        await tile.click(); // clear
        await page.waitForTimeout(200);
      }
      checkTrue('[TF] on All, a colour tile shows every stock with that colour on any timeframe', bad.length === 0, bad);
    }
    await eventually(async () => (rowBad = await rowColours('all')).length === 0, 6000);
    checkTrue('[TF] on All each row shows three dots: its 1m, 3m and 5m colours', rowBad.length === 0, rowBad);
    // colour sort: colour order first, then within a colour the most green
    // timeframes first (3 of 1m/3m/5m, then 2, then 1) -- 2026-10-15
    {
      const sortSel = page.locator('select[aria-label="Sort instruments"]');
      const prevSort = await sortSel.inputValue();
      await sortSel.selectOption('status');
      await page.waitForTimeout(500);
      const orderBad = await page.evaluate(async () => {
        const [symbols, scores] = await Promise.all([
          fetch('/api/symbols').then((r) => r.json()),
          fetch('/api/watch-scores?timeframe=all').then((r) => r.json()),
        ]);
        const ORDER = ['dark_green', 'light_green', 'orange', 'yellow', 'white', 'pink', 'red'];
        const bySym = Object.fromEntries(symbols.map((s) => [s.symbol, scores.scores.find((x) => x.instrument_id === s.id)]));
        const greens = (sc) => ['1min', '3min', '5min'].filter((t) => ['dark_green', 'light_green'].includes(sc?.by_timeframe?.[t]?.colour)).length;
        const rows = [...document.querySelectorAll('[data-instrument-grid] tbody tr')].map((tr) => tr.cells[0]?.innerText.split('\n')[0]).filter((s) => bySym[s]);
        const bad = [];
        for (let i = 1; i < rows.length; i++) {
          const a = bySym[rows[i - 1]], b = bySym[rows[i]];
          const ra = ORDER.indexOf(a.bucket), rb = ORDER.indexOf(b.bucket);
          if (ra > rb || (ra === rb && greens(a) < greens(b))) bad.push({ above: [rows[i - 1], a.bucket, greens(a)], below: [rows[i], b.bucket, greens(b)] });
        }
        return bad;
      });
      checkTrue('[SORT] colour sort: within a colour, more green timeframes first (3, then 2, then 1)', orderBad.length === 0, orderBad);
      await sortSel.selectOption(prevSort);
      await page.waitForTimeout(300);
    }
    await tfBar.locator('[data-timeframe="3min"]').click();
    checkTrue('[TF] back on 3m the tiles show single counts again', await eventually(async () => {
      const texts = await page.$$eval('[data-bucket]', (els) => els.map((e) => e.innerText.trim()));
      return texts.length === 7 && texts.every((t) => /^\d+$/.test(t));
    }));
    page.off('request', onScoreReq);

    checkTrue('[new] no "Remove" text left; every row has a × remove button',
      await eventually(async () => await page.locator('table tbody button', { hasText: /^Remove$/ }).count() === 0
        && await page.locator('[data-instrument-grid] tbody button[aria-label^="Remove "]').count() === await page.locator('[data-instrument-grid] tbody tr').count()));
    checkTrue('[new] redundant "open chart below" icon removed', await page.locator('table tbody button[title*="chart below"]').count() === 0);
    // live re-renders the grid as data arrives -- poll, and report what wrapped
    const tallest = () => page.$$eval('[data-instrument-grid] tbody tr', (rows) => rows.map((r) => {
      const c = r.cells[r.cells.length - 1];
      return { sym: r.cells[0].innerText.split('\n')[0], h: Math.round(c.getBoundingClientRect().height), text: c.innerText.replace(/\n/g, ' | ') };
    }).sort((a, b) => b.h - a.h)[0]);
    let tallestActions = await tallest();
    await eventually(async () => (tallestActions = await tallest()).h < 48, 4000);
    checkTrue('[new] Actions cells stay on one line (no wrapping)', tallestActions.h < 48, tallestActions);
    const holdBtn = page.locator('[data-row-action="hold"]').first();
    if (await holdBtn.count() > 0) {
      await holdBtn.click();
      await page.waitForTimeout(150);
      checkTrue('[new] Hold opens its informational popover', await page.locator('[data-hold-popover]').count() === 1);
      await page.mouse.click(5, 5);
      await page.waitForTimeout(150);
      checkTrue('[new] Hold popover closes on outside click', await page.locator('[data-hold-popover]').count() === 0);
    }
    await page.locator('button', { hasText: 'Show less' }).click().catch(() => {});
    await page.waitForTimeout(200);

    // ---- ALGO-34: event lists put the screen timeframe's recent events first ----
    {
      const order = (tf) => page.evaluate((screenTf) => {
        const out = { sections: [], bad: [] };
        let section = null;
        for (const tr of document.querySelectorAll('[data-live-events] tbody tr')) {
          if (tr.dataset.eventSection) { section = tr.dataset.eventSection; out.sections.push(section); continue; }
          const rowTf = tr.cells[2]?.innerText.trim();
          if (section === 'selected' && rowTf !== screenTf) out.bad.push({ section, rowTf });
          if (section === 'recent' && screenTf !== 'all' && rowTf === screenTf) out.bad.push({ section, rowTf });
        }
        return out;
      }, tf);
      const hasEvents = await eventually(async () => (await order('3min')).sections.length > 0, 20000);
      if (!hasEvents) {
        console.log('SKIP  [ALGO-34] live events list is empty on this stack right now');
      } else {
        const o = await order('3min');
        const rank = { selected: 0, recent: 1, older: 2 };
        checkTrue('[ALGO-34] live list: sections in order (screen timeframe, other timeframes, earlier)',
          o.sections.every((s, i) => i === 0 || rank[s] > rank[o.sections[i - 1]]), o.sections);
        checkTrue('[ALGO-34] live list: the first section holds only the screen timeframe (3m)', o.bad.length === 0, o.bad);
      }
      // per-stock list (☰): same order
      const listBtn = page.locator('[data-instrument-grid] button[title="Recent events"]').first();
      await listBtn.click();
      const pop = await eventually(async () => await page.locator('[data-event-section]').filter({ has: page.locator('[data-event-row]') }).count() > 0
        || await page.locator('text=No recent events').count() > 0, 10000);
      const per = await page.evaluate(() => [...document.querySelectorAll('[data-instrument-grid] [data-event-section]')].map((s) => ({
        key: s.dataset.eventSection,
        tfs: [...s.querySelectorAll('[data-event-row]')].map((r) => r.children[1]?.innerText.split(' ')[0]),
      })));
      if (!pop || per.length === 0) {
        console.log('SKIP  [ALGO-34] per-stock list has no events on this stack');
      } else {
        const rank = { selected: 0, recent: 1, older: 2 };
        checkTrue('[ALGO-34] per-stock list: sections in order', per.every((s, i) => i === 0 || rank[s.key] > rank[per[i - 1].key]), per.map((s) => s.key));
        checkTrue('[ALGO-34] per-stock list: first section is the screen timeframe only', per[0].key !== 'selected' || per[0].tfs.every((t) => t === '3min'), per[0]);
      }
      await page.mouse.click(5, 5);
    }

    // ---- user preferences: the screen reopens the way it was left (2026-10-15) ----
    {
      await page.locator('[data-instrument-grid] tbody tr').first().click(); // open one chart
      await page.waitForTimeout(500);
      await page.locator('[data-timeframe-bar] [data-timeframe="5min"]').click();
      const saved = await waitForSavedPrefs(page, (x) => x['workspace.screenTimeframe'] === '5min');
      checkTrue('[PREF] the screen timeframe is saved for the user', saved['workspace.screenTimeframe'] === '5min', saved);
      await page.reload({ waitUntil: 'load' });
      await page.locator('[data-timeframe-bar]').waitFor({ timeout: 25000 });
      checkTrue('[PREF] after a reload the screen reopens on 5m', await page.locator('[data-timeframe-bar] [data-timeframe="5min"]').getAttribute('aria-checked') === 'true');
      await page.locator('[data-timeframe-bar] [data-timeframe="3min"]').click();
      await page.waitForTimeout(800);
      // real rows, not the "No instruments registered yet" placeholder (live is slower over the tunnel)
      await page.waitForFunction(() => document.querySelectorAll('[data-instrument-grid] tbody tr').length > 1
        || !/No instruments/.test(document.querySelector('[data-instrument-grid] tbody')?.innerText ?? 'No instruments'), null, { timeout: 30000 });
      // the reload restored the open chart too -- close it so the next steps start clean
      await eventually(async () => await page.locator('.chart-grid .chart-card').count() > 0, 10000);
      const restoredCharts = await page.locator('.chart-grid .chart-card').count();
      checkTrue('[PREF] open charts come back after a reload', restoredCharts > 0, restoredCharts);
      for (let i = 0; i < restoredCharts; i++) await page.locator('.chart-grid button[title="Close chart"]').first().click();
      await page.waitForTimeout(300);
    }

    // ---- below-grid chart + 3-state depth cycle ----
    const firstRow = page.locator('table tbody tr').first();
    const firstSymbol = await firstRow.locator('td').first().innerText();
    await firstRow.click();
    await page.waitForTimeout(3000);

    const chartGridVisible = await page.locator('.chart-grid').count();
    checkTrue('[new] chart-grid appears after clicking a row', chartGridVisible > 0);
    const canvasWidth = await page.locator('.chart-grid canvas').first().evaluate((el) => el.clientWidth).catch(() => 0);
    checkTrue('[new] chart canvas has real width', canvasWidth > 0, canvasWidth);

    // ---- chart follows the screen timeframe while its sync toggle is on (2026-10-14) ----
    {
      const card = page.locator('.chart-grid').first();
      const pressedTf = () => card.locator('[data-chart-tf][aria-pressed="true"]').first().getAttribute('data-chart-tf');
      const syncBtn = card.locator('[data-chart-tool="sync"]').first();
      const screenBtn = (tf) => page.locator(`[data-timeframe-bar] [data-timeframe="${tf}"]`);
      checkTrue('[TF] chart has a sync toggle, on by default', await syncBtn.getAttribute('aria-pressed') === 'true');
      checkTrue('[TF] synced chart opens on the screen timeframe (3min)', await pressedTf() === '3min');
      await screenBtn('5min').click();
      checkTrue('[TF] screen 5m switches the synced chart to 5min', await eventually(async () => await pressedTf() === '5min'));
      await screenBtn('all').click();
      await page.waitForTimeout(300);
      checkTrue('[TF] screen All leaves the chart where it was', await pressedTf() === '5min');
      await syncBtn.click();
      await screenBtn('1min').click();
      await page.waitForTimeout(300);
      checkTrue('[TF] with sync off the chart keeps its own timeframe', await pressedTf() === '5min' && await syncBtn.getAttribute('aria-pressed') === 'false');
      await syncBtn.click();
      checkTrue('[TF] turning sync back on jumps to the screen timeframe', await eventually(async () => await pressedTf() === '1min'));
      await card.locator('[data-chart-tf="3min"]').click();
      checkTrue('[TF] picking a timeframe on the chart turns sync off', await eventually(async () => await syncBtn.getAttribute('aria-pressed') === 'false'));
      await syncBtn.click();
      await screenBtn('3min').click();
      await eventually(async () => await pressedTf() === '3min');
    }

    const depthBtn = page.locator('.chart-grid [data-chart-tool="depth"]').first();
    checkTrue('[new] below-grid Depth button present', await depthBtn.count() > 0);

    // DepthPanel renders "No depth data yet." until a live depth payload
    // arrives (no authenticated broker feed in this dev environment), so
    // these assert the panel container's presence, not real field values.
    const DEPTH_TEXT = 'text=/Best bid|No depth data yet/';

    // hidden -> right
    await depthBtn.click();
    await page.waitForTimeout(400);
    const rightVisible = await page.locator('.chart-grid').first().locator(DEPTH_TEXT).count();
    checkTrue('[new] depth cycle click 1 shows the depth panel (right)', rightVisible > 0);
    // Depth on the right + Indicators window open: one column, Depth on top, Indicators under it (2026-10-15)
    {
      const card = page.locator('.chart-grid').first();
      await setIndicatorsMode(card, 'right');
      const col = card.locator('[data-right-column]');
      const ind = await col.locator('[data-indicators-window]').boundingBox();
      const dep = await col.locator('[data-side-panel]').boundingBox();
      checkTrue('[UI] Depth and Indicators share one right-hand column, Depth on top',
        (await col.count()) === 1 && ind && dep && ind.y >= dep.y + dep.height - 1 && Math.abs(dep.x - ind.x) < 2,
        { ind, dep });
      checkTrue('[UI] ...and the column stays within the chart height',
        await col.evaluate((el) => { const r = el.getBoundingClientRect(); return [...el.children].every((c) => c.getBoundingClientRect().bottom <= r.bottom + 1) }));
      // Indicators tri-state: right -> below puts it in the row under the chart, Depth first
      await setIndicatorsMode(card, 'below');
      const chartBox = await card.locator('[data-right-column]').evaluate((el) => el.previousElementSibling.getBoundingClientRect().toJSON()).catch(() => null)
        ?? await card.locator('canvas').first().boundingBox();
      const below = await card.locator('[data-bottom-row] [data-indicators-window="below"]').boundingBox();
      checkTrue('[UI] Indicators "below" sits under the chart', below && chartBox && below.y >= chartBox.y + chartBox.height - 1, { below, chartBox });
      checkTrue('[UI] Indicators button shows the below badge', (await card.locator('[data-chart-tool="indicators"]').first().innerText()).includes('↓'));
      await setIndicatorsMode(card, 'hidden');
      checkTrue('[UI] third click hides the Indicators window', await card.locator('[data-indicators-window]').count() === 0);
    }

    // right -> below
    await depthBtn.click();
    await page.waitForTimeout(400);
    const belowStillVisible = await page.locator('.chart-grid').first().locator(DEPTH_TEXT).count();
    checkTrue('[new] depth cycle click 2 still shows the depth panel (below)', belowStillVisible > 0);
    {
      const card = page.locator('.chart-grid').first();
      await setIndicatorsMode(card, 'below');
      const row = card.locator('[data-bottom-row]');
      const dep = await row.locator('[data-side-panel]').boundingBox();
      const ind = await row.locator('[data-indicators-window="below"]').boundingBox();
      checkTrue('[UI] both below: one row under the chart, Depth first then Indicators', dep && ind && dep.x < ind.x, { dep, ind });
      await setIndicatorsMode(card, 'hidden');
    }

    // below -> hidden
    await depthBtn.click();
    await page.waitForTimeout(400);
    const hiddenAgain = await page.locator('.chart-grid').first().locator(DEPTH_TEXT).count();
    checkTrue('[new] depth cycle click 3 hides the depth panel again', hiddenAgain === 0);

    // ---- indicators: a strip above the graph, every chip with its value (2026-10-16) ----
    check('[SIG] indicator strip shows a value for all 13 indicators', await page.locator('.chart-grid [data-indicator-strip] [data-readout]').count(), 13);
    await setIndicatorsMode(page.locator('.chart-grid').first(), 'right');
    checkTrue('[SIG] the Signals tab holds no indicators (no readout rows in the window)', await page.locator('.chart-grid [data-indicators-window] [data-readout]').count() === 0);
    // ALGO-34: three tabs -- Indicators (as before), Patterns, Strategies
    const rail = page.locator('.chart-grid [data-indicator-tabs]').first();
    check('[ALGO-34] Indicators panel has 3 tabs', await rail.locator('[data-panel-tab]').allInnerTexts().then((t) => t.map((x) => x.replace(/\d+$/, '').trim())), ['Signals', 'Patterns', 'Strategies']);
    await rail.locator('[data-panel-tab="patterns"]').click();
    // the test stack can queue this request ~15-20 s behind its resetting socket.io polls
    await eventually(async () => await rail.locator('[data-pattern-option]').count() > 0, 30000);
    const patternKeys = await rail.locator('[data-pattern-option]').evaluateAll((els) => els.map((e) => e.dataset.patternOption));
    checkTrue('[ALGO-34] Patterns tab lists the system patterns by type', patternKeys.includes('macd_departure_bull') && patternKeys.includes('bb_squeeze_tight'), patternKeys.length);
    checkTrue('[ALGO-34] every enabled pattern is ticked by default',
      await rail.locator('[data-pattern-option] input').evaluateAll((els) => els.length > 0 && els.every((e) => e.checked)));
    await rail.locator('[data-pattern-option="macd_departure_bull"] input').check();
    await rail.locator('[data-panel-tab="strategies"]').click();
    checkTrue('[ALGO-34] Strategies tab shows the strategy list', await rail.locator('[data-strategy-picker], [data-panel="strategies"]').count() > 0);
    await rail.locator('[data-panel-tab="patterns"]').click();
    checkTrue('[ALGO-34] a ticked pattern stays ticked when switching tabs', await rail.locator('[data-pattern-option="macd_departure_bull"] input').isChecked());
    await rail.locator('[data-pattern-option="macd_departure_bull"] input').uncheck();
    await rail.locator('[data-panel-tab="signals"]').click();
    await setIndicatorsMode(page.locator('.chart-grid').first(), 'hidden');

    // ---- fullscreen ----
    const fullscreenBtn = page.locator('.chart-grid button[title="Fullscreen"]').first();
    await fullscreenBtn.click();
    await page.waitForTimeout(400);
    const fullscreenCanvasHeight = await page.locator('canvas').first().evaluate((el) => el.clientHeight).catch(() => 0);
    checkTrue('[new] fullscreen chart grows well past the normal small-card height', fullscreenCanvasHeight > 300, fullscreenCanvasHeight);
    const backBtn = page.locator('button', { hasText: '← Back' }).first();
    await backBtn.click();
    await page.waitForTimeout(400);
    checkTrue('[new] exiting fullscreen returns to the grid', await page.locator('h1', { hasText: /workspace/i }).count() > 0);

    // ---- open-in-tab + real new-tab navigation ----
    const openTabBtn = firstRow.locator('button[title*="Open"][title*="tab"]').first();
    await openTabBtn.click();
    await page.waitForTimeout(500);
    const tabButtons = await page.locator('button', { hasText: firstSymbol }).count();
    checkTrue('[new] open-tab icon creates a new tab for the symbol', tabButtons > 0);
    const detailHeading = await page.locator('h2').first().innerText().catch(() => '');
    check('[new] instrument detail tab shows the symbol as a heading', detailHeading, firstSymbol);

    const [newPage] = await Promise.all([
      context.waitForEvent('page'),
      page.locator('button[title="Open in a new browser tab"]').first().click(),
    ]);
    await newPage.waitForLoadState('load');
    await newPage.waitForTimeout(1500);
    check('[new] "open in new tab" navigates to a real /instrument/... URL', new URL(newPage.url()).pathname.startsWith('/instrument/'), true);
    const newPageHeading = await newPage.locator('h2').first().innerText().catch(() => '');
    check('[new] standalone instrument page shows the right symbol', newPageHeading, firstSymbol);
    const newPageCanvasWidth = await newPage.locator('canvas').first().evaluate((el) => el.clientWidth).catch(() => 0);
    checkTrue('[new] standalone instrument page renders a real chart', newPageCanvasWidth > 0);
    await newPage.close();

    // switch back to the grid tab
    await page.locator('button', { hasText: 'Market Watch' }).first().click();
    await page.waitForTimeout(300);
    checkTrue('[new] switching back to the Market Watch tab shows the grid again', await page.locator('table tbody tr').count() > 0);

    if (errors.length > 0) console.log('  (debug) [new] page errors:', JSON.stringify(errors, null, 2));
    checkTrue('[new] no page errors across the whole flow', errors.length === 0, errors);
    await page.close();
  }

  // ==================== ready reckoner on the condition maker (ALGO-34) ====================
  {
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`${BASE}/strategies`, { waitUntil: 'load' });
    const btn = page.locator('[data-reckoner-button]').first();
    await btn.waitFor({ timeout: 20000 }).catch(() => {});
    checkTrue('[ALGO-34] condition maker has a Ready reckoner button', await btn.count() > 0);
    if (await btn.count() > 0) {
      await btn.click();
      const panel = page.locator('[data-reckoner-panel]');
      await eventually(async () => await panel.locator('h2').count() > 0, 10000);
      checkTrue('[ALGO-34] the reckoner opens with the condition language reference',
        (await panel.locator('h2').first().innerText()).includes('Ready Reckoner') && await panel.locator('table').count() > 0);
      await page.keyboard.press('Escape');
      checkTrue('[ALGO-34] Escape closes the reckoner', await eventually(async () => await panel.count() === 0, 2000));
    }
    checkTrue('[ALGO-34] no page errors on the strategies page', errors.length === 0, errors);
    await page.close();
  }

  // ==================== hit review from the chart's counts (2026-10-16) ====================
  {
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`${BASE}/`, { waitUntil: 'load' });
    await page.waitForFunction(() => document.querySelectorAll('[data-instrument-grid] tbody tr').length > 1, null, { timeout: 30000 });
    if (!(await page.locator('.chart-grid .chart-card').count())) await page.locator('[data-instrument-grid] tbody tr').first().click();
    const card = page.locator('.chart-grid .chart-card').first();
    await card.locator('[data-chart-stack] canvas').first().waitFor({ timeout: 25000 });
    await page.waitForTimeout(2500);
    await setIndicatorsMode(card, 'right');
    const win = card.locator('[data-indicators-window]');
    const tfShown = await card.locator('[data-chart-tf][aria-pressed="true"]').first().getAttribute('data-chart-tf');
    const tryKind = async (tab, prefix) => {
      await win.locator(`[data-panel-tab="${tab}"]`).click();
      await eventually(async () => await win.locator(`[data-hit-count^="${prefix}:"]`).count() > 0, 15000);
      const counts = await win.locator(`[data-hit-count^="${prefix}:"]`).evaluateAll((els) => els.map((e) => ({ id: e.dataset.hitCount, n: Number(e.innerText), enabled: !e.disabled })));
      const pick = counts.find((c) => c.n > 0 && c.enabled);
      if (!pick) return { skipped: true, counts: counts.length };
      await win.locator(`[data-hit-count="${pick.id}"]`).click();
      const review = page.locator('[data-hit-review]');
      const opened = await eventually(async () => await review.count() === 1, 10000);
      const tabLabel = await page.locator('[data-tab-pane] button', { hasText: 'Review:' }).last().innerText().catch(() => '');
      // the count was this chart's timeframe; the review shows all three -- narrow it to compare
      await review.locator(`[data-review-tf="${tfShown}"]`).click();
      await page.waitForTimeout(300);
      const rows = await review.locator('[data-review-hit]').count();
      const total = Number(await review.locator('[data-review-total]').innerText());
      const hasNumbers = await review.locator('[data-review-hit] td[data-met]').count() > 0 || prefix === 'signal';
      return { opened, tabLabel, rows, total, n: pick.n, hasNumbers, id: pick.id };
    };
    const pat = await tryKind('patterns', 'pattern');
    if (pat.skipped) console.log('SKIP  [REVIEW] no pattern hits on this chart to click');
    else {
      checkTrue('[REVIEW] clicking a pattern count opens a "Review:" Workspace tab', pat.opened && /^Review: .+ · /.test(pat.tabLabel), pat);
      checkTrue('[REVIEW] ...showing that many hits on this timeframe, with each condition\'s numbers', pat.rows === pat.n && pat.total >= pat.n && pat.hasNumbers, pat);
    }
    // back to the grid tab, open a signal count
    await page.locator('[data-tab-pane] button', { hasText: 'Market Watch' }).first().click();
    await page.waitForTimeout(800);
    const card2 = page.locator('.chart-grid .chart-card').first();
    await card2.locator('[data-chart-stack] canvas').first().waitFor({ timeout: 25000 });
    await setIndicatorsMode(card2, 'right');
    const sig = await (async () => {
      const w2 = card2.locator('[data-indicators-window]');
      await w2.locator('[data-panel-tab="signals"]').click();
      await eventually(async () => await w2.locator('[data-hit-count^="signal:"]').count() > 0, 15000);
      const counts = await w2.locator('[data-hit-count^="signal:"]').evaluateAll((els) => els.map((e) => ({ id: e.dataset.hitCount, n: Number(e.innerText), enabled: !e.disabled })));
      const pick = counts.find((c) => c.n > 0 && c.enabled);
      if (!pick) return { skipped: true };
      await w2.locator(`[data-hit-count="${pick.id}"]`).click();
      const opened = await eventually(async () => await page.locator('[data-hit-review^="signal:"]').count() === 1, 10000);
      return { opened, id: pick.id };
    })();
    if (sig.skipped) console.log('SKIP  [REVIEW] no raw signals on this chart to click');
    else checkTrue('[REVIEW] clicking a signal count opens its review', sig.opened, sig);
    const reviewTabs = await page.locator('[data-tab-pane] button', { hasText: 'Review:' }).count();
    checkTrue('[REVIEW] review tabs sit in the Workspace tab bar', reviewTabs >= (pat.skipped ? 0 : 1) + (sig.skipped ? 0 : 1), reviewTabs);
    // close them again
    for (let i = 0; i < reviewTabs; i++) {
      await page.locator('[data-tab-pane] button', { hasText: 'Review:' }).first().locator('span[title^="Close"]').click();
      await page.waitForTimeout(200);
    }
    checkTrue('[REVIEW] no page errors', errors.length === 0, errors);
    await page.close();
  }

  // ==================== multi-day chart (step 4, 2026-10-08) ====================
  // Needs a stock with several stored sessions; CHART_SYMBOL picks it (the
  // test stack is seeded with BANKBARODA). Skipped if that row isn't there.
  {
    const symbol = process.env.CHART_SYMBOL || 'BANKBARODA';
    await resetPrefs();
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const candleCalls = [];
    page.on('request', (r) => { if (r.url().includes('/api/candles?')) candleCalls.push(r.url()); });
    await page.goto(`${BASE}/`, { waitUntil: 'load' });
    await page.waitForTimeout(4000);
    const row = page.locator('table tbody tr', { hasText: symbol }).first();
    if (await row.count() === 0) {
      console.log(`SKIP  [chart] no ${symbol} row on this stack`);
    } else {
      await row.click();
      await page.waitForTimeout(3500);
      checkTrue('[chart] opens with several sessions (days= requested)', candleCalls.some((u) => /[?&]days=\d+/.test(u)), candleCalls);
      const chart = page.locator('.chart-grid').first();
      // live answers the 20-session history a few seconds after the candles
      await eventually(async () => await chart.locator('[data-day-separators] div').count() >= 2, 20000);
      const dayLabels = await chart.locator('[data-day-separators] div').count();
      checkTrue('[chart] day-boundary lines drawn for earlier sessions', dayLabels >= 2, dayLabels);
      checkTrue('[chart] opening does not load older sessions by itself', !candleCalls.some((u) => u.includes('before=')), candleCalls.filter((u) => u.includes('before=')));

      // ---- ALGO-34: by default only pattern dots; ticking marks exactly what's ticked ----
      const layer34 = chart.locator('[data-diagrams]');
      const dots34 = async () => Number(await layer34.getAttribute('data-dot-count'));
      const tf1 = chart.locator('[data-chart-tf="1min"]');
      if (await tf1.count() > 0 && await tf1.getAttribute('aria-pressed') !== 'true') {
        await tf1.click();
        await page.waitForTimeout(2500);
      }
      const marks = await page.evaluate(async (sym) => {
        const c = await (await fetch(`/api/candles?symbol=${sym}&exchange_segment=NSE_EQ&timeframe=1min&days=21`)).json();
        const m = (await (await fetch(`/api/candles/markers?symbol=${sym}&exchange_segment=NSE_EQ&timeframe=1min&from=${c.candles[0].ts}`)).json()).markers;
        const byPattern = {};
        for (const r of m.filter((x) => x.activity_type === 'pattern')) (byPattern[r.activity] ??= new Set()).add(r.ts);
        const busiest = Object.entries(byPattern).sort((a, b) => b[1].size - a[1].size)[0]?.[0] ?? null;
        const without = new Set(m.filter((x) => x.activity_type === 'pattern' && x.activity !== busiest).map((x) => x.ts));
        const codes = new Set((await (await fetch('/api/strategy-elements')).json()).filter((e) => e.element_type === 'event').map((e) => e.code));
        return {
          all: new Set(m.filter((x) => x.activity_type === 'pattern' || codes.has(x.activity)).map((x) => x.ts)).size,
          patterns: new Set(m.filter((x) => x.activity_type === 'pattern').map((x) => x.ts)).size,
          busiest, withoutBusiest: without.size,
          directions: [...new Set(m.filter((x) => x.activity_type === 'pattern').map((x) => x.direction))],
        };
      }, symbol);
      await eventually(async () => await dots34() === marks.patterns, 25000);
      check('[ALGO-34] by default the chart shows only pattern dots (raw events off)', await dots34(), marks.patterns);
      const shapes = async () => JSON.parse((await layer34.getAttribute('data-mark-shapes')) || '{}');
      const s0 = await shapes();
      checkTrue('[MARK] patterns are squares (no circles while raw signals are off)', (marks.patterns === 0 || s0.square > 0) && !s0.circle && !s0.triangleUp && !s0.triangleDown, s0);
      const mw = Number(await layer34.getAttribute('data-mark-width'));
      const body = Number(await layer34.getAttribute('data-candle-body'));
      checkTrue('[MARK] a mark is as wide as the candle body, kept within 3-12 px', mw >= 3 && mw <= 12 && Math.abs(mw - Math.min(12, Math.max(3, body))) < 0.05, { mw, body });
      checkTrue('[ALGO-34] pattern hits carry a direction from their type', marks.directions.some((d) => d === 'bull' || d === 'bear'), marks.directions);
      await setIndicatorsMode(chart, 'right');
      const rail34 = chart.locator('[data-indicator-tabs]');
      await rail34.locator('[data-panel-tab="patterns"]').click();
      await eventually(async () => await rail34.locator('[data-pattern-option] input').count() > 0, 30000);
      if (marks.busiest) {
        await rail34.locator(`[data-pattern-option="${marks.busiest}"] input`).uncheck();
        let withoutNow = marks.withoutBusiest; // re-read: a new pattern hit can land mid-test on live
        await eventually(async () => {
          withoutNow = await page.evaluate(async ([sym, busiest]) => {
            const c = await (await fetch(`/api/candles?symbol=${sym}&exchange_segment=NSE_EQ&timeframe=1min&days=21`)).json();
            const m = (await (await fetch(`/api/candles/markers?symbol=${sym}&exchange_segment=NSE_EQ&timeframe=1min&from=${c.candles[0].ts}`)).json()).markers;
            return new Set(m.filter((x) => x.activity_type === 'pattern' && x.activity !== busiest).map((x) => x.ts)).size;
          }, [symbol, marks.busiest]);
          return await dots34() === withoutNow;
        }, 8000);
        check('[ALGO-34] unticking a pattern removes exactly its dots', await dots34(), withoutNow);
        await rail34.locator(`[data-pattern-option="${marks.busiest}"] input`).check();
      }
      await rail34.locator('[data-panel-tab="signals"]').click();
      // tick every raw signal: each group's "all" switch (Signals tab, 2026-10-16)
      await eventually(async () => await rail34.locator('[data-group-all^="signal-"]').count() > 0, 10000);
      for (const box of await rail34.locator('[data-group-all^="signal-"]').all()) await box.check();
      let allNow = marks.all; // a new candle can close mid-test on live: re-read
      await eventually(async () => {
        allNow = await page.evaluate(async (sym) => {
          const c = await (await fetch(`/api/candles?symbol=${sym}&exchange_segment=NSE_EQ&timeframe=1min&days=21`)).json();
          const m = (await (await fetch(`/api/candles/markers?symbol=${sym}&exchange_segment=NSE_EQ&timeframe=1min&from=${c.candles[0].ts}`)).json()).markers;
          const codes = new Set((await (await fetch('/api/strategy-elements')).json()).filter((e) => e.element_type === 'event').map((e) => e.code));
          return new Set(m.filter((x) => x.activity_type === 'pattern' || codes.has(x.activity)).map((x) => x.ts)).size;
        }, symbol);
        return await dots34() === allNow;
      }, 8000);
      check('[ALGO-34] ticking the raw events adds them (one dot per candle with anything ticked)', await dots34(), allNow);
      const s1 = await shapes();
      checkTrue('[MARK] raw signals are circles, alongside the pattern squares', s1.circle > 0 && (marks.patterns === 0 || s1.square > 0), s1);
      const savedToggles = await waitForSavedPrefs(page, (x) => (x['chart.selectedSignals']?.length ?? 0) > 20);
      checkTrue('[PREF] the chart\'s ticked signals and patterns are saved for the user',
        (savedToggles['chart.selectedSignals']?.length ?? 0) > 20 && Array.isArray(savedToggles['chart.selectedPatterns.v2']),
        { signals: savedToggles['chart.selectedSignals']?.length, patterns: savedToggles['chart.selectedPatterns.v2']?.length });
      await setIndicatorsMode(chart, 'hidden');

      // hover across the price pane until a marker's names show
      const canvas = chart.locator('canvas').first();
      const box = await canvas.boundingBox();
      let tipText = ''
      for (let x = box.x + box.width - 80; x > box.x + 20 && !tipText; x -= 3) {
        await page.mouse.move(x, box.y + box.height / 2);
        tipText = await chart.locator('[data-marker-tooltip]').evaluate((el) => (el.style.display === 'block' ? el.textContent : ''));
      }
      checkTrue('[chart] marker names show on hover (no labels drawn on the chart)', tipText.length > 0, tipText);

      // ---- ALGO-25: event dots, diagrams, icon toolbar ----
      const layer = chart.locator('[data-diagrams]');
      const dotCount = async () => Number(await layer.getAttribute('data-dot-count'));
      const diagramCount = async () => Number(await layer.getAttribute('data-diagram-count') ?? 0);
      // 1. exactly one dot per candle that has events (raw events ticked above, on 1min)
      // during market hours a new candle can close mid-test: read the expected
      // count fresh each time it's compared
      const allDots = () => page.evaluate(async (sym) => {
        const c = await (await fetch(`/api/candles?symbol=${sym}&exchange_segment=NSE_EQ&timeframe=1min&days=21`)).json();
        const m = (await (await fetch(`/api/candles/markers?symbol=${sym}&exchange_segment=NSE_EQ&timeframe=1min&from=${c.candles[0].ts}`)).json()).markers;
        const codes = new Set((await (await fetch('/api/strategy-elements')).json()).filter((e) => e.element_type === 'event').map((e) => e.code));
        return new Set(m.filter((x) => x.activity_type === 'pattern' || codes.has(x.activity)).map((x) => x.ts)).size;
      }, symbol);
      let expectedDots = marks.all;
      await eventually(async () => await dotCount() === (expectedDots = await allDots()), 25000);
      check('[ALGO-25] one dot per candle with events', await dotCount(), expectedDots);
      // 2. hover lists every event of that candle
      const tipRows = await chart.locator('[data-marker-tooltip] [data-event-row]').count();
      const tipHead = await chart.locator('[data-marker-tooltip] div').first().innerText();
      checkTrue('[ALGO-25] hover lists all of the candle\'s events', tipRows > 0 && tipHead.toLowerCase().startsWith(`${tipRows} event`), { tipRows, tipHead });
      // 3. click a dot with a drawable event: diagrams appear, then clear after ~5 s
      let drawn = 0;
      for (let x = box.x + box.width - 80; x > box.x + 20 && !drawn; x -= 4) {
        await page.mouse.move(x, box.y + box.height / 2);
        const kinds = await chart.locator('[data-marker-tooltip]').evaluate((el) => (el.style.display === 'block' ? el.textContent : ''));
        if (!/candle pattern|structure|graph formation|price action/.test(kinds)) continue;
        await page.mouse.click(x, box.y + box.height / 2);
        await page.waitForTimeout(200);
        drawn = await diagramCount();
      }
      const shapesSvg = await layer.innerHTML();
      checkTrue('[ALGO-25] clicking a dot draws its diagrams (circle/rectangle/outline)', drawn > 0 && /<(ellipse|rect|polyline)/.test(shapesSvg), { drawn });
      await page.waitForTimeout(5300);
      check('[ALGO-25] clicked diagrams clear after about 5 seconds', await diagramCount(), 0);
      // 4. Diagrams toggle draws all in view, off clears
      const tool = (name) => chart.locator(`[data-chart-tool="${name}"]`);
      await tool('diagrams').click(); await page.waitForTimeout(300);
      const allDrawn = await diagramCount();
      checkTrue('[ALGO-25] Diagrams toggle draws all diagrams in view', allDrawn > drawn, { allDrawn, drawn });
      await tool('diagrams').click(); await page.waitForTimeout(200);
      check('[ALGO-25] Diagrams toggle off clears them', await diagramCount(), 0);
      // 5. Event marking toggle hides and shows the dots
      await tool('events').click(); await page.waitForTimeout(200);
      check('[ALGO-25] Event marking off hides every dot', await dotCount(), 0);
      await tool('events').click(); await page.waitForTimeout(200);
      await eventually(async () => await dotCount() === (expectedDots = await allDots()), 10000);
      check('[ALGO-25] Event marking on shows the dots again', await dotCount(), expectedDots);
      // 6. icon toolbar: seven tools, each with a tooltip and an on/off state
      const tools = await chart.locator('[data-chart-toolbar] [data-chart-tool]').evaluateAll((els) => els.map((e) => ({
        tool: e.dataset.chartTool, title: e.getAttribute('title'), pressed: e.getAttribute('aria-pressed'), text: e.innerText.trim(),
      })));
      check('[ALGO-25] toolbar has Depth, Indicators, Events, Diagrams, RSI, MACD, Stoch, Graph only', tools.map((t) => t.tool), ['depth', 'indicators', 'events', 'diagrams', 'rsi', 'macd', 'stoch', 'graphonly']);
      checkTrue('[ALGO-25] every tool is an icon with a tooltip and an on/off state',
        tools.every((t) => t.title && (t.pressed === 'true' || t.pressed === 'false') && !/[a-z]{3,}/i.test(t.text)), tools);
      // ---- ALGO-26: wheel over the price axis zooms height, not width ----
      const view = async () => layer.evaluate((el) => { const d = el.measureView(); return { bars: Number(d.barsVisible), span: Number(d.priceSpan), zoom: Number(d.priceZoom) } });
      const paneBox = await layer.boundingBox(); // the whole price pane incl. its right-hand axis
      const axisX = paneBox.x + paneBox.width - 25; // inside the right price axis
      const midY = box.y + box.height / 2;
      const v0 = await view();
      await page.mouse.move(axisX, midY);
      for (let i = 0; i < 4; i++) { await page.mouse.wheel(0, -120); await page.waitForTimeout(80); }
      await page.waitForTimeout(250);
      const v1 = await view();
      checkTrue('[ALGO-26] wheel up over the price axis makes the graph taller (narrower price range)', v1.span < v0.span * 0.8 && v1.zoom < 1, { v0, v1 });
      // the price labels' width can shift the plot by a bar; a time zoom changes it by far more
      checkTrue('[ALGO-26] ...and leaves the horizontal zoom alone', Math.abs(v1.bars - v0.bars) / v0.bars < 0.02, { v0, v1 });
      for (let i = 0; i < 8; i++) { await page.mouse.wheel(0, 120); await page.waitForTimeout(80); }
      await page.waitForTimeout(250);
      const v2 = await view();
      checkTrue('[ALGO-26] wheel down over the price axis shrinks it again (wider range)', v2.span > v1.span * 1.3 && v2.zoom > 1, { v1, v2 });
      await page.mouse.dblclick(axisX, midY);
      await page.waitForTimeout(250);
      check('[ALGO-26] double-click on the price axis resets the height zoom', (await view()).zoom, 1);
      const v3 = await view();
      await page.mouse.move(box.x + box.width / 2, midY);
      // zoom OUT: zooming in can already sit at the candle-width limit (2026-10-16)
      for (let i = 0; i < 3; i++) { await page.mouse.wheel(0, 120); await page.waitForTimeout(80); }
      await page.waitForTimeout(250);
      const v4 = await view();
      checkTrue('[ALGO-26] wheel over the chart area still zooms width only', Math.abs(v4.bars - v3.bars) / v3.bars > 0.05 && v4.zoom === 1, { v3, v4 });

      const rsiPane = chart.locator('[data-pane]').first();
      await tool('rsi').click(); await page.waitForTimeout(150);
      checkTrue('[ALGO-25] RSI icon hides the RSI panel', await rsiPane.evaluate((el) => el.hidden) && await tool('rsi').getAttribute('aria-pressed') === 'false');
      await tool('rsi').click(); await page.waitForTimeout(150);
      checkTrue('[ALGO-25] RSI icon shows it again', !(await rsiPane.evaluate((el) => el.hidden)) && await tool('rsi').getAttribute('aria-pressed') === 'true');

      // scroll back: drag the chart right until older sessions are requested --
      // on 5min, so 21 sessions of 1-min bars (live) don't outlast the wheel turns
      await chart.locator('[data-chart-tf="5min"]').click();
      await page.waitForTimeout(3000);
      const before = candleCalls.length;
      // zoom out over the chart until the first bar reaches the left edge: 21
      // sessions are ~1,600 bars even on 5min, too many to wheel across, and
      // repeated horizontal wheels make Edge go Back (found on live 2026-10-15)
      // ...then scroll left: zooming out around the middle pads the RIGHT
      // with blank space and can stop short of the first bar (test stack,
      // 2026-10-15), so a few horizontal turns finish the trip to the edge
      for (let i = 0; i < 60 && !candleCalls.slice(before).some((u) => u.includes('before=')); i++) {
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        if (i < 40) await page.mouse.wheel(0, 400); else await page.mouse.wheel(-600, 0);
        await page.waitForTimeout(250);
      }
      // the test stack's socket.io polling keeps resetting and can queue this request ~15s behind hung long-polls
      await page.waitForResponse((r) => r.url().includes('before='), { timeout: 30000 }).catch(() => {});
      checkTrue('[chart] scrolling back loads older sessions (before= requested)', candleCalls.slice(before).some((u) => u.includes('before=')), candleCalls.slice(before));
      await page.waitForTimeout(3000);
      const dayLabelsAfter = await chart.locator('[data-day-separators] div').count();
      checkTrue('[chart] more day lines after loading older sessions', dayLabelsAfter > dayLabels, { dayLabels, dayLabelsAfter });
      await page.screenshot({ path: process.env.CHART_SHOT || 'chart_multiday.png', clip: await chart.boundingBox() }).catch(() => {});
    }
    checkTrue('[chart] no page errors', errors.length === 0, errors);
    await page.close();
  }

  console.log('\n==================== REAL APP UI TEST SUITE ====================');
  results.forEach((r) => console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}${r.pass ? '' : `  (got ${JSON.stringify(r.actual)}, expected ${JSON.stringify(r.expected)})`}`));
  const failed = results.filter((r) => !r.pass);
  console.log('-------------------------------------------------------------------');
  console.log(`${results.length - failed.length}/${results.length} passed`);
  console.log('===================================================================\n');

  await browser.close();
  await restorePrefs();
  process.exit(failed.length > 0 ? 1 : 0);
})().catch(async (e) => { console.error('SUITE CRASHED:', e); await restorePrefs(); process.exit(1); });
