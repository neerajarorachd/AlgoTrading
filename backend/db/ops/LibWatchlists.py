"""Watchlist / WatchlistInstrument reads/writes — a named group of
instruments a Strategy will eventually run against (attaching a Watchlist
to a Strategy is deferred until backtesting's own instrument-selection UI
is designed; this module only owns the Watchlist entity itself).

All functions take an already-open `session` — routes pass their own
request-scoped `g.db_session`, matching LibSymbols.py/LibStrategies.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func

from db.models import SubscribedSymbol, Watchlist, WatchlistInstrument


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_all(session) -> List[Watchlist]:
    return session.query(Watchlist).filter_by(active=True).order_by(Watchlist.name).all()


def get_by_id(session, watchlist_id: int) -> Optional[Watchlist]:
    return session.get(Watchlist, watchlist_id)


def get_member_counts(session, watchlist_ids: Sequence[int]) -> Dict[int, int]:
    """Active member count per watchlist id, one query — avoids an N+1
    (get_members per watchlist) when rendering the list view."""
    if not watchlist_ids:
        return {}
    rows = (
        session.query(WatchlistInstrument.watchlist_id, func.count(WatchlistInstrument.id))
        .filter(
            WatchlistInstrument.watchlist_id.in_(watchlist_ids),
            WatchlistInstrument.active == True,  # noqa: E712 — SQLAlchemy filter, not a Python bool check
        )
        .group_by(WatchlistInstrument.watchlist_id)
        .all()
    )
    return dict(rows)


def get_members(session, watchlist_id: int) -> List[Tuple[WatchlistInstrument, SubscribedSymbol]]:
    """Active members of one watchlist, joined to SubscribedSymbol so the
    caller has symbol/exchange to display without a second round trip."""
    return (
        session.query(WatchlistInstrument, SubscribedSymbol)
        .join(SubscribedSymbol, SubscribedSymbol.id == WatchlistInstrument.instrument_id)
        .filter(WatchlistInstrument.watchlist_id == watchlist_id, WatchlistInstrument.active == True)  # noqa: E712
        .order_by(SubscribedSymbol.symbol)
        .all()
    )


def create(session, fields: dict, instrument_ids: Optional[List[int]] = None) -> int:
    """Creates a Watchlist plus optional initial members. Returns the new
    watchlist's id."""
    watchlist = Watchlist(**fields)
    session.add(watchlist)
    session.flush()  # assigns watchlist.id
    for instrument_id in instrument_ids or []:
        add_member(session, watchlist.id, instrument_id)
    return watchlist.id


def update_fields(session, watchlist_id: int, fields: dict) -> None:
    if fields:
        session.query(Watchlist).filter_by(id=watchlist_id).update(fields)


def add_member(session, watchlist_id: int, instrument_id: int) -> WatchlistInstrument:
    """Idempotent: reactivates a previously-removed membership row instead
    of inserting a duplicate (the unique constraint is on the pair
    regardless of `active`, so a plain insert would conflict on re-add)."""
    existing = (
        session.query(WatchlistInstrument)
        .filter_by(watchlist_id=watchlist_id, instrument_id=instrument_id)
        .one_or_none()
    )
    if existing is not None:
        existing.active = True
        existing.added_at = _utcnow()
        return existing
    row = WatchlistInstrument(watchlist_id=watchlist_id, instrument_id=instrument_id)
    session.add(row)
    return row


def remove_member(session, watchlist_id: int, instrument_id: int) -> bool:
    """Soft-removes a member. Returns False if it wasn't an active member."""
    row = (
        session.query(WatchlistInstrument)
        .filter_by(watchlist_id=watchlist_id, instrument_id=instrument_id, active=True)
        .one_or_none()
    )
    if row is None:
        return False
    row.active = False
    return True


def delete(session, watchlist_id: int) -> bool:
    """Soft-deletes the watchlist itself. Returns False if it didn't exist."""
    row = session.get(Watchlist, watchlist_id)
    if row is None:
        return False
    row.active = False
    return True
