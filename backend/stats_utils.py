"""Small, DB-free statistical helpers with no natural home in indicators.py
(which is scoped to indicator/candle math specifically — RSI/MACD/
Stochastic/trend classification). Currently just wilson_lower_bound, for
recommendation_engine.py's "best option wins" ranking — room to grow if
another generic stat is needed later without dragging in indicators.py's
own scope.
"""
from __future__ import annotations

from statistics import NormalDist


def wilson_lower_bound(wins: float, total: int, confidence: float = 0.95) -> float:
    """Wilson score interval lower bound — the standard technique for
    ranking a proportion when sample sizes differ wildly (e.g. Reddit/
    Amazon review ranking — "how not to sort by average rating"), used
    here so a well-attested band with a high win COUNT outranks a thin
    band that only looks better on raw win% (explicit instruction: "30
    historical wins with 75% will win over 2 historical wins with 90%
    win rate" — verified by hand: wilson_lower_bound(22.5, 30) ~= 0.573,
    wilson_lower_bound(1.8, 2) ~= 0.278, so the 30-sample band ranks
    higher despite its lower raw win rate).

    `wins` is normally an integer match count in real callers
    (recommendation_engine.py always passes one), but is accepted as a
    float here since the formula only needs win_pct = wins/total.
    `total <= 0` returns 0.0 (no evidence at all ranks lowest, never
    divides by zero). `confidence` maps to a z-score via the inverse
    normal CDF (statistics.NormalDist, stdlib, no new dependency) rather
    than hardcoding z=1.96 for exactly 95% — the confidence level is
    itself a SystemSetting (recommendation_wilson_confidence), so it must
    be usable at any value, not just 0.95."""
    if total <= 0:
        return 0.0
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    phat = wins / total
    denom = 1 + z * z / total
    center = phat + z * z / (2 * total)
    margin = z * ((phat * (1 - phat) + z * z / (4 * total)) / total) ** 0.5
    return (center - margin) / denom
