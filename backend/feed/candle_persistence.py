from __future__ import annotations

from brokers.models import Candle
from db.models import CandleToday
from db.session import session_scope


def persist_candle(session_factory, symbol: str, exchange_segment: str, candle: Candle) -> None:
    """Upsert a finalized candle into candles_today, keyed by its natural key.

    session.merge() only dedupes by primary key, and these rows have no PK set
    until they exist — so upserting here means looking the row up by the actual
    unique constraint (symbol, exchange_segment, timeframe, ts) first. This is
    cheap insurance against a reconnect re-finalizing a boundary candle.
    """
    with session_scope(session_factory) as session:
        existing = session.query(CandleToday).filter_by(
            symbol=symbol, exchange_segment=exchange_segment,
            timeframe=candle.timeframe, ts=candle.timestamp,
        ).one_or_none()

        if existing is None:
            existing = CandleToday(
                symbol=symbol, exchange_segment=exchange_segment,
                timeframe=candle.timeframe, ts=candle.timestamp,
            )
            session.add(existing)

        existing.open_price = candle.open
        existing.high_price = candle.high
        existing.low_price = candle.low
        existing.close_price = candle.close
        existing.volume = candle.volume
