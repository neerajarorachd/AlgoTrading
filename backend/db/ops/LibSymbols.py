"""SubscribedSymbol reads/writes."""
from __future__ import annotations

from typing import List, Optional

from db.models import SubscribedSymbol


def get_active(session) -> List[SubscribedSymbol]:
    return (
        session.query(SubscribedSymbol)
        .filter_by(active=True)
        .order_by(SubscribedSymbol.symbol)
        .all()
    )


def get_by_natural_key(session, symbol: str, exchange: str, segment: str) -> Optional[SubscribedSymbol]:
    return (
        session.query(SubscribedSymbol)
        .filter_by(symbol=symbol, exchange=exchange, segment=segment)
        .one_or_none()
    )


def get_by_exchange_segment(session, symbol: str, exchange_segment: str) -> Optional[SubscribedSymbol]:
    return (
        session.query(SubscribedSymbol)
        .filter_by(symbol=symbol, exchange_segment=exchange_segment)
        .one_or_none()
    )


def get_instrument_id(session, symbol: str, exchange_segment: str) -> Optional[int]:
    row = get_by_exchange_segment(session, symbol, exchange_segment)
    return row.id if row is not None else None


def get_by_id(session, symbol_id: int) -> Optional[SubscribedSymbol]:
    return session.get(SubscribedSymbol, symbol_id)


def create(session, **fields) -> SubscribedSymbol:
    row = SubscribedSymbol(**fields)
    session.add(row)
    return row
