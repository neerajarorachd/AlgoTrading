# Plan for 2026-10-31: test all findings on 150 stocks

Written 2026-10-30 at the end of a long session. Read this first, plus
`D:\AI Trading\AlgoTrading\docs\RULEBOOK.md` (sections 1.8, 7, 7a, 7b) and
`D:\AI Trading\research\market_story_2026-10-30\RESULTS.md` (all 13-stock results).

## Why

Rule 1.8: nothing is decided without 15-30+ stocks over 2 years. Everything so
far is preliminary (13 stocks). This is the **last research test**; after it,
research stops and system building starts (rule book 7b).

## Step 0 - before 18:00 IST (the VM shuts down at 18:00)

1. Open the SSH tunnel: `ssh -L 1433:127.0.0.1:1433 trading-server -N`
   (check state first; wait 30-60 s between retries; fail2ban risk).
2. Fetch the universe (5-min + daily, 2 years, LOCAL files only, not SQL Server):
   ```
   cd "D:\AI Trading\AlgoTrading"
   .venv\Scripts\python.exe "D:\AI Trading\research\universe_2026-10-31\fetch_universe.py" --token-from-db
   ```
   - Takes the FIXED3 Dhan token from SQL Server (the .env token expired
     2026-09-11). Never print the token.
   - Picks the top 150 of 206 resolved candidates by median daily turnover
     -> `universe.json`; 5-min candles -> `5min/<SYM>.pkl`, daily -> `daily/`.
   - Resumable: re-run skips files already written. ~30-60 min.
   - Disk: D: had ~7 GB free on 10-30; keep outputs compact.

## Step 1 - loader

Add `ucommon.py` in this folder: same interface as
`market_story_2026-10-30/mcommon.py` (`SYMBOLS`, `candles(sym, "5min")`, `day`,
`minute_of_day`, `Y2_START`, `rows_from_candles`, `run_tracker`) but reading
`5min/*.pkl`. 3-min is not fetched; every test below is 5-min. For the
15-min structure test, roll 5-min up to 15-min (3 candles).

Split: learn on year 1, judge on year 2 (Y2_START = 1758067200, 2025-09-17;
adjust if the fetched range differs). Report Y1 and Y2 separately, always.

Memory: 150 stocks x ~37k candles = ~5.5M rows. Do NOT keep a snapshot dict
per candle for all stocks in RAM (the 13-stock snaps file was 221 MB). Process
per stock, and store snapshots compactly (integer codes) or only at the
candles a test needs.

## Step 2 - the tests (same rules, same costs as the 13-stock runs)

Money everywhere: Rs 5L, no leverage, Rs 2.5L per trade, shared pool, one
position per stock, `itemized_round_trip_cost` + 0.05% slippage.

| # | Finding to confirm | 13-stock result | Script to re-point |
|---|---|---|---|
| 1 | MACD cross-widening + EMA50 trend + ADX 30-40, 09:30-14:45, stop 2xATR / target 10xATR / day end | Rs 80k / 99k a year (16-20%) | `base_setup_report.py` (uses cross_widening `common.py`) |
| 2 | Story-built entry: MACD cross + BB expanding + BB width >= 1% + volume > 20-avg + trend | Rs 31k / 18k; fixed % exits lose | `story_entry.py` |
| 3 | HL entry: LL->HH->HL, dip HH->HL >= 1%, early-reversal filter, target 1.0 x first rise, stop 0.05% beyond HL | ~Rs 20k a year, 150-175 trades | `hl_entry_gap2.py` |
| 4 | Same as 3 with the structure on 15-min swings, entry on 5-min | not tested | new, from `hl_entry.py` |
| 5 | Early-reversal elements after LL-HH-HL-HH (prev_day / rsi / vwap_slope / adx 20-30 against) | 56-65% vs 50%, n 41-77 | `sequence_success_elements.py` |
| 6 | Setup prediction (story lookup, >= 300 cases + shrink, MACD locked; and gradient boosting) | AUC 0.58 / 0.60 | `build_stories.py`, `predict_unit.py` |
| 7 | Prediction as a filter / ranking on test 1's trades | top half better on 13 stocks, flipped on 7 | `filter_test.py` |
| 8 | U-turn exits: only 3xATR from best after >= 1-1.5% profit is ~neutral | none beat the base | `uturn_test.py` |
| 9 | Line tests: zone 0.3 vs 0.45 x ATR; no standalone edge | 46-50% | `line_zone_by_stock.py` |
| 10 | Opening range breakout on stocks in play: with 150 stocks the daily "top by relative volume" pick is now meaningful | lost on 13 | `orb_test.py` |

For each: PASS (positive in both years, enough trades) / FAIL, and the Rs a
year on Rs 5L. Also per-stock spread: how many stocks are positive.

## Step 3 - report

One results page (artifact) + append to a new `RESULTS.md` in this folder:
which findings hold on 150 stocks, which setups enter the setup library, and
the combined yearly Rs if the passing setups run together on Rs 5L.
Then update `docs/RULEBOOK.md` section 7 with what is confirmed.

## After this test (rule book 7b)

Stop trend research. Build: setup library (15-20 narrow setups), market story
P2 (story tables live, branch `story`), recommendations, paper trading. Then
calibrate setups on paper / live data.

## State at the end of 10-30

- Branch `story` (worktree `D:\AI Trading\AlgoTrading-story`), last commit
  f79fdab, not merged: market story P0 + P1 (point events, line tests, swing
  labels; engine setting `point_events_v2` off by default). Live needs
  `backend/scripts/migrate_swing_label_columns.py` on SQL Server BEFORE any
  restart on that code.
- `master` has UNCOMMITTED rule book edits (sections 1.8, 7a, 7b); commit only
  when the user asks.
- Open decisions: line zone 0.45 vs 0.3 x ATR; Signals picker groups for
  'line' / 'level' (UI, maybe Jira); minimum average move after costs as a
  setup gate (proposed, not decided).
- Test DB `local_dev_test.db` has the swing-label columns and point_events_v2=1.
