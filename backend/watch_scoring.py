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

Volatility multiplier, added 2026-10-04 -- explicit instruction: "add
range criteria too... bigger range means more chance of rise or fall. BB
gap also." Both ATR and Bollinger Band width are already stored per candle
(CandleIndicators.atr/bb_upper/bb_middle/bb_lower) -- this compares the
LATEST candle's own ATR and BB width against their own recent
RANGE_BASELINE_CANDLES average (reusing TREND_LOOKBACK=20, the exact
window bb_widening/bb_squeeze already use for the same "is this expanding"
question) and scales BOTH bull_score and bear_score by the result
EQUALLY -- deliberately direction-agnostic, since "bigger range = more
chance of rise OR fall" is about CONVICTION that an already-leaning move
will actually happen, not a new signal about which way it leans. Defaults
to a neutral 1.0 multiplier (no-op) whenever there isn't enough indicator
history to compare against -- the same "ship a sensible default" posture
as the rest of this module, and what keeps every existing weight-based
test passing unchanged (no CandleIndicators rows seeded there -> factor
stays exactly 1.0).
"""
from __future__ import annotations

from statistics import mean
from typing import Dict, List, Optional, Sequence

from db.models import CandleIndicators, InstrumentActivity, PatternDefinition
from indicators import TREND_LOOKBACK
from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

WINDOW_CANDLES = 10  # "last 10 candles" -- the user's own example window
RANGE_BASELINE_CANDLES = TREND_LOOKBACK  # 20 -- same baseline bb_widening/squeeze already use
# Clamp so one outlier candle (a data glitch, a single huge-range bar)
# can't send a bucket straight to "strong" or wipe a score to near-zero.
VOLATILITY_FACTOR_MIN = 0.5
VOLATILITY_FACTOR_MAX = 2.0

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


def _recent_indicator_rows(session, instrument_id: int, timeframe: str, n: int) -> List[CandleIndicators]:
    """Newest first -- index 0 is "the latest candle," the rest are the
    recent-history baseline to compare it against."""
    return (
        session.query(CandleIndicators)
        .filter_by(instrument_id=instrument_id, timeframe=timeframe)
        .order_by(CandleIndicators.ts.desc()).limit(n).all()
    )


def _bb_width(row: CandleIndicators) -> Optional[float]:
    if row.bb_upper is None or row.bb_lower is None or not row.bb_middle:
        return None
    return (float(row.bb_upper) - float(row.bb_lower)) / float(row.bb_middle)


def volatility_factor(session, instrument_id: int, timeframe: str) -> float:
    """Latest ATR and BB-width vs. their own RANGE_BASELINE_CANDLES-candle
    average, averaged together and clamped -- see module docstring. Needs
    at least 2 rows (the latest + at least one baseline point) with a real
    value for a given indicator to use it at all; falls back to the
    neutral 1.0 if neither indicator has enough history yet (early in an
    instrument's life, or CandleIndicators simply isn't populated)."""
    rows = _recent_indicator_rows(session, instrument_id, timeframe, RANGE_BASELINE_CANDLES)
    if len(rows) < 2:
        return 1.0
    latest, history = rows[0], rows[1:]

    ratios = []
    history_atrs = [float(r.atr) for r in history if r.atr is not None]
    if latest.atr is not None and history_atrs:
        baseline = mean(history_atrs)
        if baseline:
            ratios.append(float(latest.atr) / baseline)

    latest_width = _bb_width(latest)
    history_widths = [w for w in (_bb_width(r) for r in history) if w is not None]
    if latest_width is not None and history_widths:
        baseline = mean(history_widths)
        if baseline:
            ratios.append(latest_width / baseline)

    if not ratios:
        return 1.0
    return max(VOLATILITY_FACTOR_MIN, min(VOLATILITY_FACTOR_MAX, mean(ratios)))


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
    factor = volatility_factor(session, instrument_id, timeframe)
    bull_score *= factor
    bear_score *= factor
    return {
        "instrument_id": instrument_id,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "bucket": classify_bucket(bull_score, bear_score),
        "volatility_factor": factor,
    }


def score_instruments(session, instrument_ids: Sequence[int], timeframe: str,
                       window_candles: int = WINDOW_CANDLES) -> List[dict]:
    weights = load_pattern_weights(session)
    return [score_instrument(session, iid, timeframe, window_candles, weights) for iid in instrument_ids]
