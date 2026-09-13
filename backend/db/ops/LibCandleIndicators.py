"""CandleIndicators writes — see db/models.py's CandleIndicators for what
this table records and why (the per-candle indicator-value snapshot
Strategies will eventually evaluate conditions against)."""
from __future__ import annotations

from typing import List

from sqlalchemy.exc import IntegrityError

from db.models import CandleIndicators
from db.session import session_scope


def persist_bulk(session_factory, rows: List[dict]) -> None:
    """Optimistic bulk insert — same shape as LibActivities.persist_bulk:
    a fresh flush of newly-computed snapshots really is "all new rows,"
    so skip the per-row existence check and add everything in one flush,
    falling back to a per-row existence-checked insert only on conflict
    (a replay re-run over already-snapshotted candles)."""
    if not rows:
        return
    try:
        with session_scope(session_factory) as session:
            session.add_all([CandleIndicators(**row) for row in rows])
            session.flush()
    except IntegrityError:
        for row in rows:
            persist_one(session_factory, row)


def persist_one(session_factory, row: dict) -> None:
    with session_factory() as session:
        try:
            with session.begin_nested():
                exists = session.query(CandleIndicators).filter_by(
                    instrument_id=row["instrument_id"], timeframe=row["timeframe"], ts=row["ts"],
                ).one_or_none()
                if exists is None:
                    session.add(CandleIndicators(**row))
                session.flush()
            session.commit()
        except IntegrityError:
            session.rollback()
