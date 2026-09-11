from __future__ import annotations

from typing import List, Tuple

from sqlalchemy.exc import IntegrityError

from brokers.models import Candle
from db.models import CandleToday
from db.session import session_scope


def _upsert(session, symbol: str, exchange_segment: str, candle: Candle) -> None:
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


def persist_candles_bulk(session_factory, candles: List[Tuple[str, str, Candle]]) -> None:
    """Insert many finalized candles in ONE DB round-trip in the common case.

    persist_candle (below) opens a fresh session per candle and does an
    existence-check SELECT before every INSERT/UPDATE — fine for the
    live-tick path (one candle closes per minute at most), but a backfill can
    hand this a few hundred historical candles (1-min plus their 3-/5-min
    rollups) at once. The existence check turned out to be the actual cost,
    not the session/commit overhead a first pass at this function fixed —
    550 candles means 550 individual SELECT round-trips before a single
    INSERT gets issued, over a networked DB (SSH tunnel to the Azure VM),
    at 40-70+ seconds for one symbol's backfill.

    Fast path: skip the per-row existence check entirely and add every
    candle as a plain INSERT in one flush — SQLAlchemy batches homogeneous
    INSERTs together, so this is genuinely few round-trips, not hundreds.
    This is safe to try optimistically because the overwhelmingly common
    case (a fresh backfill, or the periodic scanner's first pass over a
    range) really is "all new rows." The rare case — some of these candles
    already exist (a live tick got there first, or this exact backfill is
    being re-run) — surfaces as an IntegrityError on the whole flush; only
    then does this fall back to the slower, existence-checked upsert path
    per candle (reusing persist_candle's own race handling), so a real
    conflict still can't lose the rest of the batch.
    """
    if not candles:
        return
    try:
        with session_scope(session_factory) as session:
            session.add_all([
                CandleToday(
                    symbol=symbol, exchange_segment=exchange_segment,
                    timeframe=candle.timeframe, ts=candle.timestamp,
                    open_price=candle.open, high_price=candle.high,
                    low_price=candle.low, close_price=candle.close, volume=candle.volume,
                )
                for symbol, exchange_segment, candle in candles
            ])
            session.flush()
    except IntegrityError:
        for symbol, exchange_segment, candle in candles:
            persist_candle(session_factory, symbol, exchange_segment, candle)


def persist_candle(session_factory, symbol: str, exchange_segment: str, candle: Candle) -> None:
    """Upsert a finalized candle into candles_today, keyed by its natural key.

    session.merge() only dedupes by primary key, and these rows have no PK set
    until they exist — so upserting here means looking the row up by the actual
    unique constraint (symbol, exchange_segment, timeframe, ts) first. This is
    cheap insurance against a reconnect re-finalizing a boundary candle, or a
    live tick racing a concurrent backfill/rescan for the same candle — on an
    actual conflict (both saw "no row yet" and both tried to INSERT), the
    other writer's data is equivalent, so this one just backs off instead of
    raising.
    """
    with session_factory() as session:
        try:
            with session.begin_nested():
                _upsert(session, symbol, exchange_segment, candle)
                session.flush()
            session.commit()
        except IntegrityError:
            session.rollback()
