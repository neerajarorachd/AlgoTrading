"""Detects candlestick patterns and price-action signals as each 1-/3-/5-min
candle closes, and persists them to instrument_activity. Covers, so far:
single-candle shape patterns (doji, hammer, shooting star), two-candle
patterns (bullish/bearish engulfing, piercing line, dark cloud cover,
tweezer top/bottom), three-candle patterns (three white soldiers / three
black crows, morning star / evening star), and price-action/indicator
signals (Bollinger Band squeeze/widening, price stretching away from VWAP,
price reverting back to fill a VWAP gap). Larger multi-bar chart formations
(double top/bottom, head and shoulders, flags, triangles, HH-HL trend
structure, etc.) need swing-high/swing-low detection as a foundation and
aren't built yet. MACD crossover, MA21/MA50 crossover, and outcome tracking
(what happened N candles after an activity) are deliberately not built yet
either.
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict, deque
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.exc import IntegrityError

from brokers.models import Candle
from db.models import InstrumentActivity, PatternDefinition, SubscribedSymbol
from db.session import session_scope
from indicators import TREND_LOOKBACK, classify_trend, trend_intensity

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
        self._recent: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=_LOOKBACK))
        self._instrument_ids: Dict[Tuple[str, str], int] = {}
        self._buffer: List[dict] = []
        # price-action state, one series per (symbol, exchange_segment, timeframe)
        self._closes: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=_BB_PERIOD))
        self._bb_widths: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=TREND_LOOKBACK))
        self._vwap_state: Dict[InstrumentKey, _VwapState] = {}
        self._vwap_gaps: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=_VWAP_GAP_LOOKBACK))

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

    def on_candle_closed(self, symbol: str, exchange_segment: str, candle: Candle) -> None:
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
        bb = compute_bollinger(closes)
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

        if not found:
            return

        instrument_id = self._lookup_instrument_id(symbol, exchange_segment)
        if instrument_id is None:
            return

        for activity_type, activity, intensity in found:
            self._buffer.append({
                "instrument_id": instrument_id, "timeframe": candle.timeframe, "ts": candle.timestamp,
                "activity_type": activity_type, "activity": activity, "intensity": intensity,
                "open_price": candle.open, "high_price": candle.high,
                "low_price": candle.low, "close_price": candle.close,
            })

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
