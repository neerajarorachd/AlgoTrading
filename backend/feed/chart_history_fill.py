"""Fills past days' indicator rows and patterns into the DB, so the chart
can show the previous sessions with their indicators and markers, read
from the DB like today's (2026-10-08, live_pattern_store_and_grid_window_plan
step 4: "Store them in DB").

Only the live engine records rows, and only for today -- startup warm-up
(feed/engine_recovery.py) runs the 20-day history through the engine
WITHOUT recording. So past days have candles but mostly no indicators or
patterns. This replays a stock's stored 1-min history through a private
ActivityEngine and stores the rows for every past day in the window that
is missing them. Days already stored are only warmed through, and the
saves skip rows already stored (LibActivities/LibCandleIndicators
persist_bulk), so it is safe to run again.

Runs from eod_service's 15:30 pass, outside market hours, one stock at a
time; also runnable by hand (scripts/fill_chart_history.py).
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from activity_engine import ActivityEngine
from db.ops import LibCandleIndicators, LibSymbols
from db.session import session_scope
from feed.candle_aggregator import CandleAggregator
from feed.engine_recovery import TIMEFRAMES, WARMUP_DAYS, _history_1min, _utc, today_start_utc

logger = logging.getLogger(__name__)

# the chart shows the previous 20 SESSIONS plus today (ALGO-3) -- sessions,
# not calendar days: weekends and holidays don't count. History is read over
# engine_recovery.WARMUP_DAYS calendar days (enough for 20 sessions plus a
# few earlier ones that only warm the indicators up).
FILL_SESSIONS = 20
# a day counts as stored when it has indicator rows for at least this share
# of its candles -- a part-day (a restart mid-session before step 1 existed)
# gets filled in; the existing rows are skipped by the save
COMPLETE_SHARE = 0.9
FLUSH_EVERY_ROWS = 2_000

_IST = timedelta(hours=5, minutes=30)


def _ist_day(ts: datetime):
    return (_utc(ts) + _IST).date()


def _stored_days(session_factory, instrument_id: int, timeframe: str, since: datetime, before: datetime) -> Counter:
    with session_scope(session_factory) as session:
        rows = LibCandleIndicators.get_for_instrument_range(session, instrument_id, timeframe, since, before)
        return Counter(_ist_day(r.ts) for r in rows)


def fill_instrument(symbol: str, exchange_segment: str, session_factory, now: datetime | None = None) -> dict:
    """Stores indicator rows and patterns for each of the last FILL_SESSIONS
    sessions that lacks them. Returns a summary. Never touches today."""
    now = _utc(now or datetime.now(timezone.utc))
    day_start = today_start_utc(now)
    summary = {"days_filled": {}, "activities": 0, "candles": 0}

    with session_scope(session_factory) as session:
        instrument_id = LibSymbols.get_instrument_id(session, symbol, exchange_segment)
    if instrument_id is None:
        return summary

    history = _history_1min(session_factory, symbol, exchange_segment, day_start, days=WARMUP_DAYS)
    summary["candles"] = len(history)
    if not history:
        return summary

    # the last FILL_SESSIONS sessions; anything earlier only warms up
    sessions = sorted({_ist_day(c.timestamp) for c in history})[-FILL_SESSIONS:]
    window_start = datetime.combine(sessions[0], datetime.min.time(), tzinfo=timezone.utc) - _IST
    # candles per (timeframe, day), from the 1-min count -- 3/5-min are rolled up
    one_min_per_day = Counter(_ist_day(c.timestamp) for c in history if c.timestamp >= window_start)
    missing = {}
    for tf in TIMEFRAMES:
        stored = _stored_days(session_factory, instrument_id, tf, window_start, day_start)
        size = {"1min": 1, "3min": 3, "5min": 5}[tf]
        missing[tf] = {day for day, n in one_min_per_day.items() if stored.get(day, 0) < COMPLETE_SHARE * (n / size)}
    if not any(missing.values()):
        return summary

    engine = ActivityEngine(session_factory)

    def on_closed(sym, seg, candle):
        if candle.timestamp >= window_start and _ist_day(candle.timestamp) in missing[candle.timeframe]:
            engine.on_candle_closed(sym, seg, candle)
            if engine.buffered_count() >= FLUSH_EVERY_ROWS:
                summary["activities"] += engine.flush()
        else:
            engine.warm_up(sym, seg, candle)

    aggregator = CandleAggregator(on_candle_closed=on_closed)
    for candle in history:
        aggregator.ingest_historical_1min(symbol, exchange_segment, candle)
    aggregator.flush_all(as_of=day_start)
    summary["activities"] += engine.flush()
    summary["days_filled"] = {tf: sorted(d.isoformat() for d in days) for tf, days in missing.items() if days}
    return summary


def fill_all(session_factory, symbols, now: datetime | None = None) -> dict:
    """fill_instrument for each (symbol, exchange_segment); one failing stock
    is logged and skipped."""
    results = {}
    for symbol, exchange_segment in symbols:
        try:
            results[symbol] = fill_instrument(symbol, exchange_segment, session_factory, now=now)
            logger.info("Chart history: %s (%s) %s", symbol, exchange_segment,
                        {k: (len(v) if k == "days_filled" else v) for k, v in results[symbol].items()})
        except Exception:
            logger.exception("Chart history fill failed for %s (%s)", symbol, exchange_segment)
            results[symbol] = None
    return results
