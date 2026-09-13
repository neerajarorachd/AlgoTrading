"""Neutral "what happens after a pattern occurred" analysis — the first
deliverable toward the Strategy/backtesting system (project guidance:
"Freehand backtesting" comes before Strategies, which comes before the
full backtesting engine). See db/models.py's PatternOutcome for exactly
what gets recorded and why it's independent of PredictionTracker's own
predicted stop-loss/target.

Deliberately avoids the row-by-row-in-a-loop shape found in the Trading
project's own backtest engine (reviewed this session): candles and
activities are each bulk-loaded ONCE per instrument (no per-activity DB
query), and each activity's forward-looking window is a plain list slice
(O(1) after one bisect, not a re-scan) — the only loop here is over the
*activities* themselves (typically tens to a few hundred per instrument/
day), never over every candle recomputing something. No pandas needed —
this is small enough that plain lists/bisect are both simpler and just as
fast, matching activity_engine.py's own "pandas is optional, not a hard
dependency" stance.
"""
from __future__ import annotations

import bisect
from typing import Dict, List, Optional, Sequence, Tuple

from db.ops import LibActivities, LibCandles, LibPatternOutcomes, LibSymbols
from db.session import session_scope
from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

# 5/10/15/20 candles ahead — close enough to the "next 15-20 candles"
# instruction, extended down to 5 for a shorter-horizon read too; the
# outcome-scan window used throughout this project's calibration notes
# is "20-30 candles," so 20 is this module's own upper checkpoint rather
# than a separate number to keep track of.
DEFAULT_CHECKPOINTS: Tuple[int, ...] = (5, 10, 15, 20)


def _direction_for(pattern: str) -> Optional[str]:
    """Reuses PredictionTracker's own bull/bear classification rather than
    duplicating it — this is informational labeling for the outcome row,
    not a re-derivation of PredictionTracker's prediction logic. Patterns
    with no registered direction (single/multi-candle shapes not yet
    classified, or genuinely neutral ones like rectangle) get None."""
    if pattern in BULLISH_PATTERNS:
        return "bull"
    if pattern in BEARISH_PATTERNS:
        return "bear"
    return None


def _compute_outcome(entry_price: float, window: List[tuple], checkpoints: Sequence[int]) -> dict:
    """window: candles strictly after the detection candle, chronological,
    already sliced to at most max(checkpoints) long. Each element is
    (ts, close, high, low)."""
    result: Dict[str, Optional[float]] = {"window_candles": len(window)}
    for n in checkpoints:
        if len(window) >= n:
            close_n = window[n - 1][1]
            result[f"pct_change_{n}"] = (close_n - entry_price) / entry_price
        else:
            result[f"pct_change_{n}"] = None
    if window:
        result["max_favorable_pct"] = (max(c[2] for c in window) - entry_price) / entry_price
        result["max_adverse_pct"] = (min(c[3] for c in window) - entry_price) / entry_price
    else:
        result["max_favorable_pct"] = None
        result["max_adverse_pct"] = None
    return result


def analyze_instrument(
    session_factory, symbol: str, exchange_segment: str, timeframe: str,
    checkpoints: Sequence[int] = DEFAULT_CHECKPOINTS,
) -> int:
    """Bulk-loads this instrument/timeframe's activities and candles ONCE,
    computes a neutral outcome for every activity via list slicing, writes
    every result in one bulk insert. Returns how many NEW PatternOutcome
    rows were written (0 on a re-run over already-analyzed data, or if
    the instrument/candles/activities aren't found)."""
    max_window = max(checkpoints)

    with session_scope(session_factory) as session:
        instrument_id = LibSymbols.get_instrument_id(session, symbol, exchange_segment)
        if instrument_id is None:
            return 0
        activity_rows = LibActivities.get_for_instrument(session, instrument_id, timeframe)
        candle_rows = LibCandles.get_range(session, symbol, exchange_segment, timeframe)
        # Read out plain values while the session is still open (avoids
        # DetachedInstanceError later) into ordinary tuples/lists — cheap,
        # and gives something to slice/loop over that isn't tied to the
        # session's lifetime.
        candles = [
            (c.ts, float(c.close_price), float(c.high_price), float(c.low_price))
            for c in candle_rows
        ]
        activities = [
            (a.ts, a.activity, a.activity_type, float(a.close_price))
            for a in activity_rows
        ]

    if not candles or not activities:
        return 0

    candle_ts = [c[0] for c in candles]
    results: List[dict] = []
    for ts, pattern, activity_type, entry_price in activities:
        pos = bisect.bisect_right(candle_ts, ts)  # first candle strictly after this activity's own ts
        window = candles[pos:pos + max_window]
        outcome = _compute_outcome(entry_price, window, checkpoints)
        results.append({
            "instrument_id": instrument_id, "timeframe": timeframe, "pattern": pattern,
            "activity_type": activity_type, "direction": _direction_for(pattern),
            "detected_ts": ts, "entry_price": entry_price,
            **outcome,
        })

    return LibPatternOutcomes.persist_bulk(session_factory, results)
