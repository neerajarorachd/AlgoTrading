"""Fetch-if-stale gate for persistent historical candle storage
(db.models.CandleHistorical) — the "check the last date of available
data before backtesting, fetch from the broker if older" logic.

Pure raw-OHLCV persistence: indicator computation happens later, during a
backtest's own ActivityEngine replay (see the backtesting plan), not
here — so unlike the old system's Get_Day_Historical_Data (which computed
indicators inline, chunk by chunk, with dfPrev-tail continuity), there's
no indicator-continuity problem to solve in this module at all.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from db.ops import LibCandlesHistorical as ops_candles_historical
from db.session import session_scope

# Dhan's intraday history endpoint has a materially shorter practical range
# than its daily endpoint, so intraday fetches are chunked tighter.
_DAILY_CHUNK_DAYS = 365
_INTRADAY_CHUNK_DAYS = 90

Range = Tuple[datetime, datetime]


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _step_for_timeframe(timeframe: str) -> timedelta:
    if timeframe == "1day":
        return timedelta(days=1)
    if timeframe.endswith("min"):
        return timedelta(minutes=int(timeframe[:-3]))
    raise ValueError(f"Unknown timeframe: {timeframe!r}")


def _plan_fetch(
    start_date: datetime, end_date: datetime, coverage: Optional[Range], step: timedelta,
) -> Tuple[List[Range], Range]:
    """What needs fetching, and what HistoricalDataCoverage should become
    afterward, given the currently tracked coverage for this key (None, or
    (covered_from, covered_to)).

    - No coverage yet, or the request is fully disjoint from it (doesn't
      overlap or even touch it): fetch the whole [start_date, end_date] and
      REPLACE coverage with it. A disjoint request can't safely be merged
      into a single tracked interval — see HistoricalDataCoverage's own
      docstring for why (deriving "coverage" from candle rows' own min/max
      instead would silently misreport an untouched gap between two
      islands as covered; this table avoids that by only ever growing
      contiguously).
    - The request is fully inside existing coverage: nothing to fetch.
    - The request overlaps or is adjacent to existing coverage: fetch only
      the non-covered edge(s) (up to two — the request can extend past
      both ends at once) and grow coverage to the union.
    """
    if coverage is None:
        return [(start_date, end_date)], (start_date, end_date)

    covered_from, covered_to = coverage
    if start_date >= covered_from and end_date <= covered_to:
        return [], coverage

    touches_existing = start_date <= covered_to + step and end_date >= covered_from - step
    if not touches_existing:
        return [(start_date, end_date)], (start_date, end_date)

    missing = []
    if start_date < covered_from:
        missing.append((start_date, covered_from - step))
    if end_date > covered_to:
        missing.append((covered_to + step, end_date))
    return missing, (min(start_date, covered_from), max(end_date, covered_to))


def _fetch_and_persist(
    session_factory, rest_broker, symbol: str, security_id: str, exchange_segment: str,
    timeframe: str, range_start: datetime, range_end: datetime,
) -> int:
    """Fetches [range_start, range_end] from the broker in date-range
    chunks (mirroring the old system's chunking, but without its pre-fetch
    "already have these dates" filter — this project's own convention,
    established in feed/gap_fill.py, is to trust the caller and let the
    unique constraint + persist_bulk's fallback handle any overlap).
    Returns how many candles were fetched."""
    chunk_days = _DAILY_CHUNK_DAYS if timeframe == "1day" else _INTRADAY_CHUNK_DAYS
    total_fetched = 0
    current_start = range_start
    while current_start <= range_end:
        current_end = min(current_start + timedelta(days=chunk_days - 1), range_end)
        candles = rest_broker.get_historical_data(
            symbol, security_id, exchange_segment, timeframe, current_start, current_end,
        )
        if candles:
            ops_candles_historical.persist_bulk(
                session_factory, [(symbol, exchange_segment, c) for c in candles],
            )
            total_fetched += len(candles)
        current_start = current_end + timedelta(days=1)
    return total_fetched


def ensure_data_available(
    session_factory, rest_broker, symbol: str, security_id: str, exchange_segment: str,
    timeframe: str, start_date: datetime, end_date: datetime,
) -> dict:
    """Checks the explicitly-tracked coverage for (symbol, exchange_segment,
    timeframe) (HistoricalDataCoverage, not CandleHistorical's own row
    timestamps — see that table's docstring for why); if it already spans
    [start_date, end_date], this is a no-op. Otherwise fetches whatever
    portion is actually missing from the broker's historical REST API and
    extends (or, for a disjoint request, replaces) the tracked coverage.
    Returns a small summary dict for logging/UI feedback."""
    start_date, end_date = _as_utc(start_date), _as_utc(end_date)
    step = _step_for_timeframe(timeframe)

    with session_scope(session_factory) as session:
        coverage = ops_candles_historical.get_coverage(session, symbol, exchange_segment, timeframe)
    # SQLite (tests) round-trips DateTime columns as naive, dropping
    # tzinfo — SQL Server (production) doesn't have this quirk, but
    # normalizing here matches the same defensive pattern feed/gap_fill.py
    # already uses for get_last_ts.
    if coverage is not None:
        coverage = (_as_utc(coverage[0]), _as_utc(coverage[1]))

    missing, new_coverage = _plan_fetch(start_date, end_date, coverage, step)
    if not missing:
        return {"fetched": 0, "from": None, "to": None, "already_covered": True}

    total_fetched = 0
    for range_start, range_end in missing:
        total_fetched += _fetch_and_persist(
            session_factory, rest_broker, symbol, security_id, exchange_segment,
            timeframe, range_start, range_end,
        )

    ops_candles_historical.set_coverage(session_factory, symbol, exchange_segment, timeframe, *new_coverage)

    return {
        "fetched": total_fetched,
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "already_covered": False,
    }
