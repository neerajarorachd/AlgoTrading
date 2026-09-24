"""InstrumentActivity + PatternDefinition reads/writes."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from db.models import InstrumentActivity, PatternDefinition
from db.session import session_scope


def get_for_instrument(session, instrument_id: int, timeframe: str) -> List[InstrumentActivity]:
    return (
        session.query(InstrumentActivity)
        .filter_by(instrument_id=instrument_id, timeframe=timeframe)
        .order_by(InstrumentActivity.ts)
        .all()
    )


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
                session.flush()
            session.commit()
        except IntegrityError:
            session.rollback()
