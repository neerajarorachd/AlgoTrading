"""Market Watch colored-cell grid — weighted bull/bear momentum score per
instrument, from confirmed pattern activity in the last WINDOW_CANDLES
candles. Explicit instruction, 2026-10-03: "we can give scores per pattern
like RSI is rising, weightage 1, HH-LH formed, weightage 3" — generalized
from those two examples into one weight per PatternDefinition.kind (the
category already used by the Watch-selection picker, see
InstrumentWatchExclusion), since hand-picking all ~38 directional patterns
individually isn't meaningfully more informed than picking by category.
Weights are a hand-picked STARTING point, same "ship a sensible default now,
let real backtest data correct it later" discipline this whole project
already uses (see swing_lookback/Engine Settings) — not claimed to be
calibrated.

Deliberately NOT included in v1: "RSI is rising" (the user's own first
example). It's a fundamentally different kind of signal from a discrete
pattern firing — a CONTINUOUS condition (true for many candles in a row),
not a countable event, so it needs its own trigger definition (e.g. a
slope-sign change) to avoid the double-counting risk already flagged in
[[post_crossover_divergence_plan]]'s confluence design discussion. Scoring
here is discrete-pattern-only; a continuous-indicator-trend contribution is
a deliberate, separate follow-up once that's designed properly, not folded
in by guessing.

Direction comes from prediction_tracker.BULLISH_PATTERNS/BEARISH_PATTERNS
(the same classification recommendation_engine.py and order_backtest.py
already use) — a pattern in neither set (doji, bb_squeeze, swing_high, ...)
contributes to neither score, same as everywhere else in this project.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

from db.models import InstrumentActivity, PatternDefinition
from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

WINDOW_CANDLES = 10  # "last 10 candles" -- the user's own example window

# Hand-picked starting weights by PatternDefinition.kind -- see module
# docstring. Higher = more confirmation required to fire, so weighted
# heavier: a single candlestick shape is noisy alone (1); a confirmed
# multi-point structure break or graph formation is the most "complete"
# signal this project detects (3-4). Matches the user's own "HH-LH formed,
# weightage 3" example exactly, since HH-LH (break of structure) IS the
# "structure" kind.
WEIGHT_BY_KIND: Dict[str, float] = {
    "single_candle": 1,
    "multi_candle": 2,
    "price_action": 2,
    "indicator": 2,
    "structure": 3,
    "graph_formation": 4,
}

# Bucket thresholds -- also hand-picked starting points, not calibrated.
STRONG_THRESHOLD = 6

BUCKET_COLORS = {
    "strong_bull": "#1b7a3d",   # dark green
    "mild_bull": "#8fd19e",     # light green
    "choppy": "#e0b000",        # amber -- both sides active, no clear lean
    "quiet": "#c9c9c9",         # grey -- nothing scored in the window
    "mild_bear": "#f0a6a6",     # light red
    "strong_bear": "#b3261e",   # dark red
}


def load_pattern_weights(session) -> Dict[str, float]:
    """code -> weight, built fresh from PatternDefinition.kind each call --
    cheap (one small table), and never drifts from WEIGHT_BY_KIND/kind
    changes without a code change on either side."""
    rows = session.query(PatternDefinition.code, PatternDefinition.kind).all()
    return {code: WEIGHT_BY_KIND.get(kind, 1) for code, kind in rows}


def _recent_window_activities(session, instrument_id: int, timeframe: str, window_candles: int) -> List[InstrumentActivity]:
    """Every activity row at any of the last `window_candles` DISTINCT
    candle timestamps that had at least one activity for this instrument/
    timeframe. An approximation of "last N candles" (derived from WHEN
    activity happened, not a join against candles_today/candles_historical)
    -- deliberately simple: sparse activity just means a longer real time
    span is covered, which is fine for a live snapshot grid, not a backtest."""
    cutoff_rows = (
        session.query(InstrumentActivity.ts)
        .filter(InstrumentActivity.instrument_id == instrument_id, InstrumentActivity.timeframe == timeframe)
        .distinct().order_by(InstrumentActivity.ts.desc()).limit(window_candles).all()
    )
    if not cutoff_rows:
        return []
    cutoff = cutoff_rows[-1][0]
    return (
        session.query(InstrumentActivity)
        .filter(
            InstrumentActivity.instrument_id == instrument_id, InstrumentActivity.timeframe == timeframe,
            InstrumentActivity.ts >= cutoff,
        ).all()
    )


def classify_bucket(bull_score: float, bear_score: float) -> str:
    if bull_score == 0 and bear_score == 0:
        return "quiet"
    if bull_score > 0 and bear_score > 0 and abs(bull_score - bear_score) <= min(bull_score, bear_score):
        return "choppy"
    if bull_score >= bear_score:
        return "strong_bull" if bull_score >= STRONG_THRESHOLD else "mild_bull"
    return "strong_bear" if bear_score >= STRONG_THRESHOLD else "mild_bear"


def score_instrument(session, instrument_id: int, timeframe: str, window_candles: int,
                      weights: Dict[str, float]) -> dict:
    """bull_score/bear_score: sum of each DISTINCT pattern's weight, counted
    ONCE per occurrence within the window (a pattern firing on 3 of the
    last 10 candles counts 3 times -- each occurrence is a real, separate
    signal) -- not deduplicated by pattern code, only by (pattern, candle)
    via the unique constraint already enforced on instrument_activity
    itself."""
    activities = _recent_window_activities(session, instrument_id, timeframe, window_candles)
    bull_score = sum(weights.get(a.activity, 1) for a in activities if a.activity in BULLISH_PATTERNS)
    bear_score = sum(weights.get(a.activity, 1) for a in activities if a.activity in BEARISH_PATTERNS)
    return {
        "instrument_id": instrument_id,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "bucket": classify_bucket(bull_score, bear_score),
    }


def score_instruments(session, instrument_ids: Sequence[int], timeframe: str,
                       window_candles: int = WINDOW_CANDLES) -> List[dict]:
    weights = load_pattern_weights(session)
    return [score_instrument(session, iid, timeframe, window_candles, weights) for iid in instrument_ids]
