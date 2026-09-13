"""Shared pure indicator-adjacent helpers used by both activity_engine.py
(price-action/crossover detection) and walkthrough_engine.py (indicator
snapshot persistence, not built yet) — kept here so callers don't each grow
their own copy.

classify_trend() mirrors the Trading project's LibAnalysisStrength.py
GetVWAPStrength: rather than comparing just two points (now vs. N candles
ago), it looks at every step across a lookback window and counts how many
consecutive steps move the (absolute) value up vs. down. A window is only
called "widening"/"narrowing" when a strong majority of its own steps agree
— a trend-consistency check, not a two-point comparison, so a single noisy
candle can't flip the call.

RsiState/update_rsi and MacdState/update_macd are small incremental state
machines (Wilder's smoothing for RSI, EMA for MACD) rather than the
vectorized/TA-Lib approach planned for the full walkthrough engine — they
only need to feed crossover detection here, one value at a time, so a
lightweight incremental version avoids pulling in a DataFrame + TA-Lib
dependency just for this.
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

# Default lookback and agreement ratio, shared by every trend-classified
# quantity (BB width, VWAP distance, and anything added later) so they all
# read the same way. Matches the Trading project's own 0.7 agreement ratio;
# lookback is 20 per instruction (their own reference used 5).
TREND_LOOKBACK = 20
TREND_RATIO = 0.7


@dataclass(frozen=True)
class TrendResult:
    direction: str  # "widening" | "narrowing" | "flat"
    increasing: int
    decreasing: int
    steps: int


def classify_trend(values: Sequence[float], ratio: float = TREND_RATIO) -> TrendResult:
    """values: most recent last. Compares abs(values[i]) to abs(values[i-1])
    for every consecutive pair — matching GetVWAPStrength's own use of
    abs() so a signed series (e.g. close-vwap, which can flip sign) is
    judged on its magnitude trend, not its sign. Needs at least 2 values;
    fewer returns "flat" with zero counts."""
    if len(values) < 2:
        return TrendResult("flat", 0, 0, 0)
    increasing = 0
    decreasing = 0
    for i in range(1, len(values)):
        prev, curr = abs(values[i - 1]), abs(values[i])
        if curr > prev:
            increasing += 1
        elif curr < prev:
            decreasing += 1
    steps = len(values) - 1
    if steps > 0 and increasing >= steps * ratio:
        direction = "widening"
    elif steps > 0 and decreasing >= steps * ratio:
        direction = "narrowing"
    else:
        direction = "flat"
    return TrendResult(direction, increasing, decreasing, steps)


def trend_intensity(trend: TrendResult, ratio: float = TREND_RATIO) -> float:
    """How far past the qualifying agreement ratio the winning direction's
    step count is — 1.0 right at the ratio (barely qualifies), growing
    toward a max of 1/ratio as every single step agrees. Returns 0 for a
    "flat" trend (caller shouldn't be scoring an undetected trend anyway)."""
    if trend.steps == 0:
        return 0.0
    if trend.direction == "widening":
        return (trend.increasing / trend.steps) / ratio
    if trend.direction == "narrowing":
        return (trend.decreasing / trend.steps) / ratio
    return 0.0


# --------------------------------------------------------------------- RSI (Wilder's)

RSI_PERIOD = 14


class RsiState:
    __slots__ = ("prev_close", "avg_gain", "avg_loss", "_seed_gains", "_seed_losses")

    def __init__(self):
        self.prev_close: Optional[float] = None
        self.avg_gain: Optional[float] = None
        self.avg_loss: Optional[float] = None
        self._seed_gains: list = []
        self._seed_losses: list = []


def update_rsi(state: RsiState, close: float, period: int = RSI_PERIOD) -> Optional[float]:
    """Wilder's RSI, updated one close at a time. None until `period` price
    changes have been observed (the first close only sets a baseline, it
    can't produce a change) — the standard Wilder seed is a simple average
    of the first `period` gains/losses, then each later step folds the new
    gain/loss in at weight 1/period."""
    if state.prev_close is None:
        state.prev_close = close
        return None
    change = close - state.prev_close
    state.prev_close = close
    gain, loss = max(change, 0.0), max(-change, 0.0)

    if state.avg_gain is None:
        state._seed_gains.append(gain)
        state._seed_losses.append(loss)
        if len(state._seed_gains) < period:
            return None
        state.avg_gain = statistics.fmean(state._seed_gains)
        state.avg_loss = statistics.fmean(state._seed_losses)
    else:
        state.avg_gain = (state.avg_gain * (period - 1) + gain) / period
        state.avg_loss = (state.avg_loss * (period - 1) + loss) / period

    if state.avg_loss == 0:
        return 100.0
    rs = state.avg_gain / state.avg_loss
    return 100 - 100 / (1 + rs)


# --------------------------------------------------------------------- ATR (Wilder's)

ATR_PERIOD = 14


class AtrState:
    __slots__ = ("prev_close", "avg_tr", "_seed_trs")

    def __init__(self):
        self.prev_close: Optional[float] = None
        self.avg_tr: Optional[float] = None
        self._seed_trs: list = []


def update_atr(state: AtrState, high: float, low: float, close: float, period: int = ATR_PERIOD) -> Optional[float]:
    """Wilder's ATR (Average True Range), updated one candle at a time.
    True Range is the largest of: this candle's own high-low, the gap up
    from the prior close, or the gap down from the prior close — captures
    overnight/inter-candle gaps a plain high-low range would miss (on the
    very first candle, with no prior close yet, it's just high-low). None
    until `period` true ranges have been observed; seeded the same way as
    update_rsi (a simple average of the first `period`, then each later
    step folds the new one in at weight 1/period).

    The natural next use for this: normalize the swing/structure-shift and
    double-top/bottom thresholds in activity_engine.py (currently fixed
    percentages) against ATR instead, so a volatile stock and a quiet one
    aren't held to the same absolute bar — flagged as a real gap after
    real data showed those fixed-percentage thresholds behaving very
    differently across stocks (see the project's own notes on this)."""
    if state.prev_close is None:
        tr = high - low
    else:
        tr = max(high - low, abs(high - state.prev_close), abs(low - state.prev_close))
    state.prev_close = close

    if state.avg_tr is None:
        state._seed_trs.append(tr)
        if len(state._seed_trs) < period:
            return None
        state.avg_tr = statistics.fmean(state._seed_trs)
    else:
        state.avg_tr = (state.avg_tr * (period - 1) + tr) / period
    return state.avg_tr


# --------------------------------------------------------------------- MACD (EMA-based)

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9


class MacdState:
    __slots__ = ("ema_fast", "ema_slow", "ema_signal", "count", "macd_count")

    def __init__(self):
        self.ema_fast: Optional[float] = None
        self.ema_slow: Optional[float] = None
        self.ema_signal: Optional[float] = None
        self.count = 0
        self.macd_count = 0


def update_macd(
    state: MacdState, close: float,
    fast: int = MACD_FAST, slow: int = MACD_SLOW, signal: int = MACD_SIGNAL,
) -> Tuple[Optional[float], Optional[float]]:
    """Returns (macd_line, macd_signal) — macd_line is None until `slow`
    closes have been seen (the slow EMA needs to mature past its naive
    seed-at-first-value start), macd_signal is None until `signal` more
    macd_line values have accumulated on top of that."""
    state.count += 1
    k_fast, k_slow = 2 / (fast + 1), 2 / (slow + 1)
    state.ema_fast = close if state.ema_fast is None else close * k_fast + state.ema_fast * (1 - k_fast)
    state.ema_slow = close if state.ema_slow is None else close * k_slow + state.ema_slow * (1 - k_slow)
    if state.count < slow:
        return None, None

    macd_line = state.ema_fast - state.ema_slow
    state.macd_count += 1
    k_signal = 2 / (signal + 1)
    state.ema_signal = macd_line if state.ema_signal is None else macd_line * k_signal + state.ema_signal * (1 - k_signal)
    if state.macd_count < signal:
        return macd_line, None
    return macd_line, state.ema_signal


# --------------------------------------------------------------------- Stochastic (full, smoothed)

# "Full stochastic" periods matching the Trading project's own AddIndicatorColumns
# (talib.STOCH fastk_period=5, slowk_period=3, slowd_period=3) rather than the
# more commonly quoted 14/3/3 raw stochastic.
STOCH_FASTK_PERIOD = 5
STOCH_SLOWK_PERIOD = 3
STOCH_SLOWD_PERIOD = 3


class StochasticState:
    __slots__ = ("highs", "lows", "raw_k_values", "slow_k_values")

    def __init__(self, fastk_period: int = STOCH_FASTK_PERIOD, slowk_period: int = STOCH_SLOWK_PERIOD, slowd_period: int = STOCH_SLOWD_PERIOD):
        self.highs: deque = deque(maxlen=fastk_period)
        self.lows: deque = deque(maxlen=fastk_period)
        self.raw_k_values: deque = deque(maxlen=slowk_period)
        self.slow_k_values: deque = deque(maxlen=slowd_period)


def update_stochastic(
    state: StochasticState, high: float, low: float, close: float,
    fastk_period: int = STOCH_FASTK_PERIOD, slowk_period: int = STOCH_SLOWK_PERIOD, slowd_period: int = STOCH_SLOWD_PERIOD,
) -> Tuple[Optional[float], Optional[float]]:
    """Returns (%K, %D) — both smoothed ("slow"/"full" stochastic, matching
    talib.STOCH's own defaults, not the raw single-smoothed variant). %K is
    None until `fastk_period` candles are seen and `slowk_period` raw %K
    readings have accumulated; %D needs `slowd_period` more %K readings on
    top of that."""
    state.highs.append(high)
    state.lows.append(low)
    if len(state.highs) < fastk_period:
        return None, None

    highest_high, lowest_low = max(state.highs), min(state.lows)
    span = highest_high - lowest_low
    raw_k = 100.0 if span <= 0 else 100 * (close - lowest_low) / span
    state.raw_k_values.append(raw_k)
    if len(state.raw_k_values) < slowk_period:
        return None, None

    slow_k = statistics.fmean(state.raw_k_values)
    state.slow_k_values.append(slow_k)
    if len(state.slow_k_values) < slowd_period:
        return slow_k, None
    return slow_k, statistics.fmean(state.slow_k_values)


# --------------------------------------------------------------------- generic crossovers

def crossed_above(prev_a: Optional[float], prev_b: Optional[float], curr_a: Optional[float], curr_b: Optional[float]) -> bool:
    """True when series a was at or below series b and is now strictly
    above it — works for two moving series (MACD vs. signal, MA21 vs. MA50)
    or a series against a constant threshold (pass the same value as both
    prev_b and curr_b). None in any input means "not enough data yet"."""
    if None in (prev_a, prev_b, curr_a, curr_b):
        return False
    return prev_a <= prev_b and curr_a > curr_b


def crossed_below(prev_a: Optional[float], prev_b: Optional[float], curr_a: Optional[float], curr_b: Optional[float]) -> bool:
    """crossed_above's mirror."""
    if None in (prev_a, prev_b, curr_a, curr_b):
        return False
    return prev_a >= prev_b and curr_a < curr_b


def cross_intensity(prev_a: float, prev_b: float, curr_a: float, curr_b: float) -> float:
    """How sharp the crossing step was — the change in (a - b) over the one
    candle that produced the cross. 0 would mean no separation happened at
    all (can't actually occur on a real cross), growing with how decisively
    the two series pulled apart on the crossing candle itself."""
    return abs((curr_a - curr_b) - (prev_a - prev_b))
