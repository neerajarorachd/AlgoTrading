"""CandleIndicators reads/writes — see db/models.py's CandleIndicators for
what this table records and why (the per-candle indicator-value snapshot
Strategies will eventually evaluate conditions against)."""
from __future__ import annotations

from typing import List

from sqlalchemy.exc import IntegrityError

from db.models import CandleIndicators
from db.session import session_scope


def get_for_instrument(session, instrument_id: int, timeframe: str) -> List[CandleIndicators]:
    """Bulk read, same shape/ordering as LibActivities.get_for_instrument —
    a caller that needs to match rows up against another timestamped
    series (pattern_outcome_analysis.py's own activities+candles) builds
    its own ts-keyed dict from this rather than querying per-row."""
    return (
        session.query(CandleIndicators)
        .filter_by(instrument_id=instrument_id, timeframe=timeframe)
        .order_by(CandleIndicators.ts)
        .all()
    )


def get_for_instrument_range(session, instrument_id: int, timeframe: str, ts_from=None, ts_to=None) -> List[CandleIndicators]:
    """get_for_instrument, narrowed to a ts window — routes_candles.py's
    live-chart indicators endpoint uses this (unlike get_for_instrument's
    unbounded full-history read) since this table isn't pruned the way
    candles_today is, and a chart only ever needs however much range it's
    actually showing."""
    query = session.query(CandleIndicators).filter_by(instrument_id=instrument_id, timeframe=timeframe)
    if ts_from is not None:
        query = query.filter(CandleIndicators.ts >= ts_from)
    if ts_to is not None:
        query = query.filter(CandleIndicators.ts <= ts_to)
    return query.order_by(CandleIndicators.ts).all()


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
