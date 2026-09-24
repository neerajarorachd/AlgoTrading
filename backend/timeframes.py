"""Shared timeframe-string helpers. prediction_tracker.py currently keeps
its own private _TIMEFRAME_MINUTES = {"1min": 1, "3min": 3, "5min": 5} (no
"1day") for its same-candle-ambiguity window math; feed/candle_aggregator.py's
_ROLLUP_MINUTES is feed-layer-private and also excludes 1min/1day. Neither
is reusable as a public "timeframe -> wall-clock duration" helper, and
recommendation_engine.py needs exactly that (to turn "N candles" into a
timedelta) for a timeframe set that DOES include "1day" (PatternOutcome/
CandleHistorical both already support it). Kept as its own tiny module
rather than folded into indicators.py or db/models.py, matching this
project's existing granular-module convention (depth_metrics.py,
market_state.py). The other two modules' private copies are a candidate
follow-up cleanup to migrate onto this, not touched here.
"""
from __future__ import annotations

from datetime import timedelta

TIMEFRAME_MINUTES = {"1min": 1, "3min": 3, "5min": 5, "1day": 1440}


def candle_duration(timeframe: str) -> timedelta:
    """Raises KeyError for an unrecognized timeframe — deliberately not a
    silent default, since a wrong duration would silently corrupt an
    expiry calculation."""
    return timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
