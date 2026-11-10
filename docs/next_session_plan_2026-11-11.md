# Next session plan (written 2026-11-10)

Two tracks: **A. daily candles for long-term investing** (steps B-D; step A
is done) and **B. more intraday tests**. Rulebook rules apply: one check at a
time for the user (1.5), results on fewer than 15-30 stocks x 2 years are
preliminary (1.8), impact analysis before any change to working code (1.1).

## Where things stand (end of 2026-11-10, branch `paper-trading`, not merged)

- Batch pattern evaluation (`f4f73a9`), backtests skip the "why it formed"
  notes (`b25a48d`) and process only the timeframes their patterns can form on
  (`deabb4d`). Identical hits to candle by candle (checked on HINDCOPPER).
  Live gets this only after a merge to `master` + restart (user's OK needed;
  `migrate_paper_trading.py` must run on SQL Server before that restart).
- Daily candle updater: only newer days, one call, start/end moved off
  weekends/holidays, split/bonus re-fetch, new stock = 400 trading days
  (`535930d`, `6b3d367`). **Not yet copied to the VM** (its 16:30 timer runs
  the old copy; copy `backend/data/market_holidays.txt` with it).
  Check the VM job log: 2026-11-09's daily candle was not stored.
- 15 years of daily candles for the 150-stock universe:
  `D:\AI Trading\research\daily_15y_2026-11-10\daily\*.pkl`
  (rows `(epoch_utc, open, high, low, close, volume)`, `summary.json`;
  115 stocks full 3,718 days from 2011-11-11, 35 listed later).
- **Daily mode, step A (done 2026-11-10):** `ActivityEngine(..., daily=True)`
  and `PatternEvaluator(..., daily=True)`: no reset per day, cooldown in
  candles, window = last N candles (not today + previous session), 400 rows
  kept, no VWAP / line tests / day levels / story. New fields EMA200, MA200,
  RSI_WEEKLY (week so far counts with today's close), HIGH_52W / LOW_52W
  (250 candles), TF_MINUTES = 1440 on daily. Tests: `test_daily_mode.py`.
- HINDCOPPER `ll_hh_hl_strong` 3-min, 2 years: all three stop/target runs
  lose; price ran +0.6% our way before the first opposite swing but exits kept
  ~0% -- entry comes too late (preliminary, one stock).

## Housekeeping waiting on the user (ask at the start, one at a time)

1. **Merge `paper-trading` -> `master` and restart live?** Order: run
   `backend/scripts/migrate_paper_trading.py` on SQL Server first, then restart
   (not during market hours without OK). Brings batch pattern evaluation
   (restart ~45 s -> ~1-2 s per stock) and paper trading to live. Nothing on the
   branch is pushed.
2. **Copy the new daily updater to the VM** (`backend/scripts/update_daily_candles.py`,
   `backend/db/ops/LibCandlesDaily.py`, `backend/market_calendar.py`,
   `backend/data/market_holidays.txt`); dry-run there once
   (`--symbols RELIANCE --dry-run`).
3. **Check the VM daily-candle job log** (`journalctl --user -u algotrading-daily-candles`):
   2026-11-09's daily candle was not stored by the 16:30 run (last stored 11-08).
4. The SSH tunnel opened on 2026-11-10 was a background process of that
   session; reopen with `ssh -L 1433:127.0.0.1:1433 trading-server -N` when DB
   access is needed (one attempt, wait 30-60 s between retries).

## Track A -- daily candles (long-term)

### Step B: one stock end to end (user checks against a chart)
1. Loader: research pkl -> `Candle(timeframe="1day")` (ts = IST midnight, as Dhan stamps it).
2. RELIANCE 15 years through `ActivityEngine(daily=True)` + `PatternEvaluator(daily=True, explain=False)`, batch per year.
3. Before anything else: check a stock that had a **split** (not only a bonus) for back-adjustment, e.g. compare closes around the split date.
4. Show the user 3-4 dates: close, RSI14, MA50, MA200, EMA200, weekly RSI, 52-week high, swings / LL-HH-HL events. User compares with TradingView. **Wait for pass/fail.**

### Step C: study runner over 150 stocks
- `backend/scripts/daily_pattern_study.py`: one process per stock (parallel), pattern-only (no trades).
- Fetch NIFTY 50 index daily first (one call; security id 13, segment IDX_I -- confirm in Dhan's scrip master) as the benchmark.
- Daily patterns (code list `DAILY_PATTERNS` for now; DB mode column later):
  1. LL-HH-HL + `RSI > 48` + `RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL), 10)` (user's example)
  2. Golden cross: `CROSSED_UP(MA50, MA200)`
  3. Death cross: `CROSSED_DOWN(MA50, MA200)` (warning side, for comparison)
  4. New 52-week high: `CLOSE > HIGH_52W[-1]`
  5. Trend filter: `CROSSED_UP(CLOSE, EMA200)`
  6. Pullback in uptrend: `CLOSE > EMA200`, `RECENTLY(RSI < 40, 5)`, `RSI > RSI[-1]`
  7. Squeeze breakout: `BB_WIDTH <= LOWEST(BB_WIDTH, 126)` recently, then `CLOSE > BB_UPPER`
  8. Weekly + daily momentum: `RSI_WEEKLY > 50`, `CROSSED_UP(MACD_LINE, MACD_SIGNAL)`, `MACD_LINE > 0`
  Cooldown e.g. 20 candles so one long trend is not counted many times.
- Per hit: entry = that day's close; % move after 5 / 21 / 63 / 126 / 252 trading days; best and worst point inside each window; same move of NIFTY 50.

### Step D: results page (artifact)
- Per pattern: hits, hits per distinct month, % up and average move at each horizon, against the **base rate** (same stocks, every day) and against NIFTY 50.
- Learn 2011-2018 vs check 2019-2026; year by year.
- Caveats on the page: survivorship (the 150 are today's leaders), overlapping windows, many patterns tried.
- A pattern "passes" only if it beats the base rate in both halves.

### Later (not next session)
Daily patterns in the DB (mode column + migration, UI), daily paper trading
(multi-day holds, stop/target, max days held), nightly after-close scan.

## Track B -- more intraday tests

Goal: move from one stock (HINDCOPPER) to 15-30 stocks x 2 years, so results
stop being preliminary.

### Step 1: data
- 1-min candles exist only for the 13 sample stocks (SQL Server
  `candles_historical`); the 150 universe has 5-min research files only
  (`universe_2026-10-31\5min`). 3-min patterns need 1-min data.
- Fetch 2 years of 1-min for ~30 liquid universe stocks into a **research
  SQLite file** (not the live DB): Dhan intraday = 90 days a call -> ~9 calls a
  stock, ~270 calls, ~15 min at the broker's 3 s pacing; ~5-6 M rows,
  ~0.5 GB. Use the REST (FIXED3) token from SQL Server; SSH tunnel needed.
- Day end 15:10 as in the research standard (Dhan lacks 15:15+ for most stocks since 2026-08-03).

### Step 2: tests (each through the app's own paper backtest runner)
- **T1** `ll_hh_hl_strong`, 3-min only, 30 stocks x 2 years, stop/target 0.5/1, 0.75/1.5, 1/2, Rs 1L; learn year 1 / check year 2.
- **T2** earlier entry: the structure event is confirmed `swing_lookback` (5) candles after the swing, so entries come ~15 min late. Variants with `swing_lookback` 2 and 3.
  Note: `ActivityEngine(swing_lookback=...)` currently switches market-story
  events OFF (settings = {}), and those events include `bullish_structure_shift`
  -- fix that first (impact analysis), or T2 sees no hits.
- **T3** all 21 system patterns, same 30 stocks (notes off), ranked by
  net result after costs; labelled preliminary per pattern with < 30 trades.
- Report each like the HINDCOPPER run: trades, win %, avg net % (after
  `itemized_round_trip_cost`), year 1 vs year 2, exits, stretch vs exit move.

### Step 3: run setup
- One process per stock (each ~20-60 s for one pattern, ~3.5 min for all 21
  on 2 years); a small parallel launcher.
- Optional: run on a cheaper Europe VM (user found them cheaper); backtest
  workers don't need to be near Mumbai, the live app does.
