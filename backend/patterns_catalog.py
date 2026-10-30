"""System patterns (ALGO-31): the patterns that ship with the app, seeded at
startup by LibPatterns.seed_system_patterns. Read-only to users, who edit a
copy instead.

Each pattern is a set of conditions in the shared condition language (see
docs/condition_ready_reckoner.md). A pattern is FORMED when at least 80% of
its conditions hold on a candle; its score is weight x match %. Weights here
are starting values -- the pattern backtest replaces them (backtest_weight).

Types: bullish / bearish (drive Buy/Sell), caution (pink) and avoid (red),
which override every positive colour.

Raw event names in EVENT(...) are the activity engine's own codes
(PatternDefinition / InstrumentActivity.activity)."""
from __future__ import annotations

from typing import Sequence, Tuple

AVG_VOLUME = "mean(VOLUME, 20)"
AVG_BODY = "mean(BODY, 10)"

# (key, name, type, category, weight, lookback, cooldown, description, [conditions])
# Weights (user, 2026-10-14): MACD crossover + lines moving apart matters most
# (5); MACD + Stochastic crosses together are common, so they weigh little (2).
SYSTEM_PATTERNS = (
    # ---- bullish ----
    ("strong_hammer", "Strong hammer", "bullish", "candle", 3, 10, 5,
     "Hammer with a long lower wick on a meaningful candle, on above-average volume.",
     ["EVENT(hammer)", "LOWER_WICK >= 2 * BODY", "RANGE > ATR", f"VOLUME > {AVG_VOLUME}"]),
    ("strong_bullish_engulfing", "Strong bullish engulfing", "bullish", "candle", 3, 10, 5,
     "Bullish engulfing whose body is well above the recent average, on good volume.",
     ["EVENT(bullish_engulfing)", f"BODY > 1.5 * {AVG_BODY}", f"VOLUME > {AVG_VOLUME}"]),
    ("strong_three_white_soldiers", "Strong three white soldiers", "bullish", "candle", 4, 10, 5,
     "Three white soldiers, all three with big bodies and small upper wicks.",
     ["EVENT(three_white_soldiers)", f"COUNT(BODY > 1.2 * {AVG_BODY}, 3) >= 3",
      "COUNT(UPPER_WICK < 0.3 * BODY, 3) >= 3"]),
    ("bullish_momentum", "Bullish momentum", "bullish", "indicator", 4, 10, 5,
     "RSI above 45 and rising, a recent MACD cross up with the histogram expanding, and Bollinger Bands widening past 1%.",
     ["RSI > 45", "RSI > RSI[-1]", "RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL))",
      "MACD_HIST > MACD_HIST[-1]", "BB_WIDTH > BB_WIDTH[-1]", "BB_WIDTH > 1"]),
    ("macd_stoch_bull_confluence", "MACD + Stochastic bullish confluence", "bullish", "indicator", 2, 10, 5,
     "MACD and Stochastic both crossed up recently, with RSI above 50 and price above VWAP.",
     ["RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL))", "RECENTLY(CROSSED_UP(STOCH_K, STOCH_D))",
      "RSI > 50", "CLOSE > VWAP", f"VOLUME > {AVG_VOLUME}"]),
    ("macd_departure_bull", "MACD departure (bullish)", "bullish", "indicator", 5, 10, 5,
     "MACD crossed up recently, the lines have moved apart for 3 candles in a row, and the Bollinger gap is over 1%. (User's pattern, 2026-10-14.)",
     ["RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL))", "FOR(MACD_HIST > MACD_HIST[-1], 3)", "BB_WIDTH > 1"]),
    ("vwap_reclaim", "VWAP reclaim", "bullish", "price_action", 3, 10, 5,
     "Price dipped below VWAP recently and has closed back above it on strong volume.",
     ["RECENTLY(CLOSE < VWAP, 5)", "CLOSE > VWAP", f"VOLUME > 1.5 * {AVG_VOLUME}"]),
    # ---- bearish ----
    ("strong_shooting_star", "Strong shooting star", "bearish", "candle", 3, 10, 5,
     "Shooting star with a long upper wick on a meaningful candle, on above-average volume.",
     ["EVENT(shooting_star)", "UPPER_WICK >= 2 * BODY", "RANGE > ATR", f"VOLUME > {AVG_VOLUME}"]),
    ("strong_bearish_engulfing", "Strong bearish engulfing", "bearish", "candle", 3, 10, 5,
     "Bearish engulfing whose body is well above the recent average, on good volume.",
     ["EVENT(bearish_engulfing)", f"BODY > 1.5 * {AVG_BODY}", f"VOLUME > {AVG_VOLUME}"]),
    ("strong_three_black_crows", "Strong three black crows", "bearish", "candle", 4, 10, 5,
     "Three black crows, all three with big bodies and small lower wicks.",
     ["EVENT(three_black_crows)", f"COUNT(BODY > 1.2 * {AVG_BODY}, 3) >= 3",
      "COUNT(LOWER_WICK < 0.3 * BODY, 3) >= 3"]),
    ("bearish_momentum", "Bearish momentum", "bearish", "indicator", 4, 10, 5,
     "RSI below 55 and falling, a recent MACD cross down with the histogram expanding, and Bollinger Bands widening past 1%.",
     ["RSI < 55", "RSI < RSI[-1]", "RECENTLY(CROSSED_DOWN(MACD_LINE, MACD_SIGNAL))",
      "MACD_HIST < MACD_HIST[-1]", "BB_WIDTH > BB_WIDTH[-1]", "BB_WIDTH > 1"]),
    ("macd_stoch_bear_confluence", "MACD + Stochastic bearish confluence", "bearish", "indicator", 2, 10, 5,
     "MACD and Stochastic both crossed down recently, with RSI below 50 and price below VWAP.",
     ["RECENTLY(CROSSED_DOWN(MACD_LINE, MACD_SIGNAL))", "RECENTLY(CROSSED_DOWN(STOCH_K, STOCH_D))",
      "RSI < 50", "CLOSE < VWAP", f"VOLUME > {AVG_VOLUME}"]),
    ("macd_departure_bear", "MACD departure (bearish)", "bearish", "indicator", 5, 10, 5,
     "MACD crossed down recently, the lines have moved apart for 3 candles in a row, and the Bollinger gap is over 1%. (User's pattern, 2026-10-14.)",
     ["RECENTLY(CROSSED_DOWN(MACD_LINE, MACD_SIGNAL))", "FOR(MACD_HIST < MACD_HIST[-1], 3)", "BB_WIDTH > 1"]),
    ("vwap_rejection", "VWAP rejection", "bearish", "price_action", 3, 10, 5,
     "Price poked above VWAP recently and has closed back below it on strong volume.",
     ["RECENTLY(CLOSE > VWAP, 5)", "CLOSE < VWAP", f"VOLUME > 1.5 * {AVG_VOLUME}"]),
    # ---- trend-filtered setups from the 2-year backtest (2026-10-30) ----
    # 13 stocks, Sep 2024 - Sep 2026, intraday, stop 2xATR / target 10xATR,
    # else day-end exit (memory: cross_widening_signal). Four conditions
    # each, so the 80% formed rule means ALL four must hold, exactly as
    # backtested. EVENT_ANY: the backtest used no signal-strength minimum.
    # Results per pattern live in pattern_backtest_results.
    ("macd_widening_trend_bull", "MACD widening with trend (bullish)", "bullish", "indicator", 5, 10, 0,
     "5-min MACD crossed up and the lines kept widening for 3 candles, in an up-trend: price above a rising "
     "EMA50, ADX 30-40 with +DI above -DI, entered 09:30-14:45. Backtested best setup.",
     ["EVENT_ANY(macd_bullish_cross_widening)", "CLOSE > EMA50 and EMA50 > EMA50[-10]",
      "ADX >= 30 and ADX < 40 and PLUS_DI > MINUS_DI", "TF_MINUTES == 5 and TIME >= 930 and TIME < 1445"]),
    ("macd_widening_trend_bear", "MACD widening with trend (bearish)", "bearish", "indicator", 5, 10, 0,
     "5-min MACD crossed down and the lines kept widening for 3 candles, in a down-trend: price below a falling "
     "EMA50, ADX 30-40 with -DI above +DI, entered 09:30-14:45. Backtested best setup.",
     ["EVENT_ANY(macd_bearish_cross_widening)", "CLOSE < EMA50 and EMA50 < EMA50[-10]",
      "ADX >= 30 and ADX < 40 and MINUS_DI > PLUS_DI", "TF_MINUTES == 5 and TIME >= 930 and TIME < 1445"]),
    ("ma_death_cross_trend", "MA death cross with trend", "bearish", "indicator", 4, 10, 0,
     "3- or 5-min MA21 crossed below MA50 in a down-trend: price below a falling EMA50, ADX 30-40 with -DI "
     "above +DI, entered 09:30-14:45.",
     ["EVENT_ANY(ma_death_cross)", "CLOSE < EMA50 and EMA50 < EMA50[-10]",
      "ADX >= 30 and ADX < 40 and MINUS_DI > PLUS_DI", "TF_MINUTES >= 3 and TIME >= 930 and TIME < 1445"]),
    # ---- caution (pink) ----
    ("bb_squeeze_tight", "Bollinger squeeze, no room", "caution", "volatility", 0, 10, 5,
     "Bands narrowing for 4 candles and under 0.6% wide: not enough room for a trade.",
     ["FOR(BB_WIDTH < BB_WIDTH[-1], 4)", "BB_WIDTH < 0.6"]),
    # ---- avoid (red) ----
    ("sideways_chop", "Sideways chop", "avoid", "volatility", 0, 10, 5,
     "Price keeps crossing VWAP inside very narrow bands: sideways, no direction.",
     ["BB_WIDTH < 0.4", "COUNT(CROSSED_UP(CLOSE, VWAP) or CROSSED_DOWN(CLOSE, VWAP), 10) >= 3"]),
    ("too_volatile", "Too volatile", "avoid", "volatility", 0, 10, 5,
     "A candle three times the usual size: too volatile to trade safely.",
     ["RANGE > 3 * ATR"]),
)

# setting keys (SystemSetting): (key, default, description)
PATTERN_SETTING_SEED: Sequence[Tuple[str, float, str]] = (
    ("use_per_stock_weightage", 0,
     "1 = use each stock's own pattern weightage (from backtests) when one exists; "
     "0 = always the pattern's default weightage. Off until per-stock weights are calibrated."),
    ("pattern_formed_threshold", 0.8,
     "A pattern counts as formed when at least this share of its conditions hold (0.8 = 80%)."),
)
