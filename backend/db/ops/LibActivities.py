"""InstrumentActivity + PatternDefinition reads/writes."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from sqlalchemy.exc import IntegrityError

from db.models import InstrumentActivity, PatternDefinition
from db.session import session_scope


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
