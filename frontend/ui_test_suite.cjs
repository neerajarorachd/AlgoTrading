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
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}`);
}

(async () => {
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

    await page.goto(`${BASE}/`, { waitUntil: 'load' });
    await page.waitForTimeout(4000);
    checkTrue('[new] no page errors on load', errors.length === 0, errors);

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

    // ---- Actions column, as finalized in the preview (ported 2026-10-06) ----
    await page.locator('button', { hasText: 'Show all' }).click().catch(() => {});
    await page.waitForTimeout(200);
    const actionCheck = await page.evaluate(async () => {
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
      for (const tr of document.querySelectorAll('table tbody tr')) {
        const symbol = tr.cells[0].innerText.split('\n')[0];
        const shown = tr.querySelector('[data-row-action]')?.dataset.rowAction ?? null;
        if (shown !== expected(bucketBySymbol[symbol], symbol)) mismatches.push({ symbol, bucket: bucketBySymbol[symbol], shown });
      }
      return mismatches;
    });
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
    await tfBar.locator('[data-timeframe="all"]').click();
    checkTrue('[TF] on All every colour tile shows three counts (1m · 3m · 5m)', await eventually(async () => {
      const texts = await page.$$eval('[data-bucket]', (els) => els.map((e) => e.innerText.trim()));
      return texts.length === 7 && texts.every((t) => /^\d+ · \d+ · \d+$/.test(t));
    }));
    checkTrue('[TF] All asks the API for timeframe=all', scoreCalls.some((u) => u.includes('timeframe=all')), scoreCalls);
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
    const tallestActions = await page.$$eval('table tbody tr', (rows) => Math.max(...rows.map((r) => r.cells[r.cells.length - 1].getBoundingClientRect().height)));
    checkTrue('[new] Actions cells stay on one line (no wrapping)', tallestActions < 48, tallestActions);
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

    // right -> below
    await depthBtn.click();
    await page.waitForTimeout(400);
    const belowStillVisible = await page.locator('.chart-grid').first().locator(DEPTH_TEXT).count();
    checkTrue('[new] depth cycle click 2 still shows the depth panel (below)', belowStillVisible > 0);

    // below -> hidden
    await depthBtn.click();
    await page.waitForTimeout(400);
    const hiddenAgain = await page.locator('.chart-grid').first().locator(DEPTH_TEXT).count();
    checkTrue('[new] depth cycle click 3 hides the depth panel again', hiddenAgain === 0);

    // ---- chart Indicators picker: every row has a readout column ----
    await page.locator('.chart-grid [data-chart-tool="indicators"]').first().click();
    await page.waitForTimeout(400);
    check('[new] Indicators picker shows a readout for all 15 rows', await page.locator('.chart-grid [data-readout]').count(), 15);
    // ALGO-34: three tabs -- Indicators (as before), Patterns, Strategies
    const rail = page.locator('.chart-grid [data-indicator-tabs]').first();
    check('[ALGO-34] Indicators panel has 3 tabs', await rail.locator('[data-panel-tab]').allInnerTexts().then((t) => t.map((x) => x.replace(/\d+$/, '').trim())), ['Indicators', 'Patterns', 'Strategies']);
    await rail.locator('[data-panel-tab="patterns"]').click();
    // the test stack can queue this request ~15-20 s behind its resetting socket.io polls
    await eventually(async () => await rail.locator('[data-pattern-option]').count() > 0, 30000);
    const patternKeys = await rail.locator('[data-pattern-option]').evaluateAll((els) => els.map((e) => e.dataset.patternOption));
    checkTrue('[ALGO-34] Patterns tab lists the system patterns by type', patternKeys.includes('macd_departure_bull') && patternKeys.includes('bb_squeeze_tight'), patternKeys.length);
    await rail.locator('[data-pattern-option="macd_departure_bull"] input').check();
    await rail.locator('[data-panel-tab="strategies"]').click();
    checkTrue('[ALGO-34] Strategies tab shows the strategy list', await rail.locator('[data-strategy-picker], [data-panel="strategies"]').count() > 0);
    await rail.locator('[data-panel-tab="patterns"]').click();
    checkTrue('[ALGO-34] a ticked pattern stays ticked when switching tabs', await rail.locator('[data-pattern-option="macd_departure_bull"] input').isChecked());
    await rail.locator('[data-pattern-option="macd_departure_bull"] input').uncheck();
    await rail.locator('[data-panel-tab="indicators"]').click();
    await page.locator('.chart-grid [data-chart-tool="indicators"]').first().click();
    await page.waitForTimeout(200);

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

  // ==================== multi-day chart (step 4, 2026-10-08) ====================
  // Needs a stock with several stored sessions; CHART_SYMBOL picks it (the
  // test stack is seeded with BANKBARODA). Skipped if that row isn't there.
  {
    const symbol = process.env.CHART_SYMBOL || 'BANKBARODA';
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
      // 1. exactly one dot per candle that has events (all event types) -- on
      // 1min: a synced chart opens on the screen's timeframe (3m by default)
      const tf1 = chart.locator('[data-chart-tf="1min"]');
      if (await tf1.count() > 0 && await tf1.getAttribute('aria-pressed') !== 'true') {
        await tf1.click();
        await page.waitForTimeout(2500);
      }
      const expectedDots = await page.evaluate(async (sym) => {
        const c = await (await fetch(`/api/candles?symbol=${sym}&exchange_segment=NSE_EQ&timeframe=1min&days=21`)).json();
        const m = await (await fetch(`/api/candles/markers?symbol=${sym}&exchange_segment=NSE_EQ&timeframe=1min&from=${c.candles[0].ts}`)).json();
        // pattern hits (ALGO-32) aren't drawn as raw dots yet -- ALGO-34 (5.5) adds them
        return new Set(m.markers.filter((r) => r.activity_type !== 'pattern').map((r) => r.ts)).size;
      }, symbol);
      await eventually(async () => await dotCount() === expectedDots, 25000);
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
      check('[ALGO-25] Event marking on shows the dots again', await dotCount(), expectedDots);
      // 6. icon toolbar: seven tools, each with a tooltip and an on/off state
      const tools = await chart.locator('[data-chart-toolbar] [data-chart-tool]').evaluateAll((els) => els.map((e) => ({
        tool: e.dataset.chartTool, title: e.getAttribute('title'), pressed: e.getAttribute('aria-pressed'), text: e.innerText.trim(),
      })));
      check('[ALGO-25] toolbar has Depth, Indicators, Events, Diagrams, RSI, MACD, Stoch', tools.map((t) => t.tool), ['depth', 'indicators', 'events', 'diagrams', 'rsi', 'macd', 'stoch']);
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
      for (let i = 0; i < 3; i++) { await page.mouse.wheel(0, -120); await page.waitForTimeout(80); }
      await page.waitForTimeout(250);
      const v4 = await view();
      checkTrue('[ALGO-26] wheel over the chart area still zooms width only', Math.abs(v4.bars - v3.bars) / v3.bars > 0.05 && v4.zoom === 1, { v3, v4 });

      const rsiPane = chart.locator('[data-pane]').first();
      await tool('rsi').click(); await page.waitForTimeout(150);
      checkTrue('[ALGO-25] RSI icon hides the RSI panel', await rsiPane.evaluate((el) => el.hidden) && await tool('rsi').getAttribute('aria-pressed') === 'false');
      await tool('rsi').click(); await page.waitForTimeout(150);
      checkTrue('[ALGO-25] RSI icon shows it again', !(await rsiPane.evaluate((el) => el.hidden)) && await tool('rsi').getAttribute('aria-pressed') === 'true');

      // scroll back: drag the chart right until older sessions are requested
      const before = candleCalls.length;
      for (let i = 0; i < 30 && !candleCalls.slice(before).some((u) => u.includes('before=')); i++) {
        await page.mouse.move(box.x + 60, box.y + box.height / 2);
        await page.mouse.wheel(-1500, 0); // horizontal wheel (headless drag only scrolls once)
        await page.waitForTimeout(400);
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
  process.exit(failed.length > 0 ? 1 : 0);
})().catch((e) => { console.error('SUITE CRASHED:', e); process.exit(1); });
