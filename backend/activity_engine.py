"""Detects candlestick patterns as each 1-/3-/5-min candle closes, and
persists them to instrument_activity. First slice: single-candle shape
patterns (doji, hammer, shooting star) and one three-candle pattern pair
(three white soldiers / three black crows). Indicator-based activity (MACD
crossover, MA21/MA50 crossover) and outcome tracking (what happened N candles
after an activity) are deliberately not built yet — this only covers what
was asked for in this first pass.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Callable, Dict, List, Tuple

from sqlalchemy.exc import IntegrityError

from brokers.models import Candle
from db.models import InstrumentActivity, PatternDefinition, SubscribedSymbol
from db.session import session_scope

logger = logging.getLogger(__name__)

# Longest multi-candle pattern detected today (three soldiers/crows) — the
# rolling per-instrument buffer only needs to hold this many candles.
_LOOKBACK = 3

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


MULTI_CANDLE_PATTERNS: Dict[str, Callable[[List[Candle]], bool]] = {
    "three_white_soldiers": detect_three_white_soldiers,
    "three_black_crows": detect_three_black_crows,
}

# The data-driven catalog (PatternDefinition rows) — kept next to the
# detector dicts above so a new pattern's code/kind/description is added in
# the same place as its detection function, not a separate file to remember.
PATTERN_CATALOG = [
    ("doji", "single_candle", "Open and close almost equal (body <= 10% of the high-low range) — indecision"),
    ("hammer", "single_candle", "Small body near the top, long lower wick (>= 2x body), little/no upper wick"),
    ("shooting_star", "single_candle", "Small body near the bottom, long upper wick (>= 2x body), little/no lower wick"),
    ("three_white_soldiers", "multi_candle", "Three consecutive bullish candles, each closing higher, opening within the prior body"),
    ("three_black_crows", "multi_candle", "Three consecutive bearish candles, each closing lower, opening within the prior body"),
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
    """

    def __init__(self, session_factory):
        self.session_factory = session_factory
        self._recent: Dict[InstrumentKey, deque] = defaultdict(lambda: deque(maxlen=_LOOKBACK))
        self._instrument_ids: Dict[Tuple[str, str], int] = {}

    def on_candle_closed(self, symbol: str, exchange_segment: str, candle: Candle) -> None:
        key: InstrumentKey = (symbol, exchange_segment, candle.timeframe)
        buffer = self._recent[key]
        buffer.append(candle)

        found: List[Tuple[str, str]] = []  # (activity_type, activity)
        for name, detector in SINGLE_CANDLE_PATTERNS.items():
            try:
                if detector(candle):
                    found.append(("candle_pattern", name))
            except Exception:
                logger.exception("Activity engine: %s failed for %s (%s)", name, symbol, exchange_segment)

        for name, detector in MULTI_CANDLE_PATTERNS.items():
            try:
                if detector(list(buffer)):
                    found.append(("candle_pattern", name))
            except Exception:
                logger.exception("Activity engine: %s failed for %s (%s)", name, symbol, exchange_segment)

        if not found:
            return

        instrument_id = self._lookup_instrument_id(symbol, exchange_segment)
        if instrument_id is None:
            return

        self._persist(instrument_id, candle.timeframe, candle.timestamp, found)

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

    def _persist(self, instrument_id: int, timeframe: str, ts, found: List[Tuple[str, str]]) -> None:
        with session_scope(self.session_factory) as session:
            for activity_type, activity in found:
                try:
                    with session.begin_nested():
                        exists = session.query(InstrumentActivity).filter_by(
                            instrument_id=instrument_id, timeframe=timeframe, ts=ts, activity=activity,
                        ).one_or_none()
                        if exists is None:
                            session.add(InstrumentActivity(
                                instrument_id=instrument_id, timeframe=timeframe, ts=ts,
                                activity_type=activity_type, activity=activity,
                            ))
                        session.flush()
                except IntegrityError:
                    # a concurrent writer (e.g. a backfill re-run for the same
                    # candle) already recorded this exact activity — fine,
                    # same idempotency guarantee as candle persistence
                    continue
