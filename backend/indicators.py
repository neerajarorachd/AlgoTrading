"""Shared pure indicator-adjacent helpers used by both activity_engine.py
(price-action detection) and walkthrough_engine.py (indicator snapshot
persistence) — kept here so the two don't each grow their own copy.

classify_trend() mirrors the Trading project's LibAnalysisStrength.py
GetVWAPStrength: rather than comparing just two points (now vs. N candles
ago), it looks at every step across a lookback window and counts how many
consecutive steps move the (absolute) value up vs. down. A window is only
called "widening"/"narrowing" when a strong majority of its own steps agree
— a trend-consistency check, not a two-point comparison, so a single noisy
candle can't flip the call.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

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
