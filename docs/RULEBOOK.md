# AlgoTrading Rule Book

The standing rules and finalized decisions for this project, written so that
any session (any model, any Claude account, any machine) works the same way.
`CLAUDE.md` covers architecture and setup; this file covers **how we work and
what has been decided**.

- **Precedence:** the user's message in the current session > this file >
  everything else. If this file and the code disagree, say so; don't guess.
- **Keep it current:** when a discussion is finalized, add or update the rule
  here in the same session, with the date. Remove rules the user withdraws.
  History and detail stay in commit messages and the docs; this file stays
  short.
- Dates are 2026 unless stated.

---

## 1. How to work (every task)

1. **Impact analysis before coding (10-05).** Before changing anything, write
   down which existing, working features touch the same code, data or screen,
   what could break and why it won't, and **share it with the user before
   implementing**. Existing functionality must never be silently broken.
2. **Ask before removing anything (10-05).** Never remove, hide, merge away or
   relocate existing content or a feature on your own judgment. Ask: keep,
   move or drop?
3. **Never delete a page (10-05).** Superseding a page means rename the old
   one (e.g. "Market Watch Old") and add the new one beside it.
4. **Research before implementing (09-14).** For anything with prior art
   (indicators, detectors, backtest conventions, scoring), check established
   practice first, and say what you found.
5. **One verification at a time (09-24).** When the user verifies a change,
   give one test with exact steps and the expected result, wait for
   pass/fail, then the next.
6. **Plan first when asked.** "Plan only / don't start until I say" means no
   code, no tickets, until the user explicitly says go.
7. **Don't claim "tested" without testing.** UI behavior counts as tested only
   after a real browser run (Playwright + system Edge, see section 5).
8. **Report honestly.** Failed tests, skipped steps and caveats are stated
   plainly. Backtest numbers come with their caveats (sample size, many
   variants tried, slippage).
9. **Commit/push only when asked.** Work in a worktree branch; merge to
   `master` when the user approves.

## 2. Environments and operations

- **Never touch the `Trading` project** (`~/Trading` on the VM): no copying,
  moving or editing. Read-only reads of its data over SSH are allowed
  (e.g. `~/Trading/holidays.txt`, the NSE holiday list). The one approved
  exception is the planned `LibSQLServerTokenMirror.py`.
- **Live vs test stack (10-06).** Live = the user's `run.py` on port 5000
  (VM SQL Server over the tunnel) + Vite on 5173. Test = backend 5055 with
  the seeded `local_dev_test.db` + Vite 5174
  (`D:\AI Trading\test-stack\launch_test_stack.py`). Test data never reaches
  the live stack. Browser tests on live are read-only.
- **Live restarts:** don't restart the live app during market hours without
  the user's OK. A restart loses nothing (15-minute saves + recovery), but
  saved hits keep the old rule after a rule change.
- **Migrations before restart:** any new DB column ships with an idempotent
  `backend/scripts/migrate_*.py`; run it on SQL Server **before** restarting
  the app on the new code.
- **VM:** auto-shuts down at 18:00 IST; a tunnel failure after that is
  expected. SSH: check state first, wait 30-60 s between retries, reuse the
  multiplexed connection (fail2ban risk). Scope heavy replays to 1-2 symbols
  during development.
- **`/mnt` on the VM is ephemeral.** Persistent data lives on `/data`.
- **Known flaky tests:** `backend/tests/feed/test_gap_fill.py` fails if run
  before 09:15 IST (wall-clock market-open check); exclude it to judge a
  change. `test_bootstrap.py`'s background-thread test can race.
- **Jira:** project `ALGO` at neerajprojects.atlassian.net. UI bugs and ideas
  go there as tickets while pattern work is the focus.

## 3. Product principles (startup, 09-24)

- Multi-tenant by default: every user-facing entity has an owner
  (`owner_id`, 1 until login exists). Platform data (candles, indicators) is
  shared; tenant data is owned.
- Plans, limits, prices and feature flags are **data, not code**; enforced
  server-side.
- Money and credits: append-only ledger, idempotent, reconcilable.
- API-first (mobile/desktop later): no logic only the web client knows.
- Long-running work is a queued job with progress and a notification.
- Correct before pretty: numbers shown to users must be trustworthy.
- Major features ship behind an on/off switch, released over time, and built
  standalone so one failing feature never breaks the baseline (10-05).
- Flag compliance questions early (SEBI advisory, data licensing, GST).
- **Recommendations before execution (09-17):** strengthen recommendations,
  wire Telegram, cut false alarms, and only then wire real order placement.
  Don't propose live order placement before that order is satisfied.

## 4. UI rules

- Professional design in both themes; tokens alone are not enough:
  deliberate spacing (layout `gap`), grouped elements, a colorful palette.
  Real app pages must match the quality of the reference artifacts.
- Detail panels use the "tubelight" background: a 2-color band in the top
  ~14% fading into a soft tint. Icons, buttons and badges stay flat.
- Every user-chosen UI state is saved per user via
  `frontend/src/preferences.js` (`/api/preferences`), restored on reload.
  Never bare localStorage for this (10-15).
- Every Workspace piece is a standalone component: data and callbacks via
  props, no page state (10-08).
- **Locked layout tests (10-08):** `frontend/ui_element_tests.cjs` (E1, E2...)
  changes only when the user asks. If a feature seems to need one changed,
  stop and ask exactly: **"Change in UI Element Position Test Case"**.
- One assertion-based Playwright suite per surface (`frontend/ui_test_suite.cjs`),
  run in full before every UI publish; add a regression case for every
  reported bug.
- Know which surface you are on: a Claude artifact preview is not the real
  app (`frontend/`). Confirm before testing or reporting bugs.
- Charts: `lightweight-charts` from jsdelivr (cdnjs 404s).

## 5. Data rules

- Historical fetch: always the full set, 1min + 3min + 5min + 1day. 3min and
  5min are rolled up from 1min (never fetched directly). Set 3min coverage
  after the rollup. Dhan intraday history is ~90 days deep.
- Live and backfilled candles both go through the ActivityEngine; nothing
  bypasses it. A stock added mid-session is computed from 09:15.
- Use real NSE holidays (`~/Trading/holidays.txt`) to judge candle gaps.
- Entry price for structure/graph formations is the **current** candle's
  close, never the swing candle's (stale-entry bug, 09-14;
  `prediction_tracker.py` still has it).
- New page origins must be in `CORS_ORIGINS`, or sockets loop on HTTP 400.

## 6. Patterns and signals (finalized)

- **Layering (10-14):** Walkthrough (raw facts/events) -> Pattern
  (conditions + weightage) -> Strategy (patterns/conditions + action + risk).
  One condition language for all (`condition_evaluator.py`, reference:
  `docs/condition_ready_reckoner.md`).
- Raw events are stored and reported but **not** marked on the chart; patterns
  are marked with one dot where all conditions first become true.
- A pattern is **formed at >= 80% of its conditions**; score = weight x match %
  (0 if not formed); the top score sets the stock's colour. To make a
  pattern need *all* conditions, give it at most 4 conditions.
- Colours: dark green 6, light green 5, orange 4, yellow 3, white 2, pink 1
  (caution), red 0 (avoid). Red beats pink; either beats every positive
  colour. Colour is strength, not direction (direction = Buy/Sell, ▲/▼).
- System patterns ship in `patterns_catalog.py` and are read-only; users edit
  a copy. Per-stock weightage exists but is off by default. All settings are
  user-specific.
- Signal strength (ALGO-6): the engine records every formation with its
  measured strength; only qualified ones count in `EVENT()`. `EVENT_ANY()`
  ignores strength.
- Don't change formation rules ad hoc; collect input and settle each one in
  the formation discussion pass.
- **Current focus (10-16):** pattern quality and excellent recommendations;
  UI work only via Jira tickets.

## 7. Trading research decisions (10-27 to 10-30)

From the 2-year, 13-stock, 3/5-min intraday backtests (scripts and data in
`D:\AI Trading\research\cross_widening_2026-10-27`):
- **Never trade against the trend:** counter-EMA50-trend signals lose for
  almost every pattern.
- **Best setup:** 5-min MACD cross-widening (lines keep widening 3 candles
  after the cross), price on the trend side of a sloping EMA50, ADX 30-40
  with DI agreeing, entries 09:30-14:45, stop 2xATR / target 10xATR / else
  day-end exit. Built as system patterns `macd_widening_trend_bull` /
  `_bear`; `ma_death_cross_trend` is the weaker second.
- Tight exits (6 candles, MACD flip) lose; profit comes from holding to the
  day end. Keep the 09:30-10:00 window (volatile, but most of the profit).
  Daily-chart trend filter adds nothing. ADX >= 40 adds losses.
- Classic candlestick patterns (hammer, engulfing, tweezer, double/triple
  top/bottom, stars) show no edge on 3/5-min, even trend-filtered. VWAP
  rejections ~break-even after slippage.
- Costs: use `itemized_round_trip_cost` (`backend/scripts/order_backtest.py`):
  ~0.106% per trade up to ~Rs 60k, 0.082% at Rs 1L, 0.059% at Rs 2L; add
  slippage separately (0.05% assumed).
- **Plan for Rs 5L (no leverage):** 7 stocks (ASHOKLEY, BHEL, CUB, FINCABLES,
  HINDCOPPER, NATIONALUM, RELIANCE), Rs 2.5L per trade. Backtest ~Rs 1.27-1.69L
  a year after slippage; realistic expectation ~Rs 1-1.5L. **Paper trade
  first**, then live at small size.
- Backtest results are stored in `pattern_backtest_results`.

## 8. Open questions (not decided; ask before assuming)

- Timeframe alignment for signals, colour decks and grid (options A-D, C
  recommended), 2026-10-08.
- Partial pattern match display; colour aggregation details.
- Engulfing needs trend + support/resistance + volume context; sideways_chop
  BB width should be relative, not a fixed 0.4%.
- EOD backtest overruns the 18:00 VM shutdown.
- Strategy accounts/ledger (spec only); Telegram delivery; paper trading.

## 9. Where things live

- Architecture and setup: `CLAUDE.md`. Design docs: `backend/*HLD*.md`,
  `backend/Phase1_LLD.md`. Condition language: `docs/condition_ready_reckoner.md`.
- Session memory (detailed history, this PC only):
  `C:\Users\Lenovo\.claude\projects\d--AI-Trading-AlgoTrading\memory\`.
  Anything that must survive a change of machine or account belongs in
  **this file**, not only there.
- Research scripts and data: `D:\AI Trading\research\`.
- Test stack launcher and logs: `D:\AI Trading\test-stack\`.
