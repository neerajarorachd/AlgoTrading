# Condition Maker: Ready Reckoner

One language for walkthroughs, patterns and strategies. A keyword means the
same thing everywhere it appears.

Status key: **✓ works today** · **◐ planned (pattern table, ALGO-5)**.

---

## 1. How a condition reads

```
<value> <compare> <value>
```

- **Value**: a field, a number, a function, or arithmetic on these. Examples:
  `RSI`, `45`, `mean(VOLUME, 20)`, `(BB_UPPER - BB_LOWER) / CLOSE * 100`.
- **Compare**: `>` `>=` `<` `<=` `==` `!=`
- **No time word** means the condition is about the **current candle**, the latest
  closed one.
- Several conditions are combined with **AND / OR groups** in the panel. Inside one
  formula you can also write `and`, `or`, `not` (any case), e.g.
  `RECENTLY(EVENT(hammer) and VOLUME > mean(VOLUME, 20), 5)`.

---

## 2. Fields ✓

| Keyword | Meaning | Short form |
|---|---|---|
| `OPEN` `HIGH` `LOW` `CLOSE` | Candle prices | `O` `H` `L` `C`, `LTP` = CLOSE |
| `VOLUME` | Candle volume | |
| `RSI` | RSI (14) | |
| `MACD_LINE` `MACD_SIGNAL` | MACD (12, 26, 9) lines | |
| `STOCH_K` `STOCH_D` | Stochastic (14, 3) | `SK` `SD` |
| `VWAP` | Day's VWAP (resets each day) | |
| `MA21` `MA50` | Simple moving averages | |
| `ATR` | Average true range (14) | |
| `BB_UPPER` `BB_MIDDLE` `BB_LOWER` | Bollinger Bands (20, 2σ) | |

**✓ Added fields** (EMA and derived)

| Keyword | Meaning |
|---|---|
| `EMA5` `EMA14` `EMA21` `EMA50` | Exponential moving averages (already on the chart) |
| `MACD_HIST` | MACD line minus signal |
| `BB_WIDTH` | (BB_UPPER − BB_LOWER) / BB_MIDDLE × 100, as a % |
| `BODY` | \|CLOSE − OPEN\| |
| `UPPER_WICK` `LOWER_WICK` | Wick lengths |
| `RANGE` | HIGH − LOW |

---

## 3. Looking back at earlier candles ✓

| Write | Means |
|---|---|
| `RSI` | RSI on the current candle |
| `RSI[-1]` | RSI one candle back |
| `CLOSE[-3]` | Close three candles back |

Offsets never reach into the previous day, so at the open `CLOSE[-1]` is empty.

---

## 4. Functions

| Function | Status | Meaning | Example |
|---|---|---|---|
| `slope(F, n)` | ✓ | Change per candle over n candles | `slope(RSI, 3) > 0`: RSI rising |
| `mean(F, n)` | ✓ | Average of the last n candles | `VOLUME > 1.5 * mean(VOLUME, 20)` |
| `CROSSED_UP(a, b)` | ✓ | a crossed above b on this candle | `CROSSED_UP(MACD_LINE, MACD_SIGNAL)` |
| `CROSSED_DOWN(a, b)` | ✓ | a crossed below b on this candle | `CROSSED_DOWN(STOCH_K, STOCH_D)` |
| `EVENT(name)` | ✓ | A raw event fired on this candle **and is strong enough** (section 6a) | `EVENT(hammer)`, `EVENT(bb_squeeze)` |
| `EVENT(name, measure=min)` | ✓ | Same, with your own minimum for this pattern | `EVENT(hammer, range_pct=0.1, volume_ratio=1.2)` |
| `EVENT_ANY(name)` | ✓ | The event fired, however weak | `EVENT_ANY(doji)` |
| `abs(X)` | ✓ | Absolute value | `abs(CLOSE - VWAP) / CLOSE * 100 < 0.1` |
| `median(F, n)` | ✓ | Median of the last n candles | `BODY > 2 * median(BODY, 10)` |
| `HIGHEST(F, n)` `LOWEST(F, n)` | ✓ | Highest/lowest of the last n | `CLOSE > HIGHEST(HIGH[-1], 20)`: breakout |
| `PATTERN(key)` | ◐ | A pattern is active (strategies only) | `PATTERN(bullish_momentum)` |

---

## 5. Time words ✓

| Write | Means | Example |
|---|---|---|
| *(nothing)* | True **now** | `RSI > RSI[-1]`: RSI rising now |
| `RECENTLY(cond)` | True at least once in the last N candles (N = the pattern's lookback, default 10) | `RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL))` |
| `RECENTLY(cond, n)` | Same, with your own n | `RECENTLY(EVENT(hammer), 5)` |
| `FOR(cond, n)` | True on every one of the last n | `FOR(BB_WIDTH > BB_WIDTH[-1], 4)`: expanding 4 candles in a row |
| `COUNT(cond, n) >= k` | True at least k times in the last n | `COUNT(CLOSE > OPEN, 5) >= 4`: 4 of 5 green |
| `BARS_SINCE(cond) <= n` | It happened no more than n candles ago | `BARS_SINCE(EVENT(hammer)) <= 3` |
| `THEN(a, b, n)` | a happened, then b within n candles | `THEN(EVENT(hammer), CROSSED_UP(STOCH_K, STOCH_D), 5)` |

---

## 6a. Signal strength

Every formation is recorded, but `EVENT(name)` only counts the **strong** ones.
Each signal has strength measures; a formation qualifies when it meets every
minimum.

| Signal | Measures |
|---|---|
| hammer / shooting_star | `range_pct`, `wick_ratio`, `volume_ratio` |
| doji | `range_pct`, `volume_ratio` |
| bullish/bearish_engulfing | `body_pct`, `engulf_ratio`, `volume_ratio` |
| piercing_line / dark_cloud_cover | `body_pct`, `volume_ratio` |
| tweezer_top / tweezer_bottom | `range_pct`, `volume_ratio` |
| morning_star / evening_star | `move_pct` |
| three_white_soldiers / three_black_crows | `move_pct`, `median_body_pct` |
| macd_bullish/bearish_cross | `separation_pct` |
| stoch_bullish/bearish_cross | `gap` |
| bb_widening / bb_squeeze | `width_change` |

- `_pct` measures are **% of price**; minimums are set for **1-min** and
  multiplied for 3-min and 5-min (measured: ×1.79 and ×2.36). Ratios and the
  Stochastic gap are the same on every timeframe.
- Minimums are calibrated from history so about 40% of each signal qualifies.
  All of it is configurable: the minimums, the multipliers and the target share.
- A measure that can't be computed yet (the 20-candle volume average in the
  first minutes of a session) doesn't block a formation.

## 6. Worked examples

**Bullish momentum** (bullish, lookback 10)
```
RSI > 45
RSI > RSI[-1]                                  -- rising now
RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL))   -- crossed recently
MACD_HIST > MACD_HIST[-1]                      -- histogram expanding now
BB_WIDTH > BB_WIDTH[-1]                        -- bands expanding now
BB_WIDTH > 1                                   -- bands wider than 1%
```

**Strong hammer** (bullish)
```
EVENT(hammer)
LOWER_WICK >= 3 * BODY                         -- body:wick at least 1:3
RANGE > ATR                                    -- a meaningful candle, not a tiny one
VOLUME > mean(VOLUME, 20)                      -- volume above average
```

**Squeeze (caution)** (pink)
```
FOR(BB_WIDTH < BB_WIDTH[-1], 4)                -- bands narrowing 4 candles in a row
BB_WIDTH < 0.6                                 -- not enough room for a trade
```

**VWAP reclaim** (bullish)
```
RECENTLY(CLOSE < VWAP, 5)
CLOSE > VWAP
VOLUME > 1.5 * mean(VOLUME, 20)
```

---

## 7. Common mistakes

| Instead of | Write | Why |
|---|---|---|
| `RSI rising` | `RSI > RSI[-1]` or `slope(RSI, 3) > 0` | Write the comparison, not a word |
| `MACD crossed` | `RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL))` | A cross happens on one candle; `RECENTLY` keeps it alive for N candles |
| `BB expanding` checked over 10 candles | `BB_WIDTH > BB_WIDTH[-1]` (now) or `FOR(…, 4)` | Expanding is about **now** or a short streak, not "once in 10" |
| `VOLUME > 100000` | `VOLUME > 1.5 * mean(VOLUME, 20)` | Fixed numbers don't carry across stocks |
| `CLOSE[-1]` at 09:15 | — | Offsets don't reach into yesterday, so this is empty at the open |
