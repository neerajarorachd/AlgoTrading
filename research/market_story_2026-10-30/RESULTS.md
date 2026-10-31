# Market story P0 results (2026-10-30)

Data: 13 stocks x 2 years, 5 min (3 min for row counts). Code: branch `story`,
`backend/story/`. Walk-forward: learn on year 1 (to 2025-09-16), judge year 2.
Outputs: `*.out` in this folder.

## Story rows (build_stories.out, story_stats.out)
- ~170 rows per stock per day on 5 min, ~268 on 3 min (22 subjects); about
  10x fewer than one row per subject per candle. ~65 us per candle.
- Anti-flicker hold: 1 candle -> 298 runs/day, 50% are 1-2 candles; 2 -> 170,
  18%; 3 -> 125, 3.5%. Kept 2.
- BB width "constant" (< 5% change over 5 candles, decided) covers only 11%
  of 5-min candles; VWAP gap "constant" (10% relative) only 13%: thresholds
  to review.

## Prediction unit (predict_unit.out), year 2
Question: up 0.5% before down 0.25% within 12 candles (and the mirror).
Base rate ~21-23%.

| Variant | AUC up | AUC down | Top 10% hit up |
|---|---|---|---|
| MACD state alone | 0.512 | 0.506 | 23.4% |
| Back-off, 30 cases | 0.561 | 0.549 | 25.8% |
| Back-off, 300 cases + shrink | 0.581 | 0.564 | 28.0% |
| Gradient boosting | 0.596 | 0.575 | 29.0% |

- MACD *state* alone predicts almost nothing; when allowed to rank, the data
  puts it at #32 / #43 of 44 conditions. Locking it (user decision) costs
  nothing (locked 0.561 vs free 0.566 at 30 cases).
- The setup as a whole has a modest, real out-of-sample signal.
- Overlapping candles inflate n; the variant comparison is like-for-like.

## Filter on MACD-widening trend trades (filter_test.out), year 2
- 7 plan stocks, 262 trades: all = Rs 1.69L after slippage; back-off "above
  average" keeps 241 (Rs 1.73L), the 21 dropped lost Rs 8.6k.
- Halves by prediction: GBM top half avg +0.47% vs bottom +0.25% (7 stocks);
  +0.27% vs +0.11% (13 stocks). Same direction for the back-off lookup.
- Not statistically proven (~240 trades per half, ~1.2 standard errors).

## Swings (swing_compare.out)
- HH/HL/LH/LL labels alone: next 12 candles in the label's direction 49-50%,
  average ~0.00%, for every mode (fixed 5 today, alternate, minimum leg 0.5/1%,
  fixed 3, ZigZag 0.3-1%). At confirmation price has already moved 0.4-1.1%.
- Swing ratio and previous-leg size bands: no edge (after an HL, up only
  42-46%).
- Recommendation: keep today's fixed-5 confirmation; record labels, leg % and
  ratio as context for the setup and the panel, not as signals.

## Update 2026-10-30: anchored "constant" bands (user decision)
BB width: settles at < 10% change over 5 candles, then constant within +-10% of
the settled width (1.5% -> 1.35..1.65%). Price-line gaps: settle at < 15%, then
constant within +-15% of the settled gap, never narrower than +-0.1 point.
- Constant share: BB width 11% -> 30%; VWAP gap 13% -> 26%; EMA50 gap 26%.
- Rows per stock-day 5 min: 173 (was 170).
- Prediction AUC: lookup 0.582 up / 0.564 down, GBM 0.598 / 0.576 (unchanged).
- Filter, year 2: 13 stocks lookup top half +0.268% vs bottom +0.108% per trade
  (Rs 1.07L vs Rs 9k after slippage); 7 plan stocks top +0.367% vs bottom
  +0.343% (the earlier 7-stock gap mostly disappeared, so treat it as noise
  until live data says otherwise).

## Line tests as episodes (line_tests.out, line_tests_atr03.out, line_zone_by_stock.out)
- First touch after an approach (close 3 zones away) starts a test; a close out
  of the zone ends it as bounce / break. Without the approach rule: ~87 tests
  per stock-day (7 lines), mostly 1 candle; with it: 26 (0.10%) / 33 (0.3xATR).
- No standalone edge at any zone: the next 12 candles go the exit's way 46-50%,
  average within +-0.03%; with the EMA50 trend +0.01% (far below ~0.1% costs).
- Per stock (5-min ATR 0.16% RELIANCE .. 0.34% HINDCOPPER): a fixed zone gives
  calm stocks fewer tests (RELIANCE 0.10%: 17/day) and volatile ones more
  (HINDCOPPER 0.10%: 31/day); 0.3xATR gives every stock ~32-36/day with the
  same whipsaw share (63-69%), i.e. ATR sizing calibrates per stock.
  Wider zones: fewer tests, more bounces (0.20%: 81-89%), more "held".
- Story rerun with the new price_vs_line rows: prediction AUC 0.576 / 0.594
  (was 0.582 / 0.598); filter split flipped on the 7 stocks (lookup bottom half
  better) -> the filter result is noise until live data says otherwise.

## Story-based exits (exit_test.out, exit_test2.out) - PRELIMINARY, 13 stocks
Same MACD-widening trades; stop 2xATR / target 10xATR kept, story exit on top.
7 plan stocks, net Rs after costs + slippage, Y1 / Y2: base 1.18L / 1.64L;
line 0.96L / 0.55L; fade 0.35L / 0.07L; 6-of-N against 1.17L / 1.08L;
prediction flip (Y2) 1.45L. Only-when-losing: 6-of-N 1.36L / 1.12L (better Y1,
worse Y2); protect-profit (stop to entry): 6-of-N 1.24L / 1.14L. None beats the
base in both years: story exits cut big winners. Story turns = alerts only.

## Calm then move (calm_breakout.out) - PRELIMINARY, 13 stocks
BB width + VWAP gap both constant >= 4 candles, then both expanding within 3
candles: 443 events in 2 years (~1 per stock per 14 days), next 12 candles in
the breakout direction 53.5%, avg +0.036%; >= 10 candles: 21 events. No edge
shown; needs the 15-30 stock set.

## U-turn drop range (uturn_test.out) - PRELIMINARY, 13 stocks
Part 1, best profit reached -> how trades ended (all 13, 2 years):
0.25-0.5%: 120 trades, avg -0.26%, 62% ended red; 0.5-1.0%: 150, avg -0.05%,
45% red; 1.0-1.5%: 107, +0.58%, 18% red; 1.5%+: 182, 4% red. The U-turn zone
is trades that peak at 0.25-1.0%.
Part 2, exits after a drop from the best close (stop/target kept):
- Tight (give back 33-67% after +0.5%, or 1.5xATR): lose a lot (7 stocks
  Y1/Y2 as low as Rs 17k / 36k vs base 1.18L / 1.64L); hurt > helped.
- Only wide trails are near neutral: 3xATR from the best close after +1.0%
  (7 stocks 1.16L / 1.52L; 13 stocks 0.76L / 1.18L vs 0.80L / 0.99L) or after
  +1.5% (1.14L / 1.56L; 0.76L / 1.17L). Helped ~= hurt.
- With the story (4+ against): better on 13 stocks Y1 at +0.5%, worse Y2.
Conclusion (preliminary): exiting U-turns early costs more than it saves,
because the same dips happen in the trades that become big winners. The only
drop range that does not hurt is wide: >= 3xATR (~0.5-1.0% of price on 5-min)
from the best close, once the trade is >= 1-1.5% in profit; insurance, not
profit. Re-test on 15-30 stocks.

## Story-built entries (story_entry.out) - PRELIMINARY, 13 stocks
Best: MACD cross + BB expanding + BB width >= 1% + volume > 20-avg + EMA50 trend,
exit 2xATR / 10xATR / day end: 7 stocks Rs 89k / 70k, 13 stocks Rs 31k / 18k
(Y1 / Y2) - positive both years but below the MACD-widening plan (1.18L / 1.64L;
80k / 99k). Fixed % exits 0.2/0.5, 0.4/0.8, 0.4/1.2: lose for almost every rule.

## Opening range breakout on "stocks in play" (orb_test.out) - PRELIMINARY
Zarattini & Aziz shape (first 5/15 min, direction of the range candle, stop
0.1/0.2 x daily ATR, day-end exit, relative volume filter, top-K per day) on
our 13 stocks: loses in every variant (win 5-33%, avg -0.16% .. +0.03% before
costs). Consistent with a public replication finding the net edge ~0 after
costs; the paper's edge needs daily selection from thousands of stocks.

## LL -> HH -> HL -> HH leg sizes (swing_sequence_gaps.out) - PRELIMINARY, 13 stocks
5-min, swings 5 candles each side, alternating; whole sequence inside one day.
Turn up (450): leg 1 LL->HH median 1.64% (middle half 1.12-2.31), pullback HH->HL
0.63% (0.45-0.89), new HH 1.13% (0.76-1.77); pullback ratio 0.39x (0.28-0.55);
~27 candles (2h15m) from 1st to 4th swing. Turn down (688): 1.65% / 0.61% /
1.05%, ratio 0.38x. Calm stocks (RELIANCE, TCS) about 1.0-1.1 / 0.4 / 0.7%;
volatile (HINDCOPPER, BHEL) 2.2-2.8 / 0.7-0.8 / 1.2-1.8%. After the 4th swing
is known: next 12 candles 49.5% / 51.3% in the trend's direction (no edge).

## What was present when LL-HH-HL-HH worked (sequence_success_elements.out) - PRELIMINARY
1111 sequences (both directions), 50.6% went the trend's way in the next 12
candles. Elements (oriented to the new trend) that helped by 3+ points in BOTH
years: ADX 20-30 with the opposite DI still leading (77 seq, 64.9% vs 49.5%),
close still beyond the previous day's opposite level (50, 62.0%), VWAP still
sloping against (42, 59.5%), RSI still on the opposite side (41, 56.1%), close
near the day's high for a turn up / low for a turn down (647, 53.0% vs 47.2%).
Four of five say: the turn worked best while the bigger picture was still the
old trend (an early reversal). No pair had 30+ cases. On all candles these
elements alone are no help (48-52%), so they matter only after the sequence.
5 of ~80 elements passing could partly be luck: confirm on the 150-stock set.
