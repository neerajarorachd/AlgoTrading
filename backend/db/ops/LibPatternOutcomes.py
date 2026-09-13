"""PatternOutcome writes — see db/models.py's PatternOutcome for what this
table records and why (a neutral "what happened after this pattern"
analysis, independent of PatternPrediction's own predicted stop/target)."""
from __future__ import annotations

from typing import List

from sqlalchemy.exc import IntegrityError

from db.models import PatternOutcome
from db.session import session_scope


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
