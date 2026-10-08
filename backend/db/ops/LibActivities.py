"""InstrumentActivity + PatternDefinition reads/writes."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from db.models import InstrumentActivity, InstrumentActivityDailyCount, PatternDefinition
from db.session import session_scope

# Local copy of the same constant every other module in this codebase
# defines for itself (order_backtest.py, condition_evaluator.py, ...) rather
# than importing one shared module — established convention here.
_IST_OFFSET = timedelta(hours=5, minutes=30)
_DAILY_COUNT_CATEGORIES = ("candle_pattern", "indicator")


def _trading_date(ts: datetime) -> date:
    return (ts + _IST_OFFSET).date()


def get_for_instrument(session, instrument_id: int, timeframe: str) -> List[InstrumentActivity]:
    return (
        session.query(InstrumentActivity)
        .filter_by(instrument_id=instrument_id, timeframe=timeframe)
        .order_by(InstrumentActivity.ts)
        .all()
    )


def get_for_instrument_range(
    session, instrument_id: int, timeframe: str, ts_from: Optional[datetime] = None, ts_to: Optional[datetime] = None,
) -> List[InstrumentActivity]:
    """get_for_instrument, narrowed to a ts window -- routes_candles.py's
    live-chart markers endpoint uses this rather than the unbounded read,
    same reasoning as LibCandleIndicators.get_for_instrument_range."""
    query = session.query(InstrumentActivity).filter_by(instrument_id=instrument_id, timeframe=timeframe)
    if ts_from is not None:
        query = query.filter(InstrumentActivity.ts >= ts_from)
    if ts_to is not None:
        query = query.filter(InstrumentActivity.ts <= ts_to)
    return query.order_by(InstrumentActivity.ts).all()


def get_daily_counts(session, instrument_id: int, trading_date: date) -> dict:
    """{"candle_pattern": N, "indicator": M} for one instrument/day, read
    straight from the stored rollup (see InstrumentActivityDailyCount's own
    docstring) -- the Market Watch row's count badges. A day with no row
    yet (nothing detected, or pre-dates this feature and hasn't been
    backfilled -- see scripts/backfill_activity_daily_counts.py) reads as
    all zeros, same "None/0 until there's data" convention as elsewhere."""
    row = session.query(InstrumentActivityDailyCount).filter_by(
        instrument_id=instrument_id, trading_date=trading_date,
    ).one_or_none()
    if row is None:
        return {"candle_pattern": 0, "indicator": 0}
    return {"candle_pattern": row.candle_pattern_count, "indicator": row.indicator_count}


def _bump_daily_counts(session, rows: List[dict]) -> None:
    """Aggregates the rows just inserted by (instrument_id, trading_date,
    category) and upserts InstrumentActivityDailyCount in the SAME
    transaction as the activity insert itself -- called from persist_bulk/
    persist_one below, never on its own."""
    deltas: "dict[Tuple[int, date, str], int]" = defaultdict(int)
    for row in rows:
        if row["activity_type"] not in _DAILY_COUNT_CATEGORIES:
            continue
        deltas[(row["instrument_id"], _trading_date(row["ts"]), row["activity_type"])] += 1
    if not deltas:
        return

    buckets = {(instrument_id, trading_date) for instrument_id, trading_date, _ in deltas}
    existing = {
        (r.instrument_id, r.trading_date): r
        for r in session.query(InstrumentActivityDailyCount).filter(
            InstrumentActivityDailyCount.instrument_id.in_({b[0] for b in buckets}),
            InstrumentActivityDailyCount.trading_date.in_({b[1] for b in buckets}),
        )
    }
    for (instrument_id, trading_date, activity_type), delta in deltas.items():
        row = existing.get((instrument_id, trading_date))
        if row is None:
            row = InstrumentActivityDailyCount(
                instrument_id=instrument_id, trading_date=trading_date,
                candle_pattern_count=0, indicator_count=0,
            )
            session.add(row)
            existing[(instrument_id, trading_date)] = row
        if activity_type == "candle_pattern":
            row.candle_pattern_count += delta
        else:
            row.indicator_count += delta


def get_recent(session, instrument_id: int, timeframe: Optional[str] = None, limit: int = 20) -> List[InstrumentActivity]:
    """Newest-first, for the Market Watch per-stock event list -- unlike
    get_for_instrument (one timeframe, chronological, used by analysis
    code), this optionally spans all of an instrument's timeframes at once
    (1/3/5min all fire independently) since a watch-page user wants "what
    just happened on this stock," not one timeframe's own series."""
    query = session.query(InstrumentActivity).filter_by(instrument_id=instrument_id)
    if timeframe is not None:
        query = query.filter_by(timeframe=timeframe)
    return query.order_by(InstrumentActivity.ts.desc()).limit(limit).all()


def count_by_pattern(
    session, instrument_id: int, timeframes: Sequence[str], ts_from: datetime, ts_to: datetime,
    patterns: Optional[Sequence[str]] = None,
) -> List[Tuple[str, str, int]]:
    """[(activity_type, activity, count), ...] for the given instrument/
    timeframes/date range — occurrence counts ("how many times did each
    pattern fire"), the raw material for backtesting type 1 ("just count
    the occurrences in a certain period"). No trade simulation involved —
    a pure GROUP BY over already-detected/persisted activities.

    `patterns`, when given, restricts to just those activity codes (e.g.
    ["doji", "hammer"]) — the user picks which formations to test rather
    than always getting every pattern back; omit for "all of them"."""
    query = (
        session.query(InstrumentActivity.activity_type, InstrumentActivity.activity, func.count(InstrumentActivity.id))
        .filter(InstrumentActivity.instrument_id == instrument_id)
        .filter(InstrumentActivity.timeframe.in_(timeframes))
        .filter(InstrumentActivity.ts >= ts_from, InstrumentActivity.ts <= ts_to)
    )
    if patterns:
        query = query.filter(InstrumentActivity.activity.in_(patterns))
    return query.group_by(InstrumentActivity.activity_type, InstrumentActivity.activity).all()


def intensity_values_by_pattern(
    session, instrument_id: int, timeframes: Sequence[str], ts_from: datetime, ts_to: datetime,
    patterns: Optional[Sequence[str]] = None,
) -> "dict[str, List[float]]":
    """Raw intensity values (InstrumentActivity.intensity — the wick/body
    or range/body ratio each detector already computed, see
    activity_engine.py's own *_INTENSITY dicts), grouped by pattern —
    NULLs excluded (undefined/infinite-ratio patterns, see the column's
    own docstring). A caller computes whatever summary it wants (the
    occurrence-backtest UI's "Intensity Median" column) from this list;
    this function only extracts, never aggregates, matching
    LibPatternOutcomes.raw_values_by_pattern's same "raw data" convention."""
    query = (
        session.query(InstrumentActivity.activity, InstrumentActivity.intensity)
        .filter(InstrumentActivity.instrument_id == instrument_id)
        .filter(InstrumentActivity.timeframe.in_(timeframes))
        .filter(InstrumentActivity.ts >= ts_from, InstrumentActivity.ts <= ts_to)
    )
    if patterns:
        query = query.filter(InstrumentActivity.activity.in_(patterns))

    by_pattern: "dict[str, List[float]]" = {}
    for activity, intensity in query.all():
        if intensity is None:
            continue
        by_pattern.setdefault(activity, []).append(float(intensity))
    return by_pattern


def seed_pattern_definitions(session_factory, catalog: Sequence[Tuple[str, str, str]]) -> None:
    """Idempotent upsert of a (code, kind, description) catalog into
    pattern_definitions — call once at app startup. Safe to call every
    time (existing rows are just updated in place)."""
    with session_scope(session_factory) as session:
        for code, kind, description in catalog:
            row = session.query(PatternDefinition).filter_by(code=code).one_or_none()
            if row is None:
                row = PatternDefinition(code=code, kind=kind, description=description)
                session.add(row)
            else:
                row.kind = kind
                row.description = description
                row.active = True


def persist_bulk(session_factory, rows: List[dict]) -> None:
    """Optimistic bulk insert — the common case (a fresh flush of newly
    detected activities) really is "all new rows," so skip the per-row
    existence check entirely and add everything in one flush. Same pattern
    as db.ops.LibCandles.persist_bulk, for the same reason: hundreds of
    individual SELECT-then-INSERT round trips was the actual cost, not the
    write itself."""
    try:
        with session_scope(session_factory) as session:
            session.add_all([InstrumentActivity(**row) for row in rows])
            session.flush()
            _bump_daily_counts(session, rows)
    except IntegrityError:
        # a concurrent writer (e.g. a backfill re-run touching the same
        # candles) already recorded one of these exact activities — fall
        # back to the slower existence-checked path per row so that one
        # conflict doesn't lose the rest of the flush
        for row in rows:
            persist_one(session_factory, row)


def persist_one(session_factory, row: dict) -> None:
    with session_factory() as session:
        try:
            with session.begin_nested():
                exists = session.query(InstrumentActivity).filter_by(
                    instrument_id=row["instrument_id"], timeframe=row["timeframe"],
                    ts=row["ts"], activity=row["activity"],
                ).one_or_none()
                if exists is None:
                    session.add(InstrumentActivity(**row))
                    _bump_daily_counts(session, [row])
                session.flush()
            session.commit()
        except IntegrityError:
            session.rollback()
