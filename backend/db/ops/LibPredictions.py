"""PatternPrediction reads/writes."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from db.models import PatternPrediction
from db.session import session_scope


def load_all_keys(session) -> List[Tuple[int, str, str, datetime]]:
    """Every (instrument_id, timeframe, pattern, detected_ts) ever recorded
    — a lightweight columns-only query, used to seed an in-memory
    idempotency set rather than re-checking the DB on every open."""
    return session.query(
        PatternPrediction.instrument_id, PatternPrediction.timeframe,
        PatternPrediction.pattern, PatternPrediction.detected_ts,
    ).all()


def load_pending(session) -> List[PatternPrediction]:
    return session.query(PatternPrediction).filter_by(outcome=None).all()


def insert_bulk(session_factory, rows: List[PatternPrediction]) -> List[Optional[int]]:
    """Optimistic bulk insert; falls back to inserting one row at a time on
    IntegrityError so a single conflict doesn't drop the rest of the batch
    — same shape as db.ops.LibActivities.persist_bulk. Returns each row's
    assigned id in the same order as `rows` (read while the session is
    still open — id access after session_scope's exit would raise
    DetachedInstanceError); None for a row that turned out to already
    exist (a concurrent writer, or the same replay re-run this is guarding
    against)."""
    if not rows:
        return []
    try:
        with session_scope(session_factory) as session:
            session.add_all(rows)
            session.flush()
            return [row.id for row in rows]
    except IntegrityError:
        return [insert_one(session_factory, row) for row in rows]


def insert_one(session_factory, row: PatternPrediction) -> Optional[int]:
    try:
        with session_scope(session_factory) as session:
            session.add(row)
            session.flush()
            return row.id
    except IntegrityError:
        return None


def update_outcomes_bulk(session_factory, updates: List[dict]) -> None:
    """Each update dict must include the row's `id` plus whichever fields
    are changing — passed straight to SQLAlchemy's bulk_update_mappings."""
    if not updates:
        return
    with session_scope(session_factory) as session:
        session.bulk_update_mappings(PatternPrediction, updates)
