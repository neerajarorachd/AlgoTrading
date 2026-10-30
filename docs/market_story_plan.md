# Market story: plan

Agreed 2026-10-30. Workflow chart: https://claude.ai/artifact/Tz1GrUknvUf2k4oDMDgKoM
(private page). This file is the copy that travels with the code.

## Idea

The walkthrough records high-level movement as a *story*: one row per state
run, not one event per candle. Example rows:
`[3min, bb_width, squeezing, from constant, 10 candles]`,
`[5min, price_vs_ma50, above, 25 candles, 2 rejections, slope before 1%]`.
A setup is all of a stock's open rows read together; the prediction unit
looks up past moments with the same setup and says what usually happened
next. Every prediction and recommendation is graded later, so the system
learns.

## Decisions (user, 2026-10-30)

- BB width "constant" = anchored band: settles when the width changed < 10%
  over 5 candles, then stays constant within +-10% of the settled width
  (1.5% -> 1.35..1.65%); a break above = expanding, below = squeezing.
  (5% rolling was tried first: constant only 11% of 5-min candles.)
- Price-line gap "constant" = anchored band +-15% of the settled gap, never
  narrower than +-0.1 percentage point.
- Touch / rejection / hugging thresholds are percentages (user settings).
- Two tables: `market_story_today` and `market_story_archive` (moved at EOD,
  first step of the EOD job).
- Every stock in any watchlist gets stories, on 1/3/5 min.
- Each line (VWAP first, then BB, MA21, MA50, EMA50) gets separate rows for
  its own slope, price vs line, and the price-line gap. Volume is linked
  (average volume ratio on every row, plus its own row).
- Prediction unit evaluates the whole setup with back-off (drop the least
  useful condition until there is enough evidence). MACD is the anchor and
  is never dropped.
- A prediction answers "up X% before down Y% within N candles", both
  directions. First timeframe: 5 min only.
- scikit-learn is installed for the gradient-boosting comparison.
- Screens (via Jira): per-stock story panel with tabs (Story, Setup now,
  Predictions, Events, Levels, Patterns); a Filter popup on Market Watch that
  reuses the chart's tabbed panel (`IndicatorPanelTabs.jsx`) plus a States
  tab, with its own saved selection.
- Swing review: label HH/LH/HL/LL with leg % and swing ratio; test a
  minimum leg and ZigZag-style confirmation against today's fixed 5 candles.

- Line tests are episodes (2026-10-30): first touch after an approach starts
  a test, closes inside the zone keep it open, a close out ends it as a bounce
  or a break. Zone 0.3 x ATR (~0.05-0.10% of price, per stock).
- The story also watches open trades: when the setup turns against a trade
  (BB squeezing, line break, MACD turning, other movements), it raises an
  exit alert (user's purpose: catch a U-turn halfway). Preliminary test: no
  automatic story exit beat stop / target / day end in both years, so alerts
  only until proven on 15-30 stocks.
- Capture "calm then move": BB width and VWAP gap both constant, then both
  expanding in one direction.
- Nothing is decided without testing on 15-30 stocks over 2 years.

## Phases (each has a gate)

| Phase | What | Gate |
|---|---|---|
| P0 | Offline proof on 2 years x 13 stocks: `backend/story/` pure modules + research scripts in `D:\AI Trading\research\market_story_2026-10-30` | User reviews results; stop if no value |
| P1 | Swing labels (leg %, ratio), day/previous-day high-low breaks, line touch/rejection/cross events; `migrate_swing_label_columns.py` | Tests + replayed day on test stack |
| P2 | `market_story_today/_archive` live behind `story_enabled` (off by default); saved at the 15-min flush; rebuilt after restart; EOD archive move first; `/api/story/<symbol>`, `/api/story/now` | Test stack match, mid-day restart test, one live day |
| P3 | Prediction unit, shown only: `setup_evidence` (weekly rebuild, seeded from P0), `setup_predictions` graded every 15 min | 2-4 weeks of graded predictions |
| P4 | Setup verdict (RS1 v2) in shadow mode beside RS1; `STATE()`, `STATE_AGE()`, `PREV_STATE()`, `REJECTIONS()` condition words | Beats RS1 on the same days; user approves switch |
| P5 | Stock story panel + Filter popup (Jira tickets) | UI suite green; user checks one at a time |

Telegram and paper trading follow P4 as their own plan.

## Ground rules

Separate branch (`story`), impact list before each phase, every live piece
off by default and unable to break the engine, migrations before live
restarts, full test suite before each commit, rule book updated when a
decision is finalized. Watch scoring and the prediction tracker stay until
the user decides keep / merge / drop.
