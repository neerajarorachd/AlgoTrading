"""PatternOutcome reads/writes — see db/models.py's PatternOutcome for what
this table records and why (a neutral "what happened after this pattern"
analysis, independent of PatternPrediction's own predicted stop/target)."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError

from db.models import InstrumentActivity, PatternOutcome
from db.session import session_scope

# Mirrors pattern_outcome_analysis.DEFAULT_CHECKPOINTS — kept as a plain
# tuple here (not imported) since pattern_outcome_analysis.py imports FROM
# db.ops, and importing it back here would be circular; if that module's
# checkpoints ever change, this needs updating alongside it.
CHECKPOINTS: Tuple[int, ...] = (5, 10, 15, 20, 30)


def persist_bulk(session_factory, rows: List[dict]) -> int:
    """Optimistic bulk insert — same shape as LibActivities.persist_bulk:
    the common case (a fresh analysis run) really is "all new rows," so
    skip the per-row existence check and add everything in one flush,
    falling back to a per-row existence-checked insert only if that
    flush hits the unique constraint (a re-run over already-analyzed
    data). Returns the number of rows actually inserted (a re-run
    correctly reports 0 new rows, not len(rows))."""
    if not rows:
        return 0
    try:
        with session_scope(session_factory) as session:
            session.add_all([PatternOutcome(**row) for row in rows])
            session.flush()
        return len(rows)
    except IntegrityError:
        inserted = 0
        for row in rows:
            if persist_one(session_factory, row):
                inserted += 1
        return inserted


def raw_values_by_pattern(
    session, instrument_id: int, timeframes: Sequence[str], ts_from: datetime, ts_to: datetime,
    patterns: Optional[Sequence[str]] = None,
) -> Dict[str, dict]:
    """Pure raw-data extraction — no medians, no match-rate, nothing
    derived — grouping already-computed PatternOutcome rows by pattern.
    The occurrence-backtest UI's "Intensity Median"/"Expected Result"/
    "Actual Result"/"Price Range" columns are all built by a caller
    aggregating these lists themselves (explicit instruction: "if what
    happened in next 20 candles is there, keep it as it is, it is raw
    data, then add analysis columns from that existing column" — this
    function IS "keep it as it is", nothing here changes PatternOutcome's
    own stored values).

    Returns {pattern: {"pct_values": {5: [...], 10: [...], 15: [...],
    20: [...], 30: [...]}, "up_values": [...], "down_values": [...],
    "range_values": [...]}} — up_values/down_values are PatternOutcome's
    own max_favorable_pct/max_adverse_pct (the best/worst price seen
    anywhere in the window, i.e. literally "up X% / down Y%" from entry,
    per the model's own docstring); range_values is up-down per occurrence
    (only where both are present — they're always computed together, see
    pattern_outcome_analysis.py's own _compute_outcome). pct_values[N] is
    the close-after-N-candles % change at every checkpoint (explicit
    instruction, 2026-09-15: "analyze 5, 10, 15, 20, 30 candles... share
    analysis of all of these") — the raw material a CALLER uses to decide
    "did it move the expected direction, and how does that change as N
    grows" — this function doesn't know or care what the pattern's
    expected direction is, that's routes_activities.py's job (it already
    has its own _direction_for, kept as the single source of truth rather
    than trusting this row's own possibly-stale `direction` column)."""
    query = (
        session.query(PatternOutcome)
        .filter(PatternOutcome.instrument_id == instrument_id)
        .filter(PatternOutcome.timeframe.in_(timeframes))
        .filter(PatternOutcome.detected_ts >= ts_from, PatternOutcome.detected_ts <= ts_to)
    )
    if patterns:
        query = query.filter(PatternOutcome.pattern.in_(patterns))

    by_pattern: Dict[str, dict] = {}
    for row in query.all():
        bucket = by_pattern.setdefault(row.pattern, {
            "pct_values": {n: [] for n in CHECKPOINTS},
            "up_values": [], "down_values": [], "range_values": [],
        })
        for n in CHECKPOINTS:
            value = getattr(row, f"pct_change_{n}")
            if value is not None:
                bucket["pct_values"][n].append(float(value))
        if row.max_favorable_pct is not None and row.max_adverse_pct is not None:
            up = float(row.max_favorable_pct)
            down = float(row.max_adverse_pct)
            bucket["up_values"].append(up)
            bucket["down_values"].append(down)
            bucket["range_values"].append(up - down)
    return by_pattern


def _band_stats(outcomes: Sequence[PatternOutcome]) -> dict:
    """Shared by intensity_banded_analysis and indicator_state_analysis —
    both group PatternOutcome rows into buckets by some other dimension
    (intensity tercile, RSI/MACD/Stochastic state) and then need the
    IDENTICAL per-checkpoint match-rate + price-range computation for
    whatever landed in each bucket. Kept as one function so a change to
    "what counts as a match" only has to happen in one place."""
    checkpoint_stats: Dict[int, dict] = {}
    for n in CHECKPOINTS:
        matched = total = 0
        for outcome in outcomes:
            pct = getattr(outcome, f"pct_change_{n}")
            if pct is None or outcome.direction not in ("bull", "bear"):
                continue
            pct = float(pct)
            total += 1
            if (outcome.direction == "bull" and pct > 0) or (outcome.direction == "bear" and pct < 0):
                matched += 1
        checkpoint_stats[n] = {
            "matched": matched, "total": total,
            "pct": (matched / total) if total else None,
        }

    up_values = [float(o.max_favorable_pct) for o in outcomes if o.max_favorable_pct is not None and o.max_adverse_pct is not None]
    down_values = [float(o.max_adverse_pct) for o in outcomes if o.max_favorable_pct is not None and o.max_adverse_pct is not None]
    range_values = [u - d for u, d in zip(up_values, down_values)]
    return {
        "count": len(outcomes), "checkpoints": checkpoint_stats,
        "up_median_pct": statistics.median(up_values) if up_values else None,
        "down_median_pct": statistics.median(down_values) if down_values else None,
        "range_median_pct": statistics.median(range_values) if range_values else None,
    }


def fetch_outcomes(
    session, instrument_id: int, timeframes: Sequence[str], ts_from: datetime, ts_to: datetime,
    patterns: Optional[Sequence[str]] = None,
) -> List[PatternOutcome]:
    """Plain PatternOutcome query, `patterns=None` meaning "every pattern in
    scope" — the bulk-fetch primitive pattern_level_analysis/indicator_
    state_analysis/InstrumentHistoryCache all build on, so a caller
    evaluating MANY patterns for one instrument (a replay/batch scan) can
    fetch them all in ONE query instead of one query per pattern (2026-09-15,
    explicit: "when we are doing replay, we should fetch whole bunch of
    historical result data in one go and then search in-memory" — see
    InstrumentHistoryCache, which uses this)."""
    query = (
        session.query(PatternOutcome)
        .filter(PatternOutcome.instrument_id == instrument_id)
        .filter(PatternOutcome.timeframe.in_(timeframes))
        .filter(PatternOutcome.detected_ts >= ts_from, PatternOutcome.detected_ts <= ts_to)
    )
    if patterns is not None:
        query = query.filter(PatternOutcome.pattern.in_(patterns))
    return query.all()


def fetch_intensity_paired(
    session, instrument_id: int, timeframes: Sequence[str], ts_from: datetime, ts_to: datetime,
    patterns: Optional[Sequence[str]] = None,
) -> List[Tuple[float, PatternOutcome]]:
    """The InstrumentActivity.intensity-joined-to-PatternOutcome bulk-fetch
    primitive intensity_banded_analysis/InstrumentHistoryCache build on —
    same "one query for every pattern in scope" shape as fetch_outcomes.
    Sorted by intensity ascending (NOT grouped by pattern — callers that
    need per-pattern grouping, e.g. InstrumentHistoryCache, do that
    themselves after fetching)."""
    query = (
        session.query(InstrumentActivity.intensity, PatternOutcome)
        .join(PatternOutcome, and_(
            InstrumentActivity.instrument_id == PatternOutcome.instrument_id,
            InstrumentActivity.timeframe == PatternOutcome.timeframe,
            InstrumentActivity.activity == PatternOutcome.pattern,
            InstrumentActivity.ts == PatternOutcome.detected_ts,
        ))
        .filter(InstrumentActivity.instrument_id == instrument_id)
        .filter(InstrumentActivity.timeframe.in_(timeframes))
        .filter(InstrumentActivity.ts >= ts_from, InstrumentActivity.ts <= ts_to)
        .filter(InstrumentActivity.intensity.isnot(None))
    )
    if patterns is not None:
        query = query.filter(InstrumentActivity.activity.in_(patterns))
    rows = query.all()
    return sorted(((float(intensity), outcome) for intensity, outcome in rows), key=lambda r: r[0])


def _banded_from_paired(paired: List[Tuple[float, PatternOutcome]], bands: int = 3) -> List[dict]:
    """Pure compute half of intensity_banded_analysis — equal-count
    banding (lowest first) over already-fetched, already-sorted
    (intensity, outcome) pairs. No query; shared by the query-per-call
    public function below and InstrumentHistoryCache's in-memory path."""
    if not paired:
        return []
    total_n = len(paired)
    band_size = math.ceil(total_n / bands)
    results: List[dict] = []
    for i in range(bands):
        chunk = paired[i * band_size:(i + 1) * band_size]
        if not chunk:
            continue
        intensities = [c[0] for c in chunk]
        outcomes = [c[1] for c in chunk]
        results.append({
            "band_index": i, "intensity_min": min(intensities), "intensity_max": max(intensities),
            **_band_stats(outcomes),
        })
    return results


def intensity_banded_analysis(
    session, instrument_id: int, timeframes: Sequence[str], ts_from: datetime, ts_to: datetime,
    pattern: str, bands: int = 3,
) -> List[dict]:
    """The intensity-only slice of "relate this with other state at
    formation time" (see indicator_state_analysis for the RSI/MACD/
    Stochastic slice) — does a stronger/weaker occurrence of a pattern
    actually play out differently? Joins InstrumentActivity.intensity to
    PatternOutcome's own columns for the SAME occurrence (matched on
    instrument_id/timeframe/pattern-name/timestamp — the two rows are
    always written for the identical detection, never approximately),
    sorts by intensity, and splits into `bands` equal-count groups (lowest
    intensity band first) — a caller renders these as "Low/Mid/High
    intensity" or similar. Each band reports its actual-result match rate
    at EVERY checkpoint (5/10/15/20/30) plus one overall price-range/up/
    down median (see _band_stats).

    Returns [] if this pattern has no analyzed PatternOutcome rows in
    scope yet (pattern_outcome_analysis.py hasn't run) or none of them
    have an intensity value (some pattern types have no intensity formula
    at all — see InstrumentActivity.intensity's own docstring). Thin
    wrapper: fetch_intensity_paired (one query) + _banded_from_paired
    (pure) — see InstrumentHistoryCache for the batch/in-memory
    equivalent that skips the query entirely."""
    paired = fetch_intensity_paired(session, instrument_id, timeframes, ts_from, ts_to, patterns=[pattern])
    return _banded_from_paired(paired, bands)


def _band_outcomes_from_paired(paired: List[Tuple[float, PatternOutcome]], bands: int = 3) -> List[List[PatternOutcome]]:
    """Sibling of _banded_from_paired that returns each band's own raw
    PatternOutcome list instead of aggregated stats — added 2026-09-16 for
    guiding_scenarios.py, which needs a qualifying band's own occurrence
    subset to run indicator_state_from_outcomes over (the informational
    indicator-stat layer, scoped to just that band, not the whole pattern).
    Same equal-count banding, lowest-intensity-first, as _banded_from_paired
    — kept in exact parallel so the two never drift out of sync with each
    other's bucket boundaries."""
    if not paired:
        return []
    total_n = len(paired)
    band_size = math.ceil(total_n / bands)
    results: List[List[PatternOutcome]] = []
    for i in range(bands):
        chunk = paired[i * band_size:(i + 1) * band_size]
        if not chunk:
            continue
        results.append([outcome for _, outcome in chunk])
    return results


def pattern_level_analysis(
    session, instrument_id: int, timeframes: Sequence[str], ts_from: datetime, ts_to: datetime,
    pattern: str,
) -> Optional[dict]:
    """The un-banded fallback anchor for recommendation_engine.generate_
    recommendations' PRIMARY evaluation when a live signal has no intensity
    value (some pattern types have no intensity formula at all — see
    InstrumentActivity.intensity's own docstring, and intensity_banded_
    analysis's own "returns [] ... none of them have an intensity value").
    Plain PatternOutcome query, no InstrumentActivity join needed (unlike
    intensity_banded_analysis) since there's no intensity to sort/band by —
    just the overall win rate for every occurrence of this pattern in
    scope. Returns None if there are no analyzed PatternOutcome rows at
    all yet (pattern_outcome_analysis.py hasn't run for this instrument/
    pattern). Thin wrapper over fetch_outcomes (one query) + _band_stats
    (pure)."""
    outcomes = fetch_outcomes(session, instrument_id, timeframes, ts_from, ts_to, patterns=[pattern])
    if not outcomes:
        return None
    return _band_stats(outcomes)


# Display order for each indicator's state/trend labels — low-to-high (or
# otherwise natural) reading order (matches intensity_banded_analysis's
# own lowest-band-first convention), not just whatever order a dict
# happened to fill in.
_RSI_STATE_ORDER = ("oversold", "neutral", "overbought")
_MACD_STATE_ORDER = ("bearish", "bullish")
_STOCH_STATE_ORDER = ("oversold", "neutral", "overbought")
_TREND_ORDER = ("decreasing", "flat", "increasing")


def _group_by_label(outcomes: Sequence[PatternOutcome], label_of, order: Sequence[str]) -> List[dict]:
    """Shared grouping step for both indicator_state_analysis's state and
    trend dimensions — `label_of` extracts whichever column (entry_rsi_state
    vs. entry_rsi_trend, etc.) this call is banding by. Skips outcomes with
    no label (missing snapshot, or a warm-up/no-strong-majority case) and
    any label with zero occurrences, same as intensity_banded_analysis's
    own empty-bucket handling."""
    groups: Dict[str, List[PatternOutcome]] = defaultdict(list)
    for outcome in outcomes:
        label = label_of(outcome)
        if label is not None:
            groups[label].append(outcome)
    return [{"label": label, **_band_stats(groups[label])} for label in order if groups.get(label)]


def indicator_state_from_outcomes(rows: Sequence[PatternOutcome]) -> Dict[str, Dict[str, List[dict]]]:
    """Pure compute half of indicator_state_analysis — no query; shared by
    the query-per-call public function below and InstrumentHistoryCache."""
    return {
        "rsi": {
            "state": _group_by_label(rows, lambda o: o.entry_rsi_state, _RSI_STATE_ORDER),
            "trend": _group_by_label(rows, lambda o: o.entry_rsi_trend, _TREND_ORDER),
        },
        "macd": {
            "state": _group_by_label(rows, lambda o: o.entry_macd_state, _MACD_STATE_ORDER),
            "trend": _group_by_label(rows, lambda o: o.entry_macd_trend, _TREND_ORDER),
        },
        "stoch": {
            "state": _group_by_label(rows, lambda o: o.entry_stoch_state, _STOCH_STATE_ORDER),
            "trend": _group_by_label(rows, lambda o: o.entry_stoch_trend, _TREND_ORDER),
        },
    }


def indicator_state_analysis(
    session, instrument_id: int, timeframes: Sequence[str], ts_from: datetime, ts_to: datetime,
    pattern: str,
) -> Dict[str, Dict[str, List[dict]]]:
    """"We will relate these with the state of other indicators like RSI,
    MACD etc." (explicit instruction, 2026-09-15), extended the same day
    to also cover the "condition" half of that idea (backtesting_
    calibration_plan.md's own "Next level" note — RSI "increasing," not
    just its value) — the indicator slice alongside intensity_banded_
    analysis's own intensity slice: was this pattern's occurrence more/
    less reliable depending on RSI/MACD/Stochastic's STATE
    (oversold/neutral/overbought, bullish/bearish) or TREND
    (increasing/decreasing/flat over the last few candles) at the moment
    it fired? Bands here are the fixed, named labels (see indicators.py's
    classify_rsi/classify_macd/classify_stochastic/classify_series_trend),
    not equal-count terciles — RSI="oversold" means something specific
    regardless of how many occurrences happen to land there, unlike an
    intensity tercile boundary which is just wherever this particular
    sample happened to split.

    Reads PatternOutcome's own entry_rsi_state/entry_rsi_trend/
    entry_macd_state/entry_macd_trend/entry_stoch_state/entry_stoch_trend
    columns directly (explicit instruction: "store indicator state +
    values") — pattern_outcome_analysis.py already classified these once,
    at analysis time, so no join or reclassification happens here anymore.
    A row with a null state/trend (no CandleIndicators snapshot for that
    exact occurrence, still warming up, or no strong-majority trend) just
    doesn't contribute to that band — the whole function still returns
    normally.

    Returns {"rsi": {"state": [...], "trend": [...]}, "macd": {...},
    "stoch": {...}} — each list holding {"label": ..., **_band_stats(...)}
    in reading order, skipping any label with zero occurrences. An
    indicator/dimension with NO classified occurrences at all in scope
    comes back as an empty list, not an error. Thin wrapper: fetch_outcomes
    (one query) + indicator_state_from_outcomes (pure)."""
    rows = fetch_outcomes(session, instrument_id, timeframes, ts_from, ts_to, patterns=[pattern])
    return indicator_state_from_outcomes(rows)


class InstrumentHistoryCache:
    """Bulk-loads ALL PatternOutcome rows (and the InstrumentActivity.
    intensity join) for one instrument/timeframe-set/date-range ONCE, then
    serves intensity_banded_analysis/pattern_level_analysis/
    indicator_state_analysis/band_outcomes for AS MANY patterns as needed
    with ZERO further DB queries.

    Built for replay/batch scans (2026-09-15, explicit: "when we are doing
    replay, we should fetch whole bunch of historical result data in one
    go and then search in-memory") — the original query-per-call design
    meant evaluating many signals for one instrument cost one DB round
    trip PER analysis call; a real 292-signal x 3-RS test run took over 7
    minutes almost entirely on SSH-tunnel round-trip latency to the real
    SQL Server, not computation. This cache turns that into 2 queries
    total (one for fetch_outcomes, one for fetch_intensity_paired) no
    matter how many patterns/signals are evaluated against it afterward.

    Since the 2026-09-16 guiding-scenarios redesign, this cache's primary
    consumer is guiding_scenarios.generate_guiding_scenarios (the weekly
    batch job that mines PatternOutcome for winning bands, once per
    instrument/timeframe/window) rather than a live per-signal path —
    recommendation_engine.generate_recommendations itself no longer queries
    PatternOutcome at all live; it does a cheap indexed lookup against the
    small, precomputed GuidingScenario table instead (see
    guiding_scenarios.match_guiding_scenario). generate_recommendations_batch
    still exists for evaluating many already-known signals against that
    same cheap lookup in one pass."""

    def __init__(self, session, instrument_id: int, timeframes: Sequence[str], ts_from: datetime, ts_to: datetime):
        self._outcomes_by_pattern: Dict[str, List[PatternOutcome]] = defaultdict(list)
        for row in fetch_outcomes(session, instrument_id, timeframes, ts_from, ts_to):
            self._outcomes_by_pattern[row.pattern].append(row)

        self._paired_by_pattern: Dict[str, List[Tuple[float, PatternOutcome]]] = defaultdict(list)
        for intensity, row in fetch_intensity_paired(session, instrument_id, timeframes, ts_from, ts_to):
            self._paired_by_pattern[row.pattern].append((intensity, row))
        for pattern in self._paired_by_pattern:
            self._paired_by_pattern[pattern].sort(key=lambda pair: pair[0])

    def pattern_level_analysis(self, pattern: str) -> Optional[dict]:
        outcomes = self._outcomes_by_pattern.get(pattern)
        return _band_stats(outcomes) if outcomes else None

    def intensity_banded_analysis(self, pattern: str, bands: int = 3) -> List[dict]:
        return _banded_from_paired(self._paired_by_pattern.get(pattern, []), bands)

    def band_outcomes(self, pattern: str, bands: int = 3) -> List[List[PatternOutcome]]:
        """Each intensity band's own raw PatternOutcome list — for
        guiding_scenarios.py's informational indicator-stat layer, scoped
        to a qualifying band's own occurrence subset."""
        return _band_outcomes_from_paired(self._paired_by_pattern.get(pattern, []), bands)

    def indicator_state_analysis(self, pattern: str) -> Dict[str, Dict[str, List[dict]]]:
        return indicator_state_from_outcomes(self._outcomes_by_pattern.get(pattern, []))

    def patterns_in_scope(self) -> List[str]:
        """Every pattern with at least one analyzed PatternOutcome row in
        this cache's date range — a caller doesn't need to know pattern
        names upfront to evaluate "everything available"."""
        return list(self._outcomes_by_pattern.keys())


def persist_one(session_factory, row: dict) -> bool:
    """Returns whether a new row was actually inserted (False if it
    already existed)."""
    with session_factory() as session:
        try:
            with session.begin_nested():
                exists = session.query(PatternOutcome).filter_by(
                    instrument_id=row["instrument_id"], timeframe=row["timeframe"],
                    pattern=row["pattern"], detected_ts=row["detected_ts"],
                ).one_or_none()
                if exists is None:
                    session.add(PatternOutcome(**row))
                session.flush()
            session.commit()
            return exists is None
        except IntegrityError:
            session.rollback()
            return False
