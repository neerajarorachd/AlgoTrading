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

from datetime import datetime, timezone
from statistics import mean
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, select, union_all

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


# ---- data fetch: batched, + the live engine's not-yet-flushed rows --------
#
# Both fetches are ONE round trip for every instrument: a UNION ALL of small
# per-instrument TOP-N index seeks. Measured against the VM 2026-10-06: the
# old 3-queries-per-instrument loop cost ~140 ms of SSH-tunnel latency per
# query (~5-7 s for 13 instruments, the "color tiles take 7 s" complaint);
# this is ~0.3 s per table. A window-function (DENSE_RANK/ROW_NUMBER) batch
# was tried first and was far WORSE (43 s) -- SQL Server Express on this
# I/O-capped VM ranks the whole partition instead of seeking the index.
#
# `engine` (the running ActivityEngine, optional) contributes today's rows:
# it only flushes to the DB at 15:30 IST, so without it the grid scored the
# previous day's activity all session long.

def _naive_utc(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def _one_or_union(selects):
    return selects[0] if len(selects) == 1 else union_all(*selects)


def _activity_windows(session, instrument_ids: Sequence[int], timeframe: str, window_candles: int,
                      engine=None) -> Dict[int, List[Tuple[datetime, str]]]:
    """instrument_id -> [(ts, activity)] for every activity at any of the
    last `window_candles` DISTINCT candle timestamps that had at least one
    activity. An approximation of "last N candles" (derived from WHEN
    activity happened, not a join against candle tables) -- deliberately
    simple: sparse activity just means a longer real time span is covered,
    fine for a live snapshot grid, not a backtest."""
    by_id: Dict[int, List[Tuple[datetime, str]]] = {iid: [] for iid in instrument_ids}
    if not instrument_ids:
        return by_id
    parts = []
    for iid in instrument_ids:
        recent_ts = (
            select(InstrumentActivity.ts)
            .where(InstrumentActivity.instrument_id == iid, InstrumentActivity.timeframe == timeframe)
            .distinct().order_by(InstrumentActivity.ts.desc()).limit(window_candles).subquery()
        )
        cutoff = select(func.min(recent_ts.c.ts)).scalar_subquery()
        parts.append(
            select(InstrumentActivity.instrument_id, InstrumentActivity.ts, InstrumentActivity.activity)
            .where(InstrumentActivity.instrument_id == iid, InstrumentActivity.timeframe == timeframe,
                   InstrumentActivity.ts >= cutoff)
        )
    for iid, ts, activity in session.execute(_one_or_union(parts)):
        by_id[iid].append((_naive_utc(ts), activity))

    if engine is not None:
        for iid in instrument_ids:
            seen = set(by_id[iid])
            for row in engine.pending_activities(iid, timeframe):
                item = (_naive_utc(row["ts"]), row["activity"])
                if item not in seen:
                    seen.add(item)
                    by_id[iid].append(item)
    # re-apply the window over stored + pending together
    for iid, items in by_id.items():
        recent = sorted({ts for ts, _ in items}, reverse=True)[:window_candles]
        by_id[iid] = [it for it in items if recent and it[0] >= recent[-1]]
    return by_id


_VOLATILITY_FIELDS = ("atr", "bb_upper", "bb_middle", "bb_lower")


def _indicator_histories(session, instrument_ids: Sequence[int], timeframe: str, n: int,
                         engine=None) -> Dict[int, List[dict]]:
    """instrument_id -> the latest `n` indicator rows, newest first (index 0
    is "the latest candle," the rest the baseline to compare it against)."""
    by_id: Dict[int, List[dict]] = {iid: [] for iid in instrument_ids}
    if not instrument_ids:
        return by_id
    cols = (CandleIndicators.instrument_id, CandleIndicators.ts) + tuple(
        getattr(CandleIndicators, f) for f in _VOLATILITY_FIELDS)
    parts = [
        select(*cols).where(CandleIndicators.instrument_id == iid, CandleIndicators.timeframe == timeframe)
        .order_by(CandleIndicators.ts.desc()).limit(n).subquery()
        for iid in instrument_ids
    ]
    for row in session.execute(_one_or_union([select(p) for p in parts])).mappings():
        by_id[row["instrument_id"]].append({"ts": _naive_utc(row["ts"]), **{f: row[f] for f in _VOLATILITY_FIELDS}})

    for iid in instrument_ids:
        rows = by_id[iid]
        if engine is not None:
            stored = {r["ts"] for r in rows}
            for pending in engine.pending_indicator_rows(iid, timeframe):
                ts = _naive_utc(pending["ts"])
                if ts not in stored:
                    rows.append({"ts": ts, **{f: pending.get(f) for f in _VOLATILITY_FIELDS}})
        rows.sort(key=lambda r: r["ts"], reverse=True)
        by_id[iid] = rows[:n]
    return by_id


def _bb_width(row: dict) -> Optional[float]:
    if row["bb_upper"] is None or row["bb_lower"] is None or not row["bb_middle"]:
        return None
    return (float(row["bb_upper"]) - float(row["bb_lower"])) / float(row["bb_middle"])


def _volatility_factor_from(rows: List[dict]) -> float:
    """Latest ATR and BB-width vs. their own RANGE_BASELINE_CANDLES-candle
    average, averaged together and clamped -- see module docstring. Needs
    at least 2 rows (the latest + at least one baseline point) with a real
    value for a given indicator to use it at all; falls back to the
    neutral 1.0 if neither indicator has enough history yet (early in an
    instrument's life, or CandleIndicators simply isn't populated)."""
    if len(rows) < 2:
        return 1.0
    latest, history = rows[0], rows[1:]

    ratios = []
    history_atrs = [float(r["atr"]) for r in history if r["atr"] is not None]
    if latest["atr"] is not None and history_atrs:
        baseline = mean(history_atrs)
        if baseline:
            ratios.append(float(latest["atr"]) / baseline)

    latest_width = _bb_width(latest)
    history_widths = [w for w in (_bb_width(r) for r in history) if w is not None]
    if latest_width is not None and history_widths:
        baseline = mean(history_widths)
        if baseline:
            ratios.append(latest_width / baseline)

    if not ratios:
        return 1.0
    return max(VOLATILITY_FACTOR_MIN, min(VOLATILITY_FACTOR_MAX, mean(ratios)))


def volatility_factor(session, instrument_id: int, timeframe: str, engine=None) -> float:
    rows = _indicator_histories(session, [instrument_id], timeframe, RANGE_BASELINE_CANDLES, engine)[instrument_id]
    return _volatility_factor_from(rows)


def classify_bucket(bull_score: float, bear_score: float) -> str:
    if bull_score == 0 and bear_score == 0:
        return "quiet"
    if bull_score > 0 and bear_score > 0 and abs(bull_score - bear_score) <= min(bull_score, bear_score):
        return "choppy"
    if bull_score >= bear_score:
        return "strong_bull" if bull_score >= STRONG_THRESHOLD else "mild_bull"
    return "strong_bear" if bear_score >= STRONG_THRESHOLD else "mild_bear"


def _score(instrument_id: int, activities: List[Tuple[datetime, str]], indicator_rows: List[dict],
           weights: Dict[str, float]) -> dict:
    """bull_score/bear_score: sum of each pattern's weight, counted ONCE per
    occurrence within the window (a pattern firing on 3 of the last 10
    candles counts 3 times -- each occurrence is a real, separate signal)
    -- not deduplicated by pattern code, only by (pattern, candle) via the
    unique constraint already enforced on instrument_activity itself."""
    bull_score = sum(weights.get(a, 1) for _, a in activities if a in BULLISH_PATTERNS)
    bear_score = sum(weights.get(a, 1) for _, a in activities if a in BEARISH_PATTERNS)
    factor = _volatility_factor_from(indicator_rows)
    bull_score *= factor
    bear_score *= factor
    return {
        "instrument_id": instrument_id,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "bucket": classify_bucket(bull_score, bear_score),
        "volatility_factor": factor,
    }


def score_instrument(session, instrument_id: int, timeframe: str, window_candles: int,
                      weights: Dict[str, float], engine=None) -> dict:
    return score_instruments(session, [instrument_id], timeframe, window_candles, engine, weights)[0]


def score_instruments(session, instrument_ids: Sequence[int], timeframe: str,
                       window_candles: int = WINDOW_CANDLES, engine=None,
                       weights: Optional[Dict[str, float]] = None) -> List[dict]:
    weights = weights if weights is not None else load_pattern_weights(session)
    activities = _activity_windows(session, instrument_ids, timeframe, window_candles, engine)
    indicators = _indicator_histories(session, instrument_ids, timeframe, RANGE_BASELINE_CANDLES, engine)
    return [_score(iid, activities[iid], indicators[iid], weights) for iid in instrument_ids]
