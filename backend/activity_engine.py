"""Detects candlestick patterns, price-action signals, indicator crossovers,
swing structure, and graph formations as each 1-/3-/5-min candle closes,
and persists them to instrument_activity. Covers, so far: single-candle
shape patterns (doji, hammer, shooting star), two-candle patterns
(bullish/bearish engulfing, piercing line, dark cloud cover, tweezer
top/bottom), three-candle patterns (three white soldiers / three black
crows, morning star / evening star), price-action signals (Bollinger Band
squeeze/widening, price stretching away from VWAP, price reverting back to
fill a VWAP gap), indicator crossovers (RSI crossing 60/40, MACD crossing
its signal line, Stochastic %K crossing %D, MA21 crossing MA50),
swing-high/swing-low (fractal) detection — confirmed _SWING_LOOKBACK
candles after they happen, since a swing point needs to see what came
after it to be identified — and, built on top of those swing points, the
first graph formation (double top / double bottom) plus two trend-
structure signals: bullish/bearish structure shift (industry term: CHoCH,
Change of Character — the first break signaling a reversal) and bullish/
bearish break of structure (BOS — a fresh high/low confirming the
resulting trend is continuing, not just starting). Larger formations
(head and shoulders, flags, triangles, triple top/bottom, etc.) aren't
built yet. Outcome tracking now lives in a separate, deliberately
decoupled module (prediction_tracker.py), not here.
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict, deque
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.exc import IntegrityError

from brokers.models import Candle
from db.models import EngineSetting, InstrumentActivity, PatternDefinition, SubscribedSymbol
from db.session import session_scope
from indicators import (
    TREND_LOOKBACK,
    AtrState,
    MacdState,
    RsiState,
    StochasticState,
    classify_trend,
    cross_intensity,
    crossed_above,
    crossed_below,
    trend_intensity,
    update_atr,
    update_macd,
    update_rsi,
    update_stochastic,
)

logger = logging.getLogger(__name__)

# Longest multi-candle pattern detected today (three soldiers/crows) — the
# rolling per-instrument buffer only needs to hold this many candles.
_LOOKBACK = 3

# Bollinger Bands: standard 20-period SMA +/- 2 standard deviations. Width is
# kept as a fraction of the middle band ((upper-lower)/middle) rather than an
# absolute price spread, so it's comparable across instruments/price levels
# and across a stock's own price drifting over time.
_BB_PERIOD = 20
_BB_STDDEV_MULT = 2.0
# BB width and VWAP distance are both classified "widening"/"narrowing" over
# the same TREND_LOOKBACK-candle window via the shared trend-consistency
# helper in indicators.py (see its docstring — a majority-of-steps check,
# not a two-point comparison).
_VWAP_GAP_LOOKBACK = TREND_LOOKBACK

# Crossover thresholds/periods. RSI 60/40 per instruction; MA21/MA50 pairing
# was already flagged as a planned pair before this module had any indicator
# state at all. MA_LONG_PERIOD doubles as the closes deque size — MA21 is
# just a shorter slice of the same window.
_RSI_CROSS_ABOVE = 60.0
_RSI_CROSS_BELOW = 40.0
_MA_SHORT_PERIOD = 21
_MA_LONG_PERIOD = 50

# Swing-high/swing-low (fractal) detection — the foundation graph formations
# (double top/bottom, HH-HL trend structure, head & shoulders, etc.) are
# built on top of. A candle qualifies once it's the highest/lowest across a
# window of _SWING_LOOKBACK candles on both sides of it — which means a
# swing point is only confirmed _SWING_LOOKBACK candles after it actually
# happened (need to see what came after to know it was a local extreme).
# No monotonicity requirement — the path to the extreme can zigzag freely,
# it just has to BE the extreme over the window. Standard fractals use 2 on
# each side (5-candle window); this started at 3 (7-candle) and widened to
# 5 (11-candle, close to "10 candles" per instruction) once real data
# showed 3 let through noise-level "swings" (intensities as low as 0.01%)
# that weren't genuinely significant. Still a starting point pending real
# calibration once the backtesting engine can check this against 2 years
# of data — just a better-informed one now that real data has been seen.
_SWING_LOOKBACK = 5

# Double top/bottom — the first actual graph formation, built on swing
# points. Fires as soon as the pattern's shape completes (two comparable
# tops/bottoms with a meaningfully deeper valley/peak between them), not
# waiting for a neckline break — that's its own separate signal (break of
# structure, not built yet) rather than part of this shape's definition.
# Both thresholds are starting points pending real calibration, same as
# every other threshold in this module.
_DOUBLE_SIMILARITY = 0.005  # the two tops/bottoms must be within 0.5% of each other
_DOUBLE_MIN_DEPTH = 0.003  # the valley/peak between them must be >= 0.3% deep
# Trade-planning levels for a confirmed double top/bottom — a small buffer
# above/below the pattern's own extreme for the stop, and the classic
# "measured move" target (the pattern's own height projected from the
# neckline). These are derived, not detected — no new instrument_activity
# columns, just pure functions callable wherever the same points list used
# for detection/intensity is already in hand.
_STOP_LOSS_BUFFER = 0.002

# Bullish/bearish structure shift — a Lower-Low-then-Higher-High-then-
# Higher-Low sequence (mirror for bearish) confirming a trend reversal.
# Each of the three legs (the LL's own drop, the HH's rise, the HL's rise)
# must individually clear this floor, so the shift is built from three
# genuinely meaningful moves, not just three points that happen to be
# directionally correct by a fraction of a percent — the same lesson real
# data taught for swing detection itself.
_STRUCTURE_MIN_MOVE = 0.003

InstrumentKey = Tuple[str, str, str]  # (symbol, exchange_segment, timeframe)


# --------------------------------------------------------------------- shape helpers

def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _range(c: Candle) -> float:
    return c.high - c.low


def _upper_wick(c: Candle) -> float:
    return c.high - max(c.open, c.close)


def _lower_wick(c: Candle) -> float:
    return min(c.open, c.close) - c.low


def _is_bullish(c: Candle) -> bool:
    return c.close > c.open


def _is_bearish(c: Candle) -> bool:
    return c.close < c.open


# --------------------------------------------------------------------- single-candle patterns

def detect_doji(c: Candle) -> bool:
    """Open and close are almost equal relative to the candle's own range —
    indecision. Body <= 10% of the high-low range."""
    rng = _range(c)
    return rng > 0 and _body(c) <= 0.1 * rng


def detect_hammer(c: Candle) -> bool:
    """Small body near the top of the range, a long lower wick (>= 2x the
    body), and little/no upper wick. Shape-only — no trend context (was this
    candle in a downtrend) is checked in this first pass."""
    rng = _range(c)
    body = _body(c)
    if rng <= 0 or body <= 0:
        return False
    return _lower_wick(c) >= 2 * body and _upper_wick(c) <= 0.1 * rng


def detect_shooting_star(c: Candle) -> bool:
    """Hammer's bearish mirror: small body near the bottom, a long upper
    wick, little/no lower wick."""
    rng = _range(c)
    body = _body(c)
    if rng <= 0 or body <= 0:
        return False
    return _upper_wick(c) >= 2 * body and _lower_wick(c) <= 0.1 * rng


SINGLE_CANDLE_PATTERNS: Dict[str, Callable[[Candle], bool]] = {
    "doji": detect_doji,
    "hammer": detect_hammer,
    "shooting_star": detect_shooting_star,
}


# --------------------------------------------------------------------- intensity

def hammer_intensity(c: Candle) -> float:
    """How strongly a candle qualifies as a hammer: lower wick / body. The
    detector's own qualifying threshold is 2.0 (wick >= 2x body), so
    anything that matched at all already scores >= 2 here — higher means a
    more pronounced hammer, exactly the "more than double is good, more and
    more increases intensity" rule as stated."""
    body = _body(c)
    return _lower_wick(c) / body if body > 0 else float("inf")


def shooting_star_intensity(c: Candle) -> float:
    """Shooting star's mirror of hammer_intensity: upper wick / body."""
    body = _body(c)
    return _upper_wick(c) / body if body > 0 else float("inf")


def doji_intensity(c: Candle) -> float:
    """How strongly a candle qualifies as a doji: range / body. The
    detector's qualifying threshold is body <= 10% of range, i.e. this ratio
    >= 10 — higher means the open/close sit closer together, a "more
    perfect" doji."""
    body = _body(c)
    rng = _range(c)
    return rng / body if body > 0 else float("inf")


SINGLE_CANDLE_INTENSITY: Dict[str, Callable[[Candle], float]] = {
    "doji": doji_intensity,
    "hammer": hammer_intensity,
    "shooting_star": shooting_star_intensity,
}


# --------------------------------------------------------------------- multi-candle patterns

def detect_three_white_soldiers(candles: List[Candle]) -> bool:
    """Three consecutive bullish candles, each closing higher than the last,
    each opening inside the previous candle's real body (steady climb, no
    big gaps), each closing near its own high (small upper wick)."""
    if len(candles) < 3:
        return False
    a, b, c = candles[-3], candles[-2], candles[-1]
    if not (_is_bullish(a) and _is_bullish(b) and _is_bullish(c)):
        return False
    if not (a.close < b.close < c.close):
        return False
    if not (a.open < b.open < a.close):
        return False
    if not (b.open < c.open < b.close):
        return False
    for candle in (a, b, c):
        body = _body(candle)
        if body <= 0 or _upper_wick(candle) > 0.3 * body:
            return False
    return True


def detect_three_black_crows(candles: List[Candle]) -> bool:
    """Three white soldiers' bearish mirror."""
    if len(candles) < 3:
        return False
    a, b, c = candles[-3], candles[-2], candles[-1]
    if not (_is_bearish(a) and _is_bearish(b) and _is_bearish(c)):
        return False
    if not (a.close > b.close > c.close):
        return False
    if not (a.open > b.open > a.close):
        return False
    if not (b.open > c.open > b.close):
        return False
    for candle in (a, b, c):
        body = _body(candle)
        if body <= 0 or _lower_wick(candle) > 0.3 * body:
            return False
    return True


def detect_morning_star(candles: List[Candle]) -> bool:
    """Large bearish candle, then a small-bodied "star" that gaps below its
    close (indecision), then a large bullish candle closing back above the
    midpoint of the first candle's body — a bullish reversal."""
    if len(candles) < 3:
        return False
    a, b, c = candles[-3], candles[-2], candles[-1]
    if not (_is_bearish(a) and _is_bullish(c)):
        return False
    body_a, body_c = _body(a), _body(c)
    if body_a <= 0 or body_c <= 0:
        return False
    if _body(b) > 0.3 * body_a:
        return False
    if max(b.open, b.close) >= a.close:
        return False
    midpoint_a = (a.open + a.close) / 2
    return c.close > midpoint_a


def detect_evening_star(candles: List[Candle]) -> bool:
    """Morning star's bearish mirror."""
    if len(candles) < 3:
        return False
    a, b, c = candles[-3], candles[-2], candles[-1]
    if not (_is_bullish(a) and _is_bearish(c)):
        return False
    body_a, body_c = _body(a), _body(c)
    if body_a <= 0 or body_c <= 0:
        return False
    if _body(b) > 0.3 * body_a:
        return False
    if min(b.open, b.close) <= a.close:
        return False
    midpoint_a = (a.open + a.close) / 2
    return c.close < midpoint_a


def morning_star_intensity(candles: List[Candle]) -> float:
    """How far past the first candle's midpoint the third candle's close
    penetrates, as a fraction of the first candle's half-body — 0 at the
    qualifying floor (the midpoint), 1 at the first candle's own open
    (fully reversing the entire first candle), higher beyond that."""
    a, _, c = candles[-3], candles[-2], candles[-1]
    half_body_a = _body(a) / 2
    if half_body_a <= 0:
        return float("inf")
    midpoint_a = (a.open + a.close) / 2
    return (c.close - midpoint_a) / half_body_a


def evening_star_intensity(candles: List[Candle]) -> float:
    """Morning star intensity's mirror."""
    a, _, c = candles[-3], candles[-2], candles[-1]
    half_body_a = _body(a) / 2
    if half_body_a <= 0:
        return float("inf")
    midpoint_a = (a.open + a.close) / 2
    return (midpoint_a - c.close) / half_body_a


THREE_CANDLE_INTENSITY: Dict[str, Callable[[List[Candle]], float]] = {
    "morning_star": morning_star_intensity,
    "evening_star": evening_star_intensity,
}


# --------------------------------------------------------------------- two-candle patterns

def detect_bullish_engulfing(candles: List[Candle]) -> bool:
    """Second candle's real body fully contains the first's — a bearish
    candle swallowed whole by a larger bullish one."""
    if len(candles) < 2:
        return False
    a, b = candles[-2], candles[-1]
    if not (_is_bearish(a) and _is_bullish(b)):
        return False
    return b.open <= a.close and b.close >= a.open and _body(b) > _body(a)


def detect_bearish_engulfing(candles: List[Candle]) -> bool:
    """Bullish engulfing's mirror."""
    if len(candles) < 2:
        return False
    a, b = candles[-2], candles[-1]
    if not (_is_bullish(a) and _is_bearish(b)):
        return False
    return b.open >= a.close and b.close <= a.open and _body(b) > _body(a)


def detect_piercing_line(candles: List[Candle]) -> bool:
    """Long bearish candle, then a bullish candle opening below the first's
    low and closing back above the midpoint of its body — but not fully
    engulfing it (that's bullish engulfing instead)."""
    if len(candles) < 2:
        return False
    a, b = candles[-2], candles[-1]
    if not (_is_bearish(a) and _is_bullish(b)) or _body(a) <= 0:
        return False
    midpoint = (a.open + a.close) / 2
    return b.open < a.low and midpoint < b.close < a.open


def detect_dark_cloud_cover(candles: List[Candle]) -> bool:
    """Piercing line's bearish mirror."""
    if len(candles) < 2:
        return False
    a, b = candles[-2], candles[-1]
    if not (_is_bullish(a) and _is_bearish(b)) or _body(a) <= 0:
        return False
    midpoint = (a.open + a.close) / 2
    return b.open > a.high and a.open < b.close < midpoint


def detect_tweezer_bottom(candles: List[Candle]) -> bool:
    """Two candles with matching lows — a bearish candle then a bullish one,
    both testing the same floor."""
    if len(candles) < 2:
        return False
    a, b = candles[-2], candles[-1]
    if not (_is_bearish(a) and _is_bullish(b)):
        return False
    tolerance = 0.1 * min(_range(a) or 0.01, _range(b) or 0.01)
    return abs(a.low - b.low) <= tolerance


def detect_tweezer_top(candles: List[Candle]) -> bool:
    """Tweezer bottom's mirror — matching highs."""
    if len(candles) < 2:
        return False
    a, b = candles[-2], candles[-1]
    if not (_is_bullish(a) and _is_bearish(b)):
        return False
    tolerance = 0.1 * min(_range(a) or 0.01, _range(b) or 0.01)
    return abs(a.high - b.high) <= tolerance


def engulfing_intensity(candles: List[Candle]) -> float:
    """How much bigger the engulfing candle's body is than the one it
    swallowed — the qualifying floor is just >1x (it must fully contain the
    other body), higher means more dominant."""
    a, b = candles[-2], candles[-1]
    body_a = _body(a)
    return _body(b) / body_a if body_a > 0 else float("inf")


def piercing_dark_cloud_intensity(candles: List[Candle]) -> float:
    """How far past the first candle's midpoint the second candle's close
    penetrates, as a fraction of the first candle's half-body — 0 at the
    midpoint (the qualifying floor), 1 at the boundary where it would
    instead become a full engulfing pattern."""
    a, b = candles[-2], candles[-1]
    half_body = _body(a) / 2
    if half_body <= 0:
        return float("inf")
    midpoint = (a.open + a.close) / 2
    return abs(b.close - midpoint) / half_body


def tweezer_bottom_intensity(candles: List[Candle]) -> float:
    """How closely the two lows match, relative to the candles' own range —
    smaller gap between the lows means a higher score."""
    a, b = candles[-2], candles[-1]
    diff = abs(a.low - b.low)
    avg_range = (_range(a) + _range(b)) / 2 or 0.01
    return avg_range / diff if diff > 0 else float("inf")


def tweezer_top_intensity(candles: List[Candle]) -> float:
    """Tweezer bottom's mirror — matching highs."""
    a, b = candles[-2], candles[-1]
    diff = abs(a.high - b.high)
    avg_range = (_range(a) + _range(b)) / 2 or 0.01
    return avg_range / diff if diff > 0 else float("inf")


TWO_CANDLE_INTENSITY: Dict[str, Callable[[List[Candle]], float]] = {
    "bullish_engulfing": engulfing_intensity,
    "bearish_engulfing": engulfing_intensity,
    "piercing_line": piercing_dark_cloud_intensity,
    "dark_cloud_cover": piercing_dark_cloud_intensity,
    "tweezer_bottom": tweezer_bottom_intensity,
    "tweezer_top": tweezer_top_intensity,
}


# Split by candle count so a caller (e.g. the timing benchmark script) can
# measure each category separately — on_candle_closed itself just iterates
# the merged MULTI_CANDLE_PATTERNS below, unaffected by this split.
TWO_CANDLE_PATTERNS: Dict[str, Callable[[List[Candle]], bool]] = {
    "bullish_engulfing": detect_bullish_engulfing,
    "bearish_engulfing": detect_bearish_engulfing,
    "piercing_line": detect_piercing_line,
    "dark_cloud_cover": detect_dark_cloud_cover,
    "tweezer_bottom": detect_tweezer_bottom,
    "tweezer_top": detect_tweezer_top,
}

THREE_CANDLE_PATTERNS: Dict[str, Callable[[List[Candle]], bool]] = {
    "three_white_soldiers": detect_three_white_soldiers,
    "three_black_crows": detect_three_black_crows,
    "morning_star": detect_morning_star,
    "evening_star": detect_evening_star,
}

MULTI_CANDLE_PATTERNS: Dict[str, Callable[[List[Candle]], bool]] = {
    **TWO_CANDLE_PATTERNS,
    **THREE_CANDLE_PATTERNS,
}

# Intensity formulas for multi-candle patterns that have one defined —
# three_white_soldiers/three_black_crows don't have one yet, so they're
# absent here rather than guessed at (on_candle_closed treats a missing
# entry the same as detect-only-no-intensity: stored as NULL).
MULTI_CANDLE_INTENSITY: Dict[str, Callable[[List[Candle]], float]] = {
    **TWO_CANDLE_INTENSITY,
    **THREE_CANDLE_INTENSITY,
}

# --------------------------------------------------------------------- swing high/low (structure)

def detect_swing_high(candles: List[Candle], lookback: int = _SWING_LOOKBACK) -> bool:
    """candles is a window of exactly 2*lookback+1 candles, most recent
    last. True when the MIDDLE candle's high is the highest in the whole
    window — i.e. this confirms a swing point that happened `lookback`
    candles ago, not the latest candle. `lookback` defaults to the module
    constant but ActivityEngine passes its own (possibly settings-
    overridden) value instead — see EngineSetting/load_engine_settings."""
    if len(candles) < 2 * lookback + 1:
        return False
    mid = candles[lookback]
    return mid.high == max(c.high for c in candles)


def detect_swing_low(candles: List[Candle], lookback: int = _SWING_LOOKBACK) -> bool:
    """Swing high's mirror, on lows."""
    if len(candles) < 2 * lookback + 1:
        return False
    mid = candles[lookback]
    return mid.low == min(c.low for c in candles)


def swing_high_intensity(candles: List[Candle], lookback: int = _SWING_LOOKBACK) -> float:
    """How far the swing candle's high sits above the average of every
    other high in the window, as a fraction of that average — 0 at the
    qualifying floor (a flat tie with the window's other highs), growing
    for a sharper, more pronounced peak."""
    mid = candles[lookback]
    others = [c.high for c in candles if c is not mid]
    avg_others = statistics.fmean(others)
    return (mid.high - avg_others) / avg_others if avg_others else float("inf")


def swing_low_intensity(candles: List[Candle], lookback: int = _SWING_LOOKBACK) -> float:
    """Swing high intensity's mirror, on lows."""
    mid = candles[lookback]
    others = [c.low for c in candles if c is not mid]
    avg_others = statistics.fmean(others)
    return (avg_others - mid.low) / avg_others if avg_others else float("inf")


# --------------------------------------------------------------------- graph formations (double top/bottom)

class SwingPoint:
    """One confirmed swing point, in the chronological sequence formation
    detectors read from — kind is "high" or "low", price is the swing
    candle's own high/low (matching kind), candle is that same swing
    candle (for the formation activity's own ts/OHLC when it fires)."""

    __slots__ = ("kind", "price", "candle")

    def __init__(self, kind: str, price: float, candle: Candle):
        self.kind = kind
        self.price = price
        self.candle = candle


def detect_double_top(points: List[SwingPoint]) -> bool:
    """points: the last 3 confirmed swing points, chronological, most
    recent last. True when they form high -> low -> high, the two highs
    are within _DOUBLE_SIMILARITY of each other, and the low between them
    sits at least _DOUBLE_MIN_DEPTH below their average — a real "M" shape,
    not just three nearly-flat points."""
    if len(points) < 3:
        return False
    a, b, c = points[-3], points[-2], points[-1]
    if not (a.kind == "high" and b.kind == "low" and c.kind == "high"):
        return False
    avg_tops = (a.price + c.price) / 2
    if avg_tops <= 0:
        return False
    if abs(a.price - c.price) / avg_tops > _DOUBLE_SIMILARITY:
        return False
    return (avg_tops - b.price) / avg_tops >= _DOUBLE_MIN_DEPTH


def detect_double_bottom(points: List[SwingPoint]) -> bool:
    """Double top's mirror: low -> high -> low, a "W" shape."""
    if len(points) < 3:
        return False
    a, b, c = points[-3], points[-2], points[-1]
    if not (a.kind == "low" and b.kind == "high" and c.kind == "low"):
        return False
    avg_bottoms = (a.price + c.price) / 2
    if avg_bottoms <= 0:
        return False
    if abs(a.price - c.price) / avg_bottoms > _DOUBLE_SIMILARITY:
        return False
    return (b.price - avg_bottoms) / avg_bottoms >= _DOUBLE_MIN_DEPTH


def double_top_intensity(points: List[SwingPoint]) -> float:
    """The valley's depth relative to the two tops, as a multiple of the
    qualifying floor (_DOUBLE_MIN_DEPTH) — 1.0 right at the floor, higher
    for a deeper, more pronounced "M"."""
    a, b, c = points[-3], points[-2], points[-1]
    avg_tops = (a.price + c.price) / 2
    depth = (avg_tops - b.price) / avg_tops if avg_tops else float("inf")
    return depth / _DOUBLE_MIN_DEPTH if _DOUBLE_MIN_DEPTH else float("inf")


def double_bottom_intensity(points: List[SwingPoint]) -> float:
    """Double top intensity's mirror."""
    a, b, c = points[-3], points[-2], points[-1]
    avg_bottoms = (a.price + c.price) / 2
    depth = (b.price - avg_bottoms) / avg_bottoms if avg_bottoms else float("inf")
    return depth / _DOUBLE_MIN_DEPTH if _DOUBLE_MIN_DEPTH else float("inf")


def double_top_neckline(points: List[SwingPoint]) -> float:
    """The valley between the two tops — the level price needs to close
    below for a breakout to actually confirm this pattern (that
    confirmation itself isn't checked here, see the module docstring's
    note on break-of-structure being separate, deferred work)."""
    return points[-2].price


def double_top_stop_loss(points: List[SwingPoint]) -> float:
    """A small buffer above the higher of the two tops."""
    a, c = points[-3], points[-1]
    return max(a.price, c.price) * (1 + _STOP_LOSS_BUFFER)


def double_top_target(points: List[SwingPoint]) -> float:
    """Classic measured-move target: the pattern's own height (average top
    to neckline) projected downward from the neckline."""
    a, b, c = points[-3], points[-2], points[-1]
    height = (a.price + c.price) / 2 - b.price
    return b.price - height


def double_bottom_neckline(points: List[SwingPoint]) -> float:
    """Double top neckline's mirror — the peak between the two bottoms."""
    return points[-2].price


def double_bottom_stop_loss(points: List[SwingPoint]) -> float:
    """Double top stop-loss's mirror — a small buffer below the lower of
    the two bottoms."""
    a, c = points[-3], points[-1]
    return min(a.price, c.price) * (1 - _STOP_LOSS_BUFFER)


def double_bottom_target(points: List[SwingPoint]) -> float:
    """Double top target's mirror — the pattern's own height projected
    upward from the neckline."""
    a, b, c = points[-3], points[-2], points[-1]
    height = b.price - (a.price + c.price) / 2
    return b.price + height


# --------------------------------------------------------------------- graph formations (triple top/bottom)

def detect_triple_top(points: List[SwingPoint]) -> bool:
    """points: the last 5 confirmed swing points, chronological, most
    recent last. True when they form high -> low -> high -> low -> high,
    all three tops sit within _DOUBLE_SIMILARITY of their own average (not
    just pairwise — the whole top-to-top spread has to be tight), and the
    *average* of the two valleys' depths (each measured against the pair
    of tops either side of it) clears _DOUBLE_MIN_DEPTH.

    Reuses double top/bottom's own similarity/depth thresholds rather than
    introducing new ones. The two valleys' depths are averaged rather than
    each required to independently clear the floor — same reasoning as
    bullish/bearish_structure_shift's own average-of-legs gate (real
    reversal shapes routinely have one shallower pullback and one deeper
    one; requiring both to independently qualify rejects genuine triple
    tops for no good reason), applied here up front rather than
    discovered the hard way via a live-data investigation."""
    if len(points) < 5:
        return False
    a, b, c, d, e = points[-5:]
    if not (a.kind == "high" and b.kind == "low" and c.kind == "high" and d.kind == "low" and e.kind == "high"):
        return False
    avg_top = (a.price + c.price + e.price) / 3
    if avg_top <= 0:
        return False
    if max(a.price, c.price, e.price) - min(a.price, c.price, e.price) > avg_top * _DOUBLE_SIMILARITY:
        return False
    avg_ac, avg_ce = (a.price + c.price) / 2, (c.price + e.price) / 2
    if avg_ac <= 0 or avg_ce <= 0:
        return False
    depth1 = (avg_ac - b.price) / avg_ac
    depth2 = (avg_ce - d.price) / avg_ce
    if depth1 <= 0 or depth2 <= 0:  # each valley must at least be a real pullback, not a flat tie
        return False
    return (depth1 + depth2) / 2 >= _DOUBLE_MIN_DEPTH


def detect_triple_bottom(points: List[SwingPoint]) -> bool:
    """Triple top's mirror: low -> high -> low -> high -> low."""
    if len(points) < 5:
        return False
    a, b, c, d, e = points[-5:]
    if not (a.kind == "low" and b.kind == "high" and c.kind == "low" and d.kind == "high" and e.kind == "low"):
        return False
    avg_bottom = (a.price + c.price + e.price) / 3
    if avg_bottom <= 0:
        return False
    if max(a.price, c.price, e.price) - min(a.price, c.price, e.price) > avg_bottom * _DOUBLE_SIMILARITY:
        return False
    avg_ac, avg_ce = (a.price + c.price) / 2, (c.price + e.price) / 2
    if avg_ac <= 0 or avg_ce <= 0:
        return False
    height1 = (b.price - avg_ac) / avg_ac
    height2 = (d.price - avg_ce) / avg_ce
    if height1 <= 0 or height2 <= 0:
        return False
    return (height1 + height2) / 2 >= _DOUBLE_MIN_DEPTH


def triple_top_intensity(points: List[SwingPoint]) -> float:
    """Average of the two valleys' depths, as a multiple of the qualifying
    floor — same style as double_top_intensity."""
    a, b, c, d, e = points[-5:]
    avg_ac, avg_ce = (a.price + c.price) / 2, (c.price + e.price) / 2
    depth1 = (avg_ac - b.price) / avg_ac if avg_ac else float("inf")
    depth2 = (avg_ce - d.price) / avg_ce if avg_ce else float("inf")
    avg_depth = (depth1 + depth2) / 2
    return avg_depth / _DOUBLE_MIN_DEPTH if _DOUBLE_MIN_DEPTH else float("inf")


def triple_bottom_intensity(points: List[SwingPoint]) -> float:
    """Triple top intensity's mirror."""
    a, b, c, d, e = points[-5:]
    avg_ac, avg_ce = (a.price + c.price) / 2, (c.price + e.price) / 2
    height1 = (b.price - avg_ac) / avg_ac if avg_ac else float("inf")
    height2 = (d.price - avg_ce) / avg_ce if avg_ce else float("inf")
    avg_height = (height1 + height2) / 2
    return avg_height / _DOUBLE_MIN_DEPTH if _DOUBLE_MIN_DEPTH else float("inf")


def triple_top_neckline(points: List[SwingPoint]) -> float:
    """The support line drawn through the two valleys — their average."""
    a, b, c, d, e = points[-5:]
    return (b.price + d.price) / 2


def triple_top_stop_loss(points: List[SwingPoint]) -> float:
    """A small buffer above the highest of the three tops."""
    a, b, c, d, e = points[-5:]
    return max(a.price, c.price, e.price) * (1 + _STOP_LOSS_BUFFER)


def triple_top_target(points: List[SwingPoint]) -> float:
    """Measured-move target: the pattern's own height (average top to
    neckline) projected downward from the neckline."""
    a, b, c, d, e = points[-5:]
    neckline = (b.price + d.price) / 2
    avg_top = (a.price + c.price + e.price) / 3
    return neckline - (avg_top - neckline)


def triple_bottom_neckline(points: List[SwingPoint]) -> float:
    """Triple top neckline's mirror — the resistance line through the two peaks."""
    a, b, c, d, e = points[-5:]
    return (b.price + d.price) / 2


def triple_bottom_stop_loss(points: List[SwingPoint]) -> float:
    """A small buffer below the lowest of the three bottoms."""
    a, b, c, d, e = points[-5:]
    return min(a.price, c.price, e.price) * (1 - _STOP_LOSS_BUFFER)


def triple_bottom_target(points: List[SwingPoint]) -> float:
    """Triple top target's mirror — the pattern's own height projected
    upward from the neckline."""
    a, b, c, d, e = points[-5:]
    neckline = (b.price + d.price) / 2
    avg_bottom = (a.price + c.price + e.price) / 3
    return neckline + (neckline - avg_bottom)


# Derived trade-planning levels for every graph formation, keyed by the
# formation's own activity name — a plain lookup so on_candle_closed can
# resolve the right (neckline, stop_loss, target) functions for whichever
# formation just fired without an if/elif chain that grows with every new
# formation added.
FORMATION_LEVEL_FUNCS: Dict[str, Tuple[Callable, Callable, Callable]] = {
    "double_top": (double_top_neckline, double_top_stop_loss, double_top_target),
    "double_bottom": (double_bottom_neckline, double_bottom_stop_loss, double_bottom_target),
    "triple_top": (triple_top_neckline, triple_top_stop_loss, triple_top_target),
    "triple_bottom": (triple_bottom_neckline, triple_bottom_stop_loss, triple_bottom_target),
}


# --------------------------------------------------------------------- structure shift (HH-HL / LH-LL)

def _pct_move(from_price: float, to_price: float) -> float:
    return (to_price - from_price) / from_price if from_price else float("inf")


def detect_bullish_structure_shift(lows: List[SwingPoint], highs: List[SwingPoint]) -> bool:
    """lows: last 3 confirmed swing lows chronological, highs: last 2
    confirmed swing highs chronological (tracked as separate type-only
    sequences, not one interleaved list — makes each leg's comparison a
    plain "is the newer one bigger" check). True for a genuine Lower-Low,
    then Higher-High, then Higher-Low sequence — a bearish-to-bullish
    trend structure shift — where the three points occur in that
    chronological order, each leg moves in the right direction (a real LL/
    HH/HL, not a flat tie), and the *average* of the three legs' move
    sizes clears _STRUCTURE_MIN_MOVE.

    Deliberately an average, not "every leg individually clears the
    floor" — real data showed genuine reversals routinely have one
    dominant leg and two smaller confirming ones (e.g. a decisive HH with
    a barely-there LL), and requiring each leg to independently be
    significant rejected those outright. The average still keeps pure
    noise out (a reversal where every leg is tiny won't average above the
    floor either), just doesn't let one weak leg veto an otherwise real
    move."""
    if len(lows) < 3 or len(highs) < 2:
        return False
    l_prev, l_ll, l_hl = lows[-3], lows[-2], lows[-1]
    h_prev, h_hh = highs[-2], highs[-1]
    ll_move = _pct_move(l_prev.price, l_ll.price)
    hh_move = _pct_move(h_prev.price, h_hh.price)
    hl_move = _pct_move(l_ll.price, l_hl.price)
    if ll_move >= 0 or hh_move <= 0 or hl_move <= 0:  # each leg must at least point the right way
        return False
    if not (l_ll.candle.timestamp < h_hh.candle.timestamp < l_hl.candle.timestamp):
        return False
    avg_move = (abs(ll_move) + hh_move + hl_move) / 3
    return avg_move >= _STRUCTURE_MIN_MOVE


def detect_bearish_structure_shift(highs: List[SwingPoint], lows: List[SwingPoint]) -> bool:
    """Bullish shift's mirror: Higher-High, then Lower-Low, then Lower-High."""
    if len(highs) < 3 or len(lows) < 2:
        return False
    h_prev, h_hh, h_lh = highs[-3], highs[-2], highs[-1]
    l_prev, l_ll = lows[-2], lows[-1]
    hh_move = _pct_move(h_prev.price, h_hh.price)
    ll_move = _pct_move(l_prev.price, l_ll.price)
    lh_move = _pct_move(h_hh.price, h_lh.price)
    if hh_move <= 0 or ll_move >= 0 or lh_move >= 0:
        return False
    if not (h_hh.candle.timestamp < l_ll.candle.timestamp < h_lh.candle.timestamp):
        return False
    avg_move = (hh_move + abs(ll_move) + abs(lh_move)) / 3
    return avg_move >= _STRUCTURE_MIN_MOVE


def bullish_structure_shift_intensity(lows: List[SwingPoint], highs: List[SwingPoint]) -> float:
    """Average of the three legs' move sizes, as a multiple of the
    qualifying floor — 1.0 right at the floor, higher for a more decisive
    reversal (each leg moving well past the minimum)."""
    l_prev, l_ll, l_hl = lows[-3], lows[-2], lows[-1]
    h_prev, h_hh = highs[-2], highs[-1]
    avg_move = (
        abs(_pct_move(l_prev.price, l_ll.price))
        + _pct_move(h_prev.price, h_hh.price)
        + _pct_move(l_ll.price, l_hl.price)
    ) / 3
    return avg_move / _STRUCTURE_MIN_MOVE if _STRUCTURE_MIN_MOVE else float("inf")


def bearish_structure_shift_intensity(highs: List[SwingPoint], lows: List[SwingPoint]) -> float:
    """Bullish shift intensity's mirror."""
    h_prev, h_hh, h_lh = highs[-3], highs[-2], highs[-1]
    l_prev, l_ll = lows[-2], lows[-1]
    avg_move = (
        _pct_move(h_prev.price, h_hh.price)
        + abs(_pct_move(l_prev.price, l_ll.price))
        + abs(_pct_move(h_hh.price, h_lh.price))
    ) / 3
    return avg_move / _STRUCTURE_MIN_MOVE if _STRUCTURE_MIN_MOVE else float("inf")


# Break of Structure (BOS) — the wider industry's name for trend
# *continuation*, distinct from bullish/bearish_structure_shift above
# (which is what the industry calls CHoCH, Change of Character: the
# *first* break signaling a reversal). Where a CHoCH completes on
# LL-HH-HL (bullish) — the reversal into an uptrend — a BOS completes one
# step later: a fresh Higher-High arriving *after* an already-confirmed
# Higher-Low, i.e. the structure was already HL going into this new HH,
# confirming the (established or just-reversed-into) uptrend is
# continuing rather than marking its start. Same average-of-legs gate as
# structure shift, same reasoning (see detect_bullish_structure_shift's
# own docstring) — just two legs instead of three, since BOS has no
# "initial LL" leg of its own to include.
_BOS_MIN_MOVE = 0.003


def detect_bullish_break_of_structure(lows: List[SwingPoint], highs: List[SwingPoint]) -> bool:
    """lows/highs: the same last-3-confirmed, type-only sequences bullish/
    bearish_structure_shift already read from. True when the latest
    confirmed swing high is a genuine Higher-High arriving after the
    latest confirmed swing low was itself a genuine Higher-Low (in that
    chronological order) — i.e. an uptrend making a fresh new high, not
    the initial reversal into one."""
    if len(highs) < 2 or len(lows) < 2:
        return False
    h_prev, h_hh = highs[-2], highs[-1]
    l_prev, l_hl = lows[-2], lows[-1]
    hh_move = _pct_move(h_prev.price, h_hh.price)
    hl_move = _pct_move(l_prev.price, l_hl.price)
    if hh_move <= 0 or hl_move <= 0:  # each leg must at least point the right way
        return False
    if not (l_hl.candle.timestamp < h_hh.candle.timestamp):
        return False
    avg_move = (hh_move + hl_move) / 2
    return avg_move >= _BOS_MIN_MOVE


def detect_bearish_break_of_structure(highs: List[SwingPoint], lows: List[SwingPoint]) -> bool:
    """Bullish BOS's mirror: a fresh Lower-Low arriving after the latest
    confirmed swing high was itself a genuine Lower-High."""
    if len(lows) < 2 or len(highs) < 2:
        return False
    l_prev, l_ll = lows[-2], lows[-1]
    h_prev, h_lh = highs[-2], highs[-1]
    ll_move = _pct_move(l_prev.price, l_ll.price)
    lh_move = _pct_move(h_prev.price, h_lh.price)
    if ll_move >= 0 or lh_move >= 0:
        return False
    if not (h_lh.candle.timestamp < l_ll.candle.timestamp):
        return False
    avg_move = (abs(ll_move) + abs(lh_move)) / 2
    return avg_move >= _BOS_MIN_MOVE


def bullish_break_of_structure_intensity(lows: List[SwingPoint], highs: List[SwingPoint]) -> float:
    """Average of the two legs' move sizes, as a multiple of the
    qualifying floor — same style as bullish_structure_shift_intensity."""
    h_prev, h_hh = highs[-2], highs[-1]
    l_prev, l_hl = lows[-2], lows[-1]
    avg_move = (_pct_move(h_prev.price, h_hh.price) + _pct_move(l_prev.price, l_hl.price)) / 2
    return avg_move / _BOS_MIN_MOVE if _BOS_MIN_MOVE else float("inf")


def bearish_break_of_structure_intensity(highs: List[SwingPoint], lows: List[SwingPoint]) -> float:
    """Bullish BOS intensity's mirror."""
    l_prev, l_ll = lows[-2], lows[-1]
    h_prev, h_lh = highs[-2], highs[-1]
    avg_move = (abs(_pct_move(l_prev.price, l_ll.price)) + abs(_pct_move(h_prev.price, h_lh.price))) / 2
    return avg_move / _BOS_MIN_MOVE if _BOS_MIN_MOVE else float("inf")


# --------------------------------------------------------------------- price action (BB / VWAP)

class BollingerBands:
    __slots__ = ("middle", "upper", "lower", "width")

    def __init__(self, middle: float, upper: float, lower: float, width: float):
        self.middle = middle
        self.upper = upper
        self.lower = lower
        self.width = width


def compute_bollinger(closes: Sequence[float], stddev_mult: float = _BB_STDDEV_MULT) -> Optional[BollingerBands]:
    """None until a full _BB_PERIOD window of closes is available."""
    if len(closes) < _BB_PERIOD:
        return None
    mean = statistics.fmean(closes)
    stdev = statistics.pstdev(closes)
    upper = mean + stddev_mult * stdev
    lower = mean - stddev_mult * stdev
    width = (upper - lower) / mean if mean else 0.0
    return BollingerBands(middle=mean, upper=upper, lower=lower, width=width)


def detect_bb_squeeze(widths: Sequence[float]) -> bool:
    """True when the last TREND_LOOKBACK width readings are consistently
    narrowing (a strong majority of consecutive steps decrease) — volatility
    compression, the classic setup ahead of a breakout. Uses the same
    trend-consistency check as VWAP distance, see indicators.classify_trend."""
    if len(widths) < TREND_LOOKBACK:
        return False
    return classify_trend(widths).direction == "narrowing"


def detect_bb_widening(widths: Sequence[float]) -> bool:
    """Squeeze's mirror: the last TREND_LOOKBACK width readings are
    consistently widening."""
    if len(widths) < TREND_LOOKBACK:
        return False
    return classify_trend(widths).direction == "widening"


def bb_squeeze_intensity(widths: Sequence[float]) -> float:
    """How consistently the window narrowed, relative to the qualifying
    agreement ratio — 1.0 right at the ratio, growing toward 1/TREND_RATIO
    as every single step in the window narrows."""
    return trend_intensity(classify_trend(widths))


def bb_widening_intensity(widths: Sequence[float]) -> float:
    """Squeeze intensity's mirror."""
    return trend_intensity(classify_trend(widths))


def detect_price_vwap_divergence(gaps: Sequence[float]) -> bool:
    """gaps are signed (close - vwap) / vwap readings, most recent last.
    Fires when the last TREND_LOOKBACK gap readings are consistently
    widening (price stretching further from vwap), via the same
    trend-consistency check used for BB width."""
    if len(gaps) < TREND_LOOKBACK:
        return False
    return classify_trend(gaps).direction == "widening"


def detect_vwap_gap_fill(gaps: Sequence[float]) -> bool:
    """Divergence's mirror: the last TREND_LOOKBACK gap readings are
    consistently narrowing — price reverting back toward vwap."""
    if len(gaps) < TREND_LOOKBACK:
        return False
    return classify_trend(gaps).direction == "narrowing"


def vwap_divergence_intensity(gaps: Sequence[float]) -> float:
    """How consistently the gap widened, relative to the qualifying ratio."""
    return trend_intensity(classify_trend(gaps))


def vwap_gap_fill_intensity(gaps: Sequence[float]) -> float:
    """Divergence intensity's mirror."""
    return trend_intensity(classify_trend(gaps))


PRICE_ACTION_INTENSITY: Dict[str, Callable[[Sequence[float]], float]] = {
    "bb_squeeze": bb_squeeze_intensity,
    "bb_widening": bb_widening_intensity,
    "price_vwap_divergence": vwap_divergence_intensity,
    "vwap_gap_fill": vwap_gap_fill_intensity,
}


class _VwapState:
    __slots__ = ("day", "cum_pv", "cum_vol")

    def __init__(self, day: date):
        self.day = day
        self.cum_pv = 0.0
        self.cum_vol = 0.0


# The data-driven catalog (PatternDefinition rows) — kept next to the
# detector dicts above so a new pattern's code/kind/description is added in
# the same place as its detection function, not a separate file to remember.
PATTERN_CATALOG = [
    ("doji", "single_candle", "Open and close almost equal (body <= 10% of the high-low range) — indecision"),
    ("hammer", "single_candle", "Small body near the top, long lower wick (>= 2x body), little/no upper wick"),
    ("shooting_star", "single_candle", "Small body near the bottom, long upper wick (>= 2x body), little/no lower wick"),
    ("bullish_engulfing", "multi_candle", "A bearish candle's real body fully swallowed by a larger bullish candle's body"),
    ("bearish_engulfing", "multi_candle", "A bullish candle's real body fully swallowed by a larger bearish candle's body"),
    ("piercing_line", "multi_candle", "Bearish candle, then a bullish candle opening below its low and closing back above its midpoint"),
    ("dark_cloud_cover", "multi_candle", "Bullish candle, then a bearish candle opening above its high and closing back below its midpoint"),
    ("tweezer_bottom", "multi_candle", "A bearish then a bullish candle with matching lows"),
    ("tweezer_top", "multi_candle", "A bullish then a bearish candle with matching highs"),
    ("three_white_soldiers", "multi_candle", "Three consecutive bullish candles, each closing higher, opening within the prior body"),
    ("three_black_crows", "multi_candle", "Three consecutive bearish candles, each closing lower, opening within the prior body"),
    ("morning_star", "multi_candle", "Bearish candle, then a small-bodied star gapping below its close, then a bullish candle closing back above the first candle's midpoint"),
    ("evening_star", "multi_candle", "Bullish candle, then a small-bodied star gapping above its close, then a bearish candle closing back below the first candle's midpoint"),
    ("bb_squeeze", "price_action", "Bollinger Band width has been consistently narrowing over the last 20 candles — volatility compression, often precedes a breakout"),
    ("bb_widening", "price_action", "Bollinger Band width has been consistently widening over the last 20 candles — volatility expansion, typically during a strong directional move"),
    ("price_vwap_divergence", "price_action", "Price's distance from VWAP has been consistently widening over the last 20 candles — stretching away from vwap"),
    ("vwap_gap_fill", "price_action", "Price's distance from VWAP has been consistently narrowing over the last 20 candles — reverting back toward vwap"),
    ("rsi_cross_above_60", "indicator", "RSI(14) crosses above 60 — momentum turning bullish"),
    ("rsi_cross_below_40", "indicator", "RSI(14) crosses below 40 — momentum turning bearish"),
    ("macd_bullish_cross", "indicator", "MACD line crosses above its signal line — bullish momentum shift"),
    ("macd_bearish_cross", "indicator", "MACD line crosses below its signal line — bearish momentum shift"),
    ("ma_golden_cross", "indicator", "MA21 crosses above MA50 — short-term trend turning bullish relative to the longer-term trend"),
    ("ma_death_cross", "indicator", "MA21 crosses below MA50 — short-term trend turning bearish relative to the longer-term trend"),
    ("stoch_bullish_cross", "indicator", "Stochastic %K crosses above %D — short-term momentum turning bullish"),
    ("stoch_bearish_cross", "indicator", "Stochastic %K crosses below %D — short-term momentum turning bearish"),
    ("swing_high", "structure", "A confirmed local price peak — the highest high across a window of candles on both sides of it"),
    ("swing_low", "structure", "A confirmed local price trough — the lowest low across a window of candles on both sides of it"),
    ("double_top", "graph_formation", "Two comparable swing highs with a meaningfully lower swing low between them — a classic bearish reversal shape"),
    ("double_bottom", "graph_formation", "Two comparable swing lows with a meaningfully higher swing high between them — a classic bullish reversal shape"),
    ("triple_top", "graph_formation", "Three comparable swing highs with two meaningfully lower swing lows between them — a stronger bearish reversal shape than a double top"),
    ("triple_bottom", "graph_formation", "Three comparable swing lows with two meaningfully higher swing highs between them — a stronger bullish reversal shape than a double bottom"),
    ("bullish_structure_shift", "structure", "Lower Low, then Higher High, then Higher Low — trend structure shifting from bearish to bullish (CHoCH)"),
    ("bearish_structure_shift", "structure", "Higher High, then Lower Low, then Lower High — trend structure shifting from bullish to bearish (CHoCH)"),
    ("bullish_break_of_structure", "structure", "A fresh Higher-High arriving after an already-confirmed Higher-Low — an uptrend continuing to make new highs (BOS)"),
    ("bearish_break_of_structure", "structure", "A fresh Lower-Low arriving after an already-confirmed Lower-High — a downtrend continuing to make new lows (BOS)"),
]


def seed_pattern_definitions(session_factory) -> None:
    """Idempotent upsert of PATTERN_CATALOG into pattern_definitions — call
    once at app startup. Safe to call every time (existing rows are just
    updated in place, matching the same upsert style used elsewhere in this
    codebase, e.g. feed/candle_persistence.py)."""
    with session_scope(session_factory) as session:
        for code, kind, description in PATTERN_CATALOG:
            row = session.query(PatternDefinition).filter_by(code=code).one_or_none()
            if row is None:
                row = PatternDefinition(code=code, kind=kind, description=description)
                session.add(row)
            else:
                row.kind = kind
                row.description = description
                row.active = True


# Keys read from engine_settings, alongside the module-constant default
# used whenever a key has no row yet (nothing has been calibrated). This
# list is what ActivityEngine.__init__ actually resolves at construction
# time — see load_engine_settings(). Extend this the same way each time a
# new tunable parameter is added, matching the "same thing for intensity
# of all patterns" plan (this session, 2026-09-13) — swing_lookback is the
# first one wired up; the rest of this module's thresholds move onto this
# same mechanism incrementally, not all at once.
ENGINE_SETTING_DEFAULTS: Dict[str, float] = {
    "swing_lookback": float(_SWING_LOOKBACK),
}


def load_engine_settings(session_factory) -> Dict[str, float]:
    """Reads every stored override from engine_settings. A key absent from
    the returned dict simply hasn't been calibrated yet — callers combine
    this with ENGINE_SETTING_DEFAULTS (or their own hardcoded default) to
    get an actual value, so the table can be completely empty and nothing
    behaves any differently than before this existed."""
    with session_scope(session_factory) as session:
        rows = session.query(EngineSetting).all()
        return {row.key: float(row.value) for row in rows}


# --------------------------------------------------------------------- engine

class ActivityEngine:
    """Call on_candle_closed(symbol, exchange_segment, candle) for every
    finalized 1-/3-/5-min candle (live or backfilled) — same entry point
    style as CandleAggregator's on_candle_closed callback. Keeps a small
    rolling buffer per (symbol, exchange_segment, timeframe) in memory for
    multi-candle pattern lookback, so this never needs its own DB read on
    the hot path.

    Detected activities are buffered in memory (a plain list of dicts —
    cheap, and trivially handed to pandas via to_dataframe() if a caller
    wants it) rather than written to instrument_activity as they're found.
    Nothing hits the DB until flush() is called explicitly — once per
    trading day in production (hooked into the existing EOD timer
    alongside CandleAggregator.flush_all), or once at the end of a whole
    backtest/calibration run across many days if a caller wants to buffer
    that much before ever touching the DB. The point is db calls scale
    with flushes, not with detections — a session close to a thousand
    activities is one bulk insert, not a thousand round-trips.
    """

    def __init__(self, session_factory):
        self.session_factory = session_factory
        settings = load_engine_settings(session_factory)
        # swing_lookback is the first parameter wired to engine_settings —
        # falls back to the module default (ENGINE_SETTING_DEFAULTS) when
        # nothing's been calibrated yet
        self.swing_lookback = int(settings.get("swing_lookback", ENGINE_SETTING_DEFAULTS["swing_lookback"]))
        self._recent: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=_LOOKBACK))
        self._instrument_ids: Dict[Tuple[str, str], int] = {}
        self._buffer: List[dict] = []
        # price-action state, one series per (symbol, exchange_segment, timeframe).
        # _closes holds _MA_LONG_PERIOD candles (the longest window any
        # indicator here needs) — BB(20) reads only the tail slice it needs.
        self._closes: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=_MA_LONG_PERIOD))
        self._bb_widths: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=TREND_LOOKBACK))
        self._vwap_state: Dict[InstrumentKey, _VwapState] = {}
        self._vwap_gaps: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=_VWAP_GAP_LOOKBACK))
        # indicator-crossover state
        self._rsi_state: Dict[InstrumentKey, RsiState] = defaultdict(RsiState)
        self._macd_state: Dict[InstrumentKey, MacdState] = defaultdict(MacdState)
        self._stoch_state: Dict[InstrumentKey, StochasticState] = defaultdict(StochasticState)
        self._prev_rsi: Dict[InstrumentKey, float] = {}
        self._prev_macd: Dict[InstrumentKey, Tuple[float, float]] = {}
        self._prev_stoch: Dict[InstrumentKey, Tuple[float, float]] = {}
        self._prev_ma: Dict[InstrumentKey, Tuple[float, float]] = {}
        # swing-high/swing-low (structure) — rolling 2*swing_lookback+1 window
        self._swing_window: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=2 * self.swing_lookback + 1))
        # last 5 confirmed swing points, chronological — graph formations
        # read off this (double/triple top/bottom need 3/5 respectively;
        # keeping 5 means points_list always has enough for whichever
        # formation's check needs the most, double top/bottom just read
        # the tail 3 of it)
        self._swing_points: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=5))
        # confirmed swing highs/lows tracked as separate type-only sequences
        # (not interleaved) — trend structure shift reads off these instead,
        # since it only ever compares a point against the prior one of its
        # own type (is this high bigger than the last high? etc.)
        self._recent_highs: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=3))
        self._recent_lows: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=3))
        # ATR — not used by any detector's threshold yet (see ATR_PERIOD's
        # own docstring in indicators.py), but tracked here per instrument/
        # timeframe so PredictionTracker can read the latest value via
        # get_atr() for its own crossover stop/target sizing, without this
        # module needing to know PredictionTracker exists
        self._atr_state: Dict[InstrumentKey, AtrState] = defaultdict(AtrState)
        self._latest_atr: Dict[InstrumentKey, float] = {}

    def buffered_count(self) -> int:
        return len(self._buffer)

    def to_dataframe(self):
        """Optional convenience for calibration/analysis work — pandas is
        intentionally not a hard dependency of this module, only imported
        if a caller actually asks for this.

        Dtypes are tuned rather than left at pandas' defaults: `category`
        for the low-cardinality string columns (timeframe/activity_type/
        activity) instead of `object`, `float32` for the numeric ones.
        Memory isn't actually under pressure at the intended scale (one
        instrument's full 2-year backtest is ~13 MB this way vs. a still-
        trivial ~50 MB with default dtypes) — this is worth doing anyway
        because `category` columns compare/group noticeably faster than
        plain Python strings once a calibration pass is querying this.
        """
        import pandas as pd
        df = pd.DataFrame(self._buffer)
        if df.empty:
            return df
        df["instrument_id"] = df["instrument_id"].astype("int32")
        for col in ("timeframe", "activity_type", "activity"):
            df[col] = df[col].astype("category")
        for col in ("intensity", "open_price", "high_price", "low_price", "close_price"):
            df[col] = df[col].astype("float32")
        return df

    def flush(self) -> int:
        """Write every buffered activity to instrument_activity in as few
        DB round-trips as possible, then clear the buffer. Returns the
        number of activities flushed."""
        if not self._buffer:
            return 0
        rows, self._buffer = self._buffer, []
        self._persist_bulk(rows)
        return len(rows)

    def on_candle_closed(self, symbol: str, exchange_segment: str, candle: Candle) -> List[dict]:
        """Returns every activity dict newly appended to the buffer during
        this call (empty list if none fired) — lets a caller (e.g. a
        prediction tracker) react to just this candle's detections without
        being woven into the detection logic itself. Deliberately a plain
        return value, not a callback/event system — see the "keep
        prediction tracking loosely coupled" note in activity_engine's own
        history for why this stays minimal."""
        buffer_start = len(self._buffer)
        key: InstrumentKey = (symbol, exchange_segment, candle.timeframe)
        buffer = self._recent[key]
        buffer.append(candle)

        found: List[Tuple[str, str, Optional[float]]] = []  # (activity_type, activity, intensity)
        for name, detector in SINGLE_CANDLE_PATTERNS.items():
            try:
                if detector(candle):
                    intensity = SINGLE_CANDLE_INTENSITY[name](candle)
                    # a mathematically infinite ratio (open == close exactly)
                    # is a real result, not an error — store NULL rather
                    # than a sentinel that would read as an ordinary number
                    if intensity == float("inf"):
                        intensity = None
                    found.append(("candle_pattern", name, intensity))
            except Exception:
                logger.exception("Activity engine: %s failed for %s (%s)", name, symbol, exchange_segment)

        for name, detector in MULTI_CANDLE_PATTERNS.items():
            try:
                candles = list(buffer)
                if detector(candles):
                    intensity_fn = MULTI_CANDLE_INTENSITY.get(name)
                    intensity = intensity_fn(candles) if intensity_fn else None
                    if intensity == float("inf"):
                        intensity = None
                    found.append(("candle_pattern", name, intensity))
            except Exception:
                logger.exception("Activity engine: %s failed for %s (%s)", name, symbol, exchange_segment)

        closes = self._closes[key]
        closes.append(candle.close)
        bb = compute_bollinger(list(closes)[-_BB_PERIOD:])
        if bb is not None:
            widths = self._bb_widths[key]
            widths.append(bb.width)
            for name, detector in (("bb_squeeze", detect_bb_squeeze), ("bb_widening", detect_bb_widening)):
                try:
                    if detector(widths):
                        intensity = PRICE_ACTION_INTENSITY[name](widths)
                        if intensity == float("inf"):
                            intensity = None
                        found.append(("price_action", name, intensity))
                except Exception:
                    logger.exception("Activity engine: %s failed for %s (%s)", name, symbol, exchange_segment)

        vwap = self._update_vwap(key, candle)
        if vwap:
            gaps = self._vwap_gaps[key]
            gaps.append((candle.close - vwap) / vwap)
            for name, detector in (
                ("price_vwap_divergence", detect_price_vwap_divergence),
                ("vwap_gap_fill", detect_vwap_gap_fill),
            ):
                try:
                    if detector(gaps):
                        intensity = PRICE_ACTION_INTENSITY[name](gaps)
                        if intensity == float("inf"):
                            intensity = None
                        found.append(("price_action", name, intensity))
                except Exception:
                    logger.exception("Activity engine: %s failed for %s (%s)", name, symbol, exchange_segment)

        try:
            rsi = update_rsi(self._rsi_state[key], candle.close)
            if rsi is not None:
                prev_rsi = self._prev_rsi.get(key)
                if prev_rsi is not None:
                    if crossed_above(prev_rsi, _RSI_CROSS_ABOVE, rsi, _RSI_CROSS_ABOVE):
                        found.append(("indicator", "rsi_cross_above_60", cross_intensity(prev_rsi, _RSI_CROSS_ABOVE, rsi, _RSI_CROSS_ABOVE)))
                    if crossed_below(prev_rsi, _RSI_CROSS_BELOW, rsi, _RSI_CROSS_BELOW):
                        found.append(("indicator", "rsi_cross_below_40", cross_intensity(prev_rsi, _RSI_CROSS_BELOW, rsi, _RSI_CROSS_BELOW)))
                self._prev_rsi[key] = rsi
        except Exception:
            logger.exception("Activity engine: rsi crossover failed for %s (%s)", symbol, exchange_segment)

        try:
            macd_line, macd_signal = update_macd(self._macd_state[key], candle.close)
            if macd_line is not None and macd_signal is not None:
                prev_macd = self._prev_macd.get(key)
                if prev_macd is not None:
                    prev_line, prev_signal = prev_macd
                    if crossed_above(prev_line, prev_signal, macd_line, macd_signal):
                        found.append(("indicator", "macd_bullish_cross", cross_intensity(prev_line, prev_signal, macd_line, macd_signal)))
                    if crossed_below(prev_line, prev_signal, macd_line, macd_signal):
                        found.append(("indicator", "macd_bearish_cross", cross_intensity(prev_line, prev_signal, macd_line, macd_signal)))
                self._prev_macd[key] = (macd_line, macd_signal)
        except Exception:
            logger.exception("Activity engine: macd crossover failed for %s (%s)", symbol, exchange_segment)

        try:
            stoch_k, stoch_d = update_stochastic(self._stoch_state[key], candle.high, candle.low, candle.close)
            if stoch_k is not None and stoch_d is not None:
                prev_stoch = self._prev_stoch.get(key)
                if prev_stoch is not None:
                    prev_k, prev_d = prev_stoch
                    if crossed_above(prev_k, prev_d, stoch_k, stoch_d):
                        found.append(("indicator", "stoch_bullish_cross", cross_intensity(prev_k, prev_d, stoch_k, stoch_d)))
                    if crossed_below(prev_k, prev_d, stoch_k, stoch_d):
                        found.append(("indicator", "stoch_bearish_cross", cross_intensity(prev_k, prev_d, stoch_k, stoch_d)))
                self._prev_stoch[key] = (stoch_k, stoch_d)
        except Exception:
            logger.exception("Activity engine: stochastic crossover failed for %s (%s)", symbol, exchange_segment)

        try:
            atr = update_atr(self._atr_state[key], candle.high, candle.low, candle.close)
            if atr is not None:
                self._latest_atr[key] = atr
        except Exception:
            logger.exception("Activity engine: ATR update failed for %s (%s)", symbol, exchange_segment)

        if len(closes) >= _MA_LONG_PERIOD:
            ma21 = statistics.fmean(list(closes)[-_MA_SHORT_PERIOD:])
            ma50 = statistics.fmean(closes)
            prev_ma = self._prev_ma.get(key)
            if prev_ma is not None:
                prev_ma21, prev_ma50 = prev_ma
                if crossed_above(prev_ma21, prev_ma50, ma21, ma50):
                    found.append(("indicator", "ma_golden_cross", cross_intensity(prev_ma21, prev_ma50, ma21, ma50)))
                if crossed_below(prev_ma21, prev_ma50, ma21, ma50):
                    found.append(("indicator", "ma_death_cross", cross_intensity(prev_ma21, prev_ma50, ma21, ma50)))
            self._prev_ma[key] = (ma21, ma50)

        try:
            window = self._swing_window[key]
            window.append(candle)
            window_list = list(window)
            swing_hits = []
            if detect_swing_high(window_list, self.swing_lookback):
                swing_hits.append(("swing_high", swing_high_intensity(window_list, self.swing_lookback)))
            if detect_swing_low(window_list, self.swing_lookback):
                swing_hits.append(("swing_low", swing_low_intensity(window_list, self.swing_lookback)))
            if swing_hits:
                # the confirmed swing candle is swing_lookback candles behind
                # the latest one, not the latest candle itself
                swing_candle = window_list[self.swing_lookback]
                instrument_id = self._lookup_instrument_id(symbol, exchange_segment)
                if instrument_id is not None:
                    for activity, intensity in swing_hits:
                        if intensity == float("inf"):
                            intensity = None
                        self._buffer.append({
                            "instrument_id": instrument_id, "timeframe": candle.timeframe,
                            "ts": swing_candle.timestamp,
                            "activity_type": "structure", "activity": activity, "intensity": intensity,
                            "open_price": swing_candle.open, "high_price": swing_candle.high,
                            "low_price": swing_candle.low, "close_price": swing_candle.close,
                        })

                        points = self._swing_points[key]
                        kind = "high" if activity == "swing_high" else "low"
                        price = swing_candle.high if kind == "high" else swing_candle.low
                        swing_point = SwingPoint(kind=kind, price=price, candle=swing_candle)
                        points.append(swing_point)
                        points_list = list(points)

                        # both a double_top and a triple_top (etc.) can
                        # legitimately fire on the same trigger point — a
                        # genuine triple top's last two peaks usually also
                        # satisfy double_top's own looser 3-point check, and
                        # both are real, differently-scoped observations of
                        # the same shape, not a contradiction
                        formation_events = []
                        if kind == "high":
                            if detect_double_top(points_list):
                                formation_events.append(("double_top", double_top_intensity(points_list)))
                            if detect_triple_top(points_list):
                                formation_events.append(("triple_top", triple_top_intensity(points_list)))
                        else:  # kind == "low"
                            if detect_double_bottom(points_list):
                                formation_events.append(("double_bottom", double_bottom_intensity(points_list)))
                            if detect_triple_bottom(points_list):
                                formation_events.append(("triple_bottom", triple_bottom_intensity(points_list)))

                        for formation_name, formation_intensity in formation_events:
                            if formation_intensity == float("inf"):
                                formation_intensity = None
                            self._buffer.append({
                                "instrument_id": instrument_id, "timeframe": candle.timeframe,
                                "ts": swing_candle.timestamp,
                                "activity_type": "graph_formation", "activity": formation_name,
                                "intensity": formation_intensity,
                                "open_price": swing_candle.open, "high_price": swing_candle.high,
                                "low_price": swing_candle.low, "close_price": swing_candle.close,
                            })
                            # trade-planning levels — derived, not stored
                            # (see _STOP_LOSS_BUFFER's comment); logged here
                            # since this is the only point in the whole
                            # pipeline that still has the raw points list in
                            # hand, not just the persisted single-candle row
                            neckline_fn, stop_fn, target_fn = FORMATION_LEVEL_FUNCS[formation_name]
                            neckline, stop, target = neckline_fn(points_list), stop_fn(points_list), target_fn(points_list)
                            logger.info(
                                "%s %s %s: neckline=%.2f stop_loss=%.2f target=%.2f",
                                symbol, candle.timeframe, formation_name, neckline, stop, target,
                            )

                        # separate type-only sequences for structure-shift
                        # detection (LL-HH-HL / HH-LL-LH) — see detect_
                        # bullish_structure_shift's docstring for why this
                        # isn't just read off `points` above
                        if kind == "high":
                            self._recent_highs[key].append(swing_point)
                        else:
                            self._recent_lows[key].append(swing_point)
                        recent_highs = list(self._recent_highs[key])
                        recent_lows = list(self._recent_lows[key])

                        # at most one of these ever fires per call — a new
                        # low's own "is it higher or lower than the last
                        # low" comparison can't satisfy both the CHoCH and
                        # the BOS check's opposite-signed requirement on
                        # that same leg, and likewise for a new high
                        structure_events = []
                        if kind == "low":
                            if detect_bullish_structure_shift(recent_lows, recent_highs):
                                structure_events.append((
                                    "bullish_structure_shift",
                                    bullish_structure_shift_intensity(recent_lows, recent_highs),
                                ))
                            if detect_bearish_break_of_structure(recent_highs, recent_lows):
                                structure_events.append((
                                    "bearish_break_of_structure",
                                    bearish_break_of_structure_intensity(recent_highs, recent_lows),
                                ))
                        else:  # kind == "high"
                            if detect_bearish_structure_shift(recent_highs, recent_lows):
                                structure_events.append((
                                    "bearish_structure_shift",
                                    bearish_structure_shift_intensity(recent_highs, recent_lows),
                                ))
                            if detect_bullish_break_of_structure(recent_lows, recent_highs):
                                structure_events.append((
                                    "bullish_break_of_structure",
                                    bullish_break_of_structure_intensity(recent_lows, recent_highs),
                                ))
                        for event_name, event_intensity in structure_events:
                            if event_intensity == float("inf"):
                                event_intensity = None
                            self._buffer.append({
                                "instrument_id": instrument_id, "timeframe": candle.timeframe,
                                "ts": swing_candle.timestamp,
                                "activity_type": "structure", "activity": event_name,
                                "intensity": event_intensity,
                                "open_price": swing_candle.open, "high_price": swing_candle.high,
                                "low_price": swing_candle.low, "close_price": swing_candle.close,
                            })
        except Exception:
            logger.exception("Activity engine: swing detection failed for %s (%s)", symbol, exchange_segment)

        if not found:
            return self._buffer[buffer_start:]

        instrument_id = self._lookup_instrument_id(symbol, exchange_segment)
        if instrument_id is None:
            return self._buffer[buffer_start:]

        for activity_type, activity, intensity in found:
            self._buffer.append({
                "instrument_id": instrument_id, "timeframe": candle.timeframe, "ts": candle.timestamp,
                "activity_type": activity_type, "activity": activity, "intensity": intensity,
                "open_price": candle.open, "high_price": candle.high,
                "low_price": candle.low, "close_price": candle.close,
            })

        return self._buffer[buffer_start:]

    def get_atr(self, symbol: str, exchange_segment: str, timeframe: str) -> Optional[float]:
        """Read-only accessor for the latest ATR(14) value — used by
        PredictionTracker to size a crossover prediction's stop/target.
        None until _ATR_PERIOD candles have been seen for this key."""
        return self._latest_atr.get((symbol, exchange_segment, timeframe))

    def get_swing_points(self, symbol: str, exchange_segment: str, timeframe: str) -> List["SwingPoint"]:
        """Read-only accessor for the last (up to 5) confirmed swing points —
        used by PredictionTracker to recompute a just-fired double/triple
        top/bottom's neckline/stop/target via this module's own formulas
        (FORMATION_LEVEL_FUNCS), without this module needing to know
        PredictionTracker exists. Safe to call only right after
        on_candle_closed returns an activity naming that pattern — nothing
        else mutates this deque in between."""
        return list(self._swing_points[(symbol, exchange_segment, timeframe)])

    def _update_vwap(self, key: InstrumentKey, candle: Candle) -> Optional[float]:
        """Cumulative volume-weighted average price since the day's first
        candle for this instrument/timeframe — resets whenever the candle's
        date rolls over (market open/close are both well inside the same
        UTC calendar date, so a plain date() compare is enough, no IST
        conversion needed). Returns None until at least one candle with
        nonzero volume has been seen today."""
        day = candle.timestamp.date()
        state = self._vwap_state.get(key)
        if state is None or state.day != day:
            state = _VwapState(day=day)
            self._vwap_state[key] = state
        if candle.volume:
            typical_price = (candle.high + candle.low + candle.close) / 3
            state.cum_pv += typical_price * candle.volume
            state.cum_vol += candle.volume
        return state.cum_pv / state.cum_vol if state.cum_vol > 0 else None

    def _lookup_instrument_id(self, symbol: str, exchange_segment: str):
        cache_key = (symbol, exchange_segment)
        if cache_key in self._instrument_ids:
            return self._instrument_ids[cache_key]
        with session_scope(self.session_factory) as session:
            row = (
                session.query(SubscribedSymbol)
                .filter_by(symbol=symbol, exchange_segment=exchange_segment)
                .one_or_none()
            )
            if row is None:
                return None
            instrument_id = row.id  # read while still attached — session_scope closes on exit
        self._instrument_ids[cache_key] = instrument_id
        return instrument_id

    def _persist_bulk(self, rows: List[dict]) -> None:
        """Optimistic bulk insert — the common case (a fresh flush of newly
        detected activities) really is "all new rows," so skip the
        per-row existence check entirely and add everything in one flush.
        Same pattern as feed/candle_persistence.py's persist_candles_bulk,
        for the same reason: hundreds of individual SELECT-then-INSERT
        round trips was the actual cost there, not the write itself."""
        try:
            with session_scope(self.session_factory) as session:
                session.add_all([InstrumentActivity(**row) for row in rows])
                session.flush()
        except IntegrityError:
            # a concurrent writer (e.g. a backfill re-run touching the same
            # candles) already recorded one of these exact activities —
            # fall back to the slower existence-checked path per row so
            # that one conflict doesn't lose the rest of the flush
            for row in rows:
                self._persist_one(row)

    def _persist_one(self, row: dict) -> None:
        with self.session_factory() as session:
            try:
                with session.begin_nested():
                    exists = session.query(InstrumentActivity).filter_by(
                        instrument_id=row["instrument_id"], timeframe=row["timeframe"],
                        ts=row["ts"], activity=row["activity"],
                    ).one_or_none()
                    if exists is None:
                        session.add(InstrumentActivity(**row))
                    session.flush()
                session.commit()
            except IntegrityError:
                session.rollback()
