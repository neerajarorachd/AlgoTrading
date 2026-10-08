"""Classic (floor-trader) pivot points -- P/R1-3/S1-3 from one prior day's
H/L/C, drawn as horizontal reference lines on the live chart's price pane
(see memory: live_indicators_phase1_priority). Pure formula, no DB/session
dependency, same shape as indicators.py's own pure helpers -- the caller
(routes_candles.py) is responsible for finding the right "previous day"
candle to feed in.
"""
from __future__ import annotations

from typing import TypedDict


class PivotLevels(TypedDict):
    p: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float


def classic_pivot_points(prev_high: float, prev_low: float, prev_close: float) -> PivotLevels:
    """Standard floor-trader formula. P = (H+L+C)/3; R/S levels expand out
    from P by the prior day's own range (H-L), widening at each tier."""
    pivot = (prev_high + prev_low + prev_close) / 3
    day_range = prev_high - prev_low
    return {
        "p": pivot,
        "r1": 2 * pivot - prev_low,
        "s1": 2 * pivot - prev_high,
        "r2": pivot + day_range,
        "s2": pivot - day_range,
        "r3": prev_high + 2 * (pivot - prev_low),
        "s3": prev_low - 2 * (prev_high - pivot),
    }
