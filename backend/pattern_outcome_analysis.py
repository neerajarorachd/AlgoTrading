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
from datetime import timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from db.ops import LibActivities, LibCandleIndicators, LibCandlesHistorical, LibPatternOutcomes, LibSymbols
from db.session import session_scope
from indicators import (
    INDICATOR_TREND_LOOKBACK, classify_macd, classify_rsi, classify_series_trend, classify_stochastic,
)
from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

# 5/10/15/20/30 candles ahead (explicit instruction, 2026-09-15: "analyze
# 5, 10, 15, 20, 30 candles") — a short/medium/long horizon ladder so a
# pattern's own "how fast does it play out" shape is visible, not just a
# single fixed-N snapshot. 30 is also this module's upper checkpoint for
# max_favorable_pct/max_adverse_pct (see _compute_outcome — window size is
# max(checkpoints)), matching this project's own "20-30 candles" outcome-
# scan convention. See PatternOutcome.pct_change_30's own comment for the
# re-analysis caveat this extension creates for already-analyzed rows.
DEFAULT_CHECKPOINTS: Tuple[int, ...] = (5, 10, 15, 20, 30)


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


# A genuine data-collection hole (found live 2026-09-15: HINDCOPPER's own
# CandleHistorical has a real 440-day gap, 2025-04-01 to 2026-06-16 — a
# missing-data hole, not a market closure) must never be silently treated
# as "the next candle" — a checkpoint computed across it reports a price
# move spanning MONTHS, not real market minutes (confirmed: a fake +134%
# "30-candle move" traced directly to this exact gap). 7 days is generous
# enough to tolerate any real weekend/holiday closure (the smaller,
# legitimate gaps in this same dataset are all <= 3 days) while still
# catching an actual hole in data collection.
_MAX_NORMAL_GAP = timedelta(days=7)


def _truncate_at_data_gap(
    anchor_ts, window: List[tuple], max_gap: timedelta = _MAX_NORMAL_GAP,
) -> List[tuple]:
    """Cuts `window` (chronological (ts, close, high, low) tuples,
    strictly after `anchor_ts` — the detection candle's own timestamp)
    short at the first point further than `max_gap` from whatever comes
    before it — either from `anchor_ts` itself (window[0] alone can
    already be on the far side of a hole, if the detection candle sits
    right before one) or between two later candles in the window.
    Everything from that point on is on the far side of a data-collection
    hole, not a real forward-looking candle for this occurrence. A window
    with no gap wider than max_gap anywhere is returned unchanged."""
    previous_ts = anchor_ts
    for i, candle in enumerate(window):
        if candle[0] - previous_ts > max_gap:
            return window[:i]
        previous_ts = candle[0]
    return window


def _compute_outcome(entry_price: float, window: List[tuple], checkpoints: Sequence[int]) -> dict:
    """window: candles strictly after the detection candle, chronological,
    already sliced to at most max(checkpoints) long, and already passed
    through _truncate_at_data_gap by the caller — this function itself
    doesn't re-check for gaps, it just trusts window's own length. Each
    element is (ts, close, high, low)."""
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


def _series_trend(indicator_rows: List, idx: int, field: str) -> Optional[str]:
    """Trend of one indicator field over the INDICATOR_TREND_LOOKBACK rows
    ending at (and including) indicator_rows[idx] — e.g. was RSI climbing
    into this pattern's own formation candle, not just what it was AT that
    candle. Filters out None (warm-up) values from the window rather than
    letting one gap collapse the whole lookback to nothing."""
    window = indicator_rows[max(0, idx - INDICATOR_TREND_LOOKBACK + 1):idx + 1]
    values = [float(getattr(r, field)) for r in window if getattr(r, field) is not None]
    return classify_series_trend(values)


def _indicator_snapshot(indicator_rows: List, idx: int) -> dict:
    """Raw VALUE + classified STATE + classified TREND for the indicator
    snapshot at indicator_rows[idx] (explicit instruction, 2026-09-15:
    "store indicator state + values", then "lets do it next" for the
    trend/condition half of the same idea) — state and trend are both pure
    functions of already-persisted CandleIndicators values (indicators.py's
    classify_rsi/classify_macd/classify_stochastic/classify_series_trend),
    so re-labeling after a threshold tune is a re-analysis, not a schema
    change."""
    row = indicator_rows[idx]
    rsi = float(row.rsi) if row.rsi is not None else None
    macd_line = float(row.macd_line) if row.macd_line is not None else None
    macd_signal = float(row.macd_signal) if row.macd_signal is not None else None
    stoch_k = float(row.stoch_k) if row.stoch_k is not None else None
    return {
        "entry_rsi": rsi, "entry_rsi_state": classify_rsi(rsi),
        "entry_rsi_trend": _series_trend(indicator_rows, idx, "rsi"),
        "entry_macd_line": macd_line, "entry_macd_signal": macd_signal,
        "entry_macd_state": classify_macd(macd_line, macd_signal),
        "entry_macd_trend": _series_trend(indicator_rows, idx, "macd_line"),
        "entry_stoch_k": stoch_k, "entry_stoch_state": classify_stochastic(stoch_k),
        "entry_stoch_trend": _series_trend(indicator_rows, idx, "stoch_k"),
    }


_EMPTY_INDICATOR_SNAPSHOT = {
    "entry_rsi": None, "entry_rsi_state": None, "entry_rsi_trend": None,
    "entry_macd_line": None, "entry_macd_signal": None, "entry_macd_state": None, "entry_macd_trend": None,
    "entry_stoch_k": None, "entry_stoch_state": None, "entry_stoch_trend": None,
}


def analyze_instrument(
    session_factory, symbol: str, exchange_segment: str, timeframe: str,
    checkpoints: Sequence[int] = DEFAULT_CHECKPOINTS,
) -> int:
    """Bulk-loads this instrument/timeframe's activities, candles, and
    indicator snapshots ONCE, computes a neutral outcome for every
    activity via list slicing, writes every result in one bulk insert.
    Returns how many NEW PatternOutcome rows were written (0 on a re-run
    over already-analyzed data, or if the instrument/candles/activities
    aren't found).

    Indicator snapshots (CandleIndicators) are matched by exact ts — no
    bisect needed to FIND one (unlike the forward-looking candle window),
    since an activity and its indicator snapshot are always written for
    the IDENTICAL candle close; the trend half of the snapshot (was RSI/
    MACD/Stochastic climbing into this candle) DOES need the row's
    position in the full ordered series, to slice the INDICATOR_TREND_
    LOOKBACK candles immediately before it — still one dict lookup for the
    position, not a per-activity query. Missing entirely (e.g. a replay
    that never called ActivityEngine.flush() for the indicator side) just
    means every entry_*/state/trend field on that row comes back None —
    never an error."""
    max_window = max(checkpoints)

    with session_scope(session_factory) as session:
        instrument_id = LibSymbols.get_instrument_id(session, symbol, exchange_segment)
        if instrument_id is None:
            return 0
        activity_rows = LibActivities.get_for_instrument(session, instrument_id, timeframe)
        # LibCandlesHistorical (the persistent multi-day archive), NOT
        # LibCandles (CandleToday — a handful of recent days only, meant
        # for the live feed, not historical analysis). Found live
        # 2026-09-15: this function used to read CandleToday, so any
        # instrument/timeframe whose activities extended past CandleToday's
        # own narrow window silently got an empty or truncated forward
        # candle set — this is a genuine historical-analysis function, it
        # needs the real archive.
        candle_rows = LibCandlesHistorical.get_range(session, symbol, exchange_segment, timeframe)
        indicator_rows = LibCandleIndicators.get_for_instrument(session, instrument_id, timeframe)
        # Read out plain values while the session is still open (avoids
        # DetachedInstanceError later) into ordinary tuples/lists — cheap,
        # and gives something to slice/loop over that isn't tied to the
        # session's lifetime. indicator_rows itself stays as ORM objects
        # (not tuples) since _indicator_snapshot/_series_trend read several
        # named fields off it — session_scope's rows are still attached
        # here, before the block exits.
        candles = [
            (c.ts, float(c.close_price), float(c.high_price), float(c.low_price))
            for c in candle_rows
        ]
        activities = [
            (a.ts, a.activity, a.activity_type, float(a.close_price))
            for a in activity_rows
        ]
        indicator_idx_by_ts = {r.ts: i for i, r in enumerate(indicator_rows)}
        indicator_snapshots_by_ts = {
            ts: _indicator_snapshot(indicator_rows, idx) for ts, idx in indicator_idx_by_ts.items()
        }

    if not candles or not activities:
        return 0

    candle_ts = [c[0] for c in candles]
    results: List[dict] = []
    for ts, pattern, activity_type, entry_price in activities:
        pos = bisect.bisect_right(candle_ts, ts)  # first candle strictly after this activity's own ts
        window = candles[pos:pos + max_window]
        window = _truncate_at_data_gap(ts, window)
        outcome = _compute_outcome(entry_price, window, checkpoints)
        results.append({
            "instrument_id": instrument_id, "timeframe": timeframe, "pattern": pattern,
            "activity_type": activity_type, "direction": _direction_for(pattern),
            "detected_ts": ts, "entry_price": entry_price,
            **outcome,
            **indicator_snapshots_by_ts.get(ts, _EMPTY_INDICATOR_SNAPSHOT),
        })

    # Chunked, not one giant bulk insert — found live 2026-09-17: a single
    # persist_bulk call over hundreds of thousands of rows (this function's
    # own natural scale for a 2-year 1-min history) stalled badly under SQL
    # Server Express's small (~1.4GB) buffer pool, the same class of issue
    # already fixed for ActivityEngine's own flush() in
    # build_historical_activities.py. Smaller chunks give the same
    # scheduler-contention relief without changing what gets written.
    _CHUNK = 2_000
    written = 0
    for start in range(0, len(results), _CHUNK):
        written += LibPatternOutcomes.persist_bulk(session_factory, results[start:start + _CHUNK])
    return written
