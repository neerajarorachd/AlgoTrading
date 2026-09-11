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
    """Upsert many finalized candles in ONE DB session/transaction.

    persist_candle (below) opens a fresh session per candle — fine for the
    live-tick path (one candle closes per minute at most), but a backfill can
    hand this a few hundred historical candles (1-min plus their 3-/5-min
    rollups) at once; one round-trip apiece over a networked DB (SSH tunnel to
    the Azure VM) made a single symbol's backfill take tens of seconds.

    Each candle's upsert runs in its own SAVEPOINT (session.begin_nested()),
    not the outer transaction directly — a live tick finalizing the same
    boundary candle at the same moment a backfill/rescan also writes it (both
    see "no row yet" and both try to INSERT) is a real race the SELECT-then-
    INSERT pattern can't fully prevent; without per-candle savepoints, one
    such conflict would fail the *entire* batch's commit, not just that one
    candle.
    """
    if not candles:
        return
    with session_scope(session_factory) as session:
        for symbol, exchange_segment, candle in candles:
            try:
                with session.begin_nested():
                    _upsert(session, symbol, exchange_segment, candle)
                    # this sessionmaker uses autoflush=False — without forcing
                    # the flush here, the pending INSERT wouldn't actually hit
                    # the DB (and thus couldn't raise IntegrityError) until
                    # some *later* flush/commit, by which point it's outside
                    # this candle's own savepoint/except below, and a
                    # conflict would take the whole remaining batch down
                    # with it instead of being caught right here.
                    session.flush()
            except IntegrityError:
                # a concurrent writer already persisted this exact candle
                # between our SELECT and INSERT — its data is equivalent
                # (same finalized OHLCV), nothing lost by skipping it here
                continue


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
