"""Insert-only helpers for keeping `candles_historical` current (used by
scripts/update_intraday_candles.py and scripts/load_5min_from_pkl.py).

Kept in its own module (not LibCandlesHistorical.py) so it can be copied to
the VM without replacing that file. Rows are plain dicts:
    {"ts": naive-UTC datetime, "open": f, "high": f, "low": f, "close": f, "volume": int}
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from db.models import CandleHistorical
from db.ops import LibCandlesHistorical as ops
from db.session import session_scope

STEP = {"1min": timedelta(minutes=1), "3min": timedelta(minutes=3), "5min": timedelta(minutes=5)}
IST = timezone(timedelta(hours=5, minutes=30))


def naive_utc(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def ist_date(naive_utc_ts: datetime):
    return naive_utc_ts.replace(tzinfo=timezone.utc).astimezone(IST).date()


def last_ts(session_factory, symbol: str, segment: str, timeframe: str) -> Optional[datetime]:
    with session_scope(session_factory) as session:
        return ops.get_last_ts(session, symbol, segment, timeframe)


def has_rows(session_factory, symbol: str, segment: str, timeframe: str) -> bool:
    with session_scope(session_factory) as session:
        return session.query(CandleHistorical.id).filter_by(
            symbol=symbol, exchange_segment=segment, timeframe=timeframe).first() is not None


def insert_missing(session_factory, symbol: str, segment: str, timeframe: str,
                   rows: List[dict], batch: int = 10_000) -> int:
    """Inserts rows whose (symbol, segment, timeframe, ts) is not stored yet;
    existing rows are never changed. Returns how many were inserted."""
    if not rows:
        return 0
    lo, hi = min(r["ts"] for r in rows), max(r["ts"] for r in rows)
    with session_scope(session_factory) as session:
        have = {
            r[0] for r in session.query(CandleHistorical.ts).filter(
                CandleHistorical.symbol == symbol, CandleHistorical.exchange_segment == segment,
                CandleHistorical.timeframe == timeframe,
                CandleHistorical.ts >= lo, CandleHistorical.ts <= hi)
        }
    new = [
        {"symbol": symbol, "exchange_segment": segment, "timeframe": timeframe, "ts": r["ts"],
         "open_price": r["open"], "high_price": r["high"], "low_price": r["low"],
         "close_price": r["close"], "volume": int(r["volume"])}
        for r in rows if r["ts"] not in have
    ]
    with session_scope(session_factory) as session:
        for i in range(0, len(new), batch):
            session.execute(CandleHistorical.__table__.insert(), new[i:i + batch])
    return len(new)


def widen_coverage(session_factory, symbol: str, segment: str, timeframe: str,
                   first: datetime, last: datetime) -> None:
    """Records [first, last] (naive UTC) as fetched: sets it for a new key,
    widens it when it overlaps or touches the tracked range, and leaves a
    disjoint range alone (a single interval can't represent two islands)."""
    first, last = first.replace(tzinfo=timezone.utc), last.replace(tzinfo=timezone.utc)
    with session_scope(session_factory) as session:
        cov = ops.get_coverage(session, symbol, segment, timeframe)
    if cov is None:
        ops.set_coverage(session_factory, symbol, segment, timeframe, first, last)
        return
    c_from, c_to = (c if c.tzinfo else c.replace(tzinfo=timezone.utc) for c in cov)
    step = STEP[timeframe]
    if first <= c_to + step and last >= c_from - step:
        ops.set_coverage(session_factory, symbol, segment, timeframe, min(first, c_from), max(last, c_to))
