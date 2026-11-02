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
8. **Test breadth before deciding (10-30).** No trading rule, threshold or
   signal is decided until it is tested on **15-30 stocks over 2 years**
   (learn on one year, check on the other). Results on fewer stocks are
   preliminary and must be labelled so.
9. **Report honestly.** Failed tests, skipped steps and caveats are stated
   plainly. Backtest numbers come with their caveats (sample size, many
   variants tried, slippage).
10. **Commit/push only when asked.** Work in a worktree branch; merge to
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
  the live stack. Browser tests on live are read-only. `--replay --as-today`
  replays the test candles dated today (needed for the grid signal filter
  tests; chart marks bunch up in that mode).
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
- **Grid signal filter (ALGO-37, 11-02):** one signal / pattern / strategy at
  a time (several = ALGO-38); while on, the grid colours show All (1m/3m/5m)
  and the timeframe bar is locked. Panel numbers = stocks having the item.
  "Last 10 candles / Whole day" applies to the grid and, separately, the chart.

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
- **Daily candle upkeep (10-31):** the VM keeps the 150-stock research universe
  (`backend/data/daily_universe.json`) current after each close. 16:30 IST
  (Mon-Fri): daily candles into their own table `candles_daily`
  (`scripts/update_daily_candles.py`). 16:45: 5-min for all 150, fetched
  directly, into `candles_historical` (`scripts/update_intraday_candles.py`).
  Stocks that already have 1-min data (the 13 samples) also get 1-min, with
  3/5-min rolled up from it. Each timeframe is gap-filled from its own last
  candle. Other stocks get the 1/3-min set when added to a watchlist.
  Systemd user timers on the VM (`deploy/`). Research tests use 5-min first,
  daily later. PC copy of the 5-min files:
  `D:\AI Trading\research\universe_2026-10-31\5min`.
- **Dhan quirks:** `toDate` is exclusive; a range with no trading day returns
  error DH-907 (treat as empty); a direct 3-min request returns only today
  (roll up from 1-min instead).

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

## 7. Trading research decisions (10-27 to 10-31)

**150-stock test (10-31): none of the findings below holds.** On 149 stocks x
2 years (5-min, day end 15:10), every setup earns ~0.00-0.07% a trade before
costs against ~0.16% costs + slippage, and loses in both years; stocks that
look good per stock are as many as chance gives. The 13-stock results were
stock selection. Details: `D:\AI Trading\research\universe_2026-10-31\RESULTS.md`
and the results page https://claude.ai/artifact/CDGfQ4qU7kCmdQ6TraTqvH.
- The best setup and the Rs 5L plan below are **not a money plan**; nothing
  is in the setup library as proven. Setups are judged on paper trading.
- Still true: never trade against the trend (no counter-trend test passed
  either); U-turn exits are neutral (alerts only); line tests and the story
  lookup have no standalone edge (the lookup ranks moments that will move,
  not the direction).
- Research standard: day end 15:10 (Dhan has no 15:15-15:25 candles for most
  stocks from 2026-08-03); loader `ucommon.py` in that folder.

The 13-stock findings (10-27 to 10-30), kept for the record:

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

## 7a. Market story (planned, 10-30; not built)

- Walkthrough also logs **state runs** (a "story"), one row per run, not per
  candle: e.g. `[3min, bb_width, squeezing, from constant, 10 candles]`.
- Two tables: **today** (intraday) and **archive** (moved at EOD).
- Every stock in any watchlist gets stories, on 1/3/5-min.
- "Constant" uses an anchored band: BB width settles at < 10% change over 5
  candles, then stays constant within +-10% of the settled width (1.5% ->
  1.35-1.65%); price-line gaps the same with +-15%, never narrower than
  +-0.1 point. A break of the band is movement.
- Line touch/rejection/hugging thresholds are percentages (user setting).
- P0 findings (research/market_story_2026-10-30/RESULTS.md): MACD *state*
  alone predicts little (lock costs nothing, kept); swing labels alone have
  no edge on 5-min, so keep fixed-5 confirmation and use labels as context
  only; prediction lookup needs >= 300 cases + shrink. Plan:
  `docs/market_story_plan.md` (branch `story`).
- Line tests are episodes, like BB expansion: the first touch after an
  approach starts a test, closes inside the zone keep it open, a close out
  ends it as a bounce or a break. Zone ~0.05-0.10% of price, sized per stock
  from its ATR (0.3 x ATR default; 0.45 x ATR under review). 149 stocks
  (10-31): neither zone predicts direction; 0.45 gives fewer tests and more
  bounces that hold (41% vs 37%).
- The story also watches open trades (user's purpose: exit on a U-turn).
  Preliminary (13 stocks): no story-based exit beat stop / target / day end
  in both years, so story turns are **alerts**, not automatic exits.
  Confirmed on 149 stocks (10-31): every U-turn exit within +-0.006% a trade.
- Capture "calm then move" (BB width and VWAP gap both constant, then both
  expanding in one direction) as a story event; preliminary: rare (~1 per
  stock per 14 days), no edge yet.
- Each line gets separate rows for its own slope, price vs line, and the
  price-line gap (expanding/narrowing); VWAP first. Volume is linked in.
- Each open row carries a guess of the next state (learned from past
  stories), checked when the next state arrives.
- Proven offline on research data first; ships behind a flag.

## 7b. Direction: a library of narrow setups (10-30)

- We are **not** looking for one golden setup that works everywhere. The goal
  is **15-20 different setups**, each with tight, high-confidence conditions,
  even if one fires only ~30 times a year.
- Example of a candidate: LL -> HH -> HL -> HH formed while VWAP / RSI / DI
  still lean the old way (early reversal; preliminary 56-65% vs 50%).
- The early-reversal example did not hold on 149 stocks (49.8% / 51.1% vs
  base 50.0% / 51.7%, 10-31).
- Order of work: (1) one last research test on the 150-stock set: **done
  10-31, nothing passed; trend research is stopped**; (2) build the
  system: setup development, story recording, recommendations, paper trading;
  (3) only once paper trading runs, **calibrate** each setup on live and
  paper results.

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
