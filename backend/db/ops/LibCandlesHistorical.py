"""CandleHistorical reads/writes — persistent multi-day candle history
(1/3/5-min + "1day"), fed on demand by backend/historical_data_service.py.

Mirrors LibCandles.py's own shape almost exactly (same natural key, same
optimistic-bulk-insert-with-per-row-fallback strategy) — kept as a
separate module rather than parameterizing LibCandles.py over the table,
since the two tables have different write paths (live feed vs.
fetch-on-demand) and this project's convention is one Lib file per table.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from brokers.models import Candle
from db.models import CandleHistorical, HistoricalDataCoverage
from db.session import session_scope


def get_range(
    session, symbol: str, exchange_segment: str, timeframe: str,
    ts_from: Optional[datetime] = None, ts_to: Optional[datetime] = None,
) -> List[CandleHistorical]:
    query = session.query(CandleHistorical).filter_by(
        symbol=symbol, exchange_segment=exchange_segment, timeframe=timeframe,
    )
    if ts_from is not None:
        query = query.filter(CandleHistorical.ts >= ts_from)
    if ts_to is not None:
        query = query.filter(CandleHistorical.ts <= ts_to)
    return query.order_by(CandleHistorical.ts).all()


def get_last_ts(session, symbol: str, exchange_segment: str, timeframe: str) -> Optional[datetime]:
    row = (
        session.query(CandleHistorical.ts)
        .filter_by(symbol=symbol, exchange_segment=exchange_segment, timeframe=timeframe)
        .order_by(CandleHistorical.ts.desc())
        .first()
    )
    return row[0] if row is not None else None


def _upsert(session, symbol: str, exchange_segment: str, candle: Candle) -> None:
    existing = session.query(CandleHistorical).filter_by(
        symbol=symbol, exchange_segment=exchange_segment,
        timeframe=candle.timeframe, ts=candle.timestamp,
    ).one_or_none()

    if existing is None:
        existing = CandleHistorical(
            symbol=symbol, exchange_segment=exchange_segment,
            timeframe=candle.timeframe, ts=candle.timestamp,
        )
        session.add(existing)

    existing.open_price = candle.open
    existing.high_price = candle.high
    existing.low_price = candle.low
    existing.close_price = candle.close
    existing.volume = candle.volume


def persist_bulk(session_factory, candles: List[Tuple[str, str, Candle]]) -> None:
    """Same optimistic-insert-then-per-row-fallback strategy as
    LibCandles.persist_bulk — see that function's own docstring for why
    (avoiding one existence-check round trip per candle over the SSH
    tunnel is what actually matters at backfill-sized batches)."""
    if not candles:
        return
    try:
        with session_scope(session_factory) as session:
            session.add_all([
                CandleHistorical(
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
            persist_one(session_factory, symbol, exchange_segment, candle)


def persist_one(session_factory, symbol: str, exchange_segment: str, candle: Candle) -> None:
    """Upsert a single candle by its natural key — see LibCandles.persist_one."""
    with session_factory() as session:
        try:
            with session.begin_nested():
                _upsert(session, symbol, exchange_segment, candle)
                session.flush()
            session.commit()
        except IntegrityError:
            session.rollback()


def get_latest_day_range(
    session, symbol: str, exchange_segment: str, timeframe: str,
) -> List[CandleHistorical]:
    """All candles for the most recent calendar date that has any data for
    this symbol/timeframe -- used by routes_candles.py as a fallback when
    candles_today is empty (no live ticks yet today, e.g. before market
    open or on a non-trading day), so the live chart always has something
    to show instead of a blank canvas.
    """
    last_ts = get_last_ts(session, symbol, exchange_segment, timeframe)
    if last_ts is None:
        return []
    day_start = last_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = last_ts.replace(hour=23, minute=59, second=59, microsecond=999999)
    return get_range(session, symbol, exchange_segment, timeframe, ts_from=day_start, ts_to=day_end)


def get_previous_day_ohlc(
    session, symbol: str, exchange_segment: str, before: datetime,
) -> Optional[Tuple[float, float, float]]:
    """(high, low, close) of the most recent "1day" candle strictly before
    `before` -- the previous COMPLETE trading session, for classic pivot
    points (pivot_points.py). Reads the "1day" timeframe specifically
    (one row per session) rather than aggregating 1-min rows, matching
    this project's own "always fetch the full timeframe set, including
    1day" convention. None if no prior-day row exists yet (a freshly
    subscribed symbol with no historical backfill)."""
    row = (
        session.query(CandleHistorical)
        .filter_by(symbol=symbol, exchange_segment=exchange_segment, timeframe="1day")
        .filter(CandleHistorical.ts < before)
        .order_by(CandleHistorical.ts.desc())
        .first()
    )
    if row is None:
        return None
    return float(row.high_price), float(row.low_price), float(row.close_price)


def get_coverage(
    session, symbol: str, exchange_segment: str, timeframe: str,
) -> Optional[Tuple[datetime, datetime]]:
    row = session.query(HistoricalDataCoverage).filter_by(
        symbol=symbol, exchange_segment=exchange_segment, timeframe=timeframe,
    ).one_or_none()
    return (row.covered_from, row.covered_to) if row is not None else None


def set_coverage(
    session_factory, symbol: str, exchange_segment: str, timeframe: str,
    covered_from: datetime, covered_to: datetime,
) -> None:
    with session_factory() as session:
        row = session.query(HistoricalDataCoverage).filter_by(
            symbol=symbol, exchange_segment=exchange_segment, timeframe=timeframe,
        ).one_or_none()
        if row is None:
            row = HistoricalDataCoverage(symbol=symbol, exchange_segment=exchange_segment, timeframe=timeframe)
            session.add(row)
        row.covered_from = covered_from
        row.covered_to = covered_to
        session.commit()
