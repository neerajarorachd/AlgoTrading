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
    const depthBtn = page.locator('.chart-grid button', { hasText: 'Depth' }).first();
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
    check('[new] grid tab heading', h1, 'Market Watch');

    const tabBarText = await page.locator('button', { hasText: 'Market Watch' }).first().innerText();
    checkTrue('[new] pinned grid tab present', tabBarText.includes('Market Watch'));

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
    const eyebrow = page.locator('text=Live snapshot · Market Watch');
    checkTrue('[new] header eyebrow present', await eyebrow.count() > 0);
    const paramsText = await page.locator('text=/instruments registered/').first().innerText().catch(() => '');
    const totalRows = await page.evaluate(() => fetch('/api/symbols').then((r) => r.json()).then((rows) => rows.length));
    checkTrue('[new] header shows the real registered-instrument count', paramsText.includes(String(totalRows)), { paramsText, totalRows });
    const headerToggle = page.locator('button[title="Collapse header"]');
    await headerToggle.click();
    await page.waitForTimeout(150);
    checkTrue('[new] header collapse hides eyebrow + params', await eyebrow.count() === 0 && await page.locator('text=/instruments registered/').count() === 0);
    check('[new] header collapse keeps the title', await page.locator('h1').innerText(), 'Market Watch');
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
    check('[new] all 6 color tiles render', await tiles.count(), 6);

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
      await page.locator('table tbody tr').count() === 0 && await tiles.count() === 0 && await footer.count() === 0);
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
      const bucketBySymbol = Object.fromEntries(symbols.map((s) => [s.symbol, scores.scores.find((x) => x.instrument_id === s.id)?.bucket]));
      const expected = (b) => ({ strong_bull: 'buy', strong_bear: 'sell', mild_bull: 'hold', mild_bear: 'hold' }[b] ?? null);
      const mismatches = [];
      for (const tr of document.querySelectorAll('table tbody tr')) {
        const symbol = tr.cells[0].innerText.split('\n')[0];
        const shown = tr.querySelector('[data-row-action]')?.dataset.rowAction ?? null;
        if (shown !== expected(bucketBySymbol[symbol])) mismatches.push({ symbol, bucket: bucketBySymbol[symbol], shown });
      }
      return mismatches;
    });
    checkTrue('[new] every row\'s action follows the bucket rule (strong=Buy/Sell, mild=Hold, else none)', actionCheck.length === 0, actionCheck);
    checkTrue('[new] no "Remove" text left; every row has a × remove button',
      await page.locator('table tbody button', { hasText: /^Remove$/ }).count() === 0
      && await page.locator('table tbody button[aria-label^="Remove "]').count() === await page.locator('table tbody tr').count());
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

    const depthBtn = page.locator('.chart-grid button', { hasText: 'Depth' }).first();
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
    await page.locator('.chart-grid button', { hasText: 'Indicators' }).first().click();
    await page.waitForTimeout(400);
    check('[new] Indicators picker shows a readout for all 15 rows', await page.locator('.chart-grid [data-readout]').count(), 15);
    await page.locator('.chart-grid button', { hasText: 'Indicators' }).first().click();
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
    checkTrue('[new] exiting fullscreen returns to the grid', await page.locator('h1', { hasText: 'Market Watch' }).count() > 0);

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

  console.log('\n==================== REAL APP UI TEST SUITE ====================');
  results.forEach((r) => console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}${r.pass ? '' : `  (got ${JSON.stringify(r.actual)}, expected ${JSON.stringify(r.expected)})`}`));
  const failed = results.filter((r) => !r.pass);
  console.log('-------------------------------------------------------------------');
  console.log(`${results.length - failed.length}/${results.length} passed`);
  console.log('===================================================================\n');

  await browser.close();
  process.exit(failed.length > 0 ? 1 : 0);
})().catch((e) => { console.error('SUITE CRASHED:', e); process.exit(1); });
