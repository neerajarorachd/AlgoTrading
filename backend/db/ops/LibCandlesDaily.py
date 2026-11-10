"""candles_daily reads/writes (see db/models_daily.py)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from brokers.models import Candle
from db.models_daily import CandleDaily
from db.session import session_scope

IST = timezone(timedelta(hours=5, minutes=30))


def candle_trade_date(candle: Candle) -> date:
    """Dhan stamps a daily candle at IST midnight of its trading day."""
    ts = candle.timestamp if candle.timestamp.tzinfo else candle.timestamp.replace(tzinfo=timezone.utc)
    return ts.astimezone(IST).date()


def get_last_date(session, symbol: str, exchange_segment: str) -> Optional[date]:
    row = (
        session.query(CandleDaily.trade_date)
        .filter_by(symbol=symbol, exchange_segment=exchange_segment)
        .order_by(CandleDaily.trade_date.desc())
        .first()
    )
    return row[0] if row else None


def get_first_date(session, symbol: str, exchange_segment: str) -> Optional[date]:
    row = (
        session.query(CandleDaily.trade_date)
        .filter_by(symbol=symbol, exchange_segment=exchange_segment)
        .order_by(CandleDaily.trade_date.asc())
        .first()
    )
    return row[0] if row else None


def get_close(session, symbol: str, exchange_segment: str, day: date) -> Optional[float]:
    row = (
        session.query(CandleDaily.close_price)
        .filter_by(symbol=symbol, exchange_segment=exchange_segment, trade_date=day)
        .first()
    )
    return float(row[0]) if row else None


def upsert_candles(session_factory, symbol: str, exchange_segment: str, candles: List[Candle]) -> int:
    """Insert new days and refresh days whose values changed (e.g. a partial
    candle stored earlier). One query for the existing rows in the range.
    Returns how many rows were inserted or changed."""
    if not candles:
        return 0
    by_date = {candle_trade_date(c): c for c in candles}
    with session_scope(session_factory) as session:
        existing = {
            r.trade_date: r
            for r in session.query(CandleDaily).filter(
                CandleDaily.symbol == symbol, CandleDaily.exchange_segment == exchange_segment,
                CandleDaily.trade_date >= min(by_date), CandleDaily.trade_date <= max(by_date),
            )
        }
        changed = 0
        for day, c in by_date.items():
            row = existing.get(day)
            if row is None:
                session.add(CandleDaily(
                    symbol=symbol, exchange_segment=exchange_segment, trade_date=day,
                    open_price=c.open, high_price=c.high, low_price=c.low,
                    close_price=c.close, volume=c.volume,
                ))
                changed += 1
            elif (float(row.open_price), float(row.high_price), float(row.low_price),
                  float(row.close_price), int(row.volume)) != (c.open, c.high, c.low, c.close, c.volume):
                row.open_price, row.high_price, row.low_price = c.open, c.high, c.low
                row.close_price, row.volume = c.close, c.volume
                row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
                changed += 1
    return changed
