"""Brings the ActivityEngine up to date for one instrument before it goes
live -- at startup (every watched stock), after a crash/restart, and when a
stock is added mid-session (2026-10-08, live_pattern_store_and_grid_window_plan:
"when a stock was added to the watchlist or when the system was on, patterns
etc will be calculated and marked for whole day and stored in db").

Order, per instrument:
  1. make sure the previous WARMUP_DAYS of 1-min history is stored
     (historical_data_service, fetched from the broker only if missing);
  2. warm the engine up on that history -- builds MA/EMA/MACD/RSI/swing
     state without recording anything;
  3. put today's already-stored patterns/indicator rows back into memory;
  4. today's stored candles: those the engine already processed before the
     restart (up to the latest stored indicator row) warm up only, the rest
     are processed and recorded.
The caller then backfills the still-missing minutes up to now through the
same engine (feed/gap_fill.py) and subscribes the live feed.

Skipped entirely when the engine already has state for the instrument (it
has been running in this process) -- re-feeding older candles would corrupt
its rolling state.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from brokers.models import Candle
from db.models import CandleIndicators, InstrumentActivity
from db.ops import LibActivities, LibCandleIndicators, LibCandles, LibCandlesHistorical, LibSymbols
from db.session import session_scope
from feed.candle_aggregator import CandleAggregator
import market_calendar
from historical_data_service import ensure_data_available

logger = logging.getLogger(__name__)

# calendar days of 1-min history fetched/kept for warm-up -- 35 covers the
# chart's previous 20 sessions (ALGO-3) plus a few warm-up sessions
WARMUP_DAYS = 35
TIMEFRAMES = ("1min", "3min", "5min")
_IST = timedelta(hours=5, minutes=30)


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def today_start_utc(now: datetime) -> datetime:
    """00:00 IST today, as UTC."""
    ist_day = (_utc(now) + _IST).date()
    return datetime.combine(ist_day, time(), tzinfo=timezone.utc) - _IST


def _to_candle(symbol: str, timeframe: str, row) -> Candle:
    return Candle(
        symbol=symbol, timeframe=timeframe, timestamp=_utc(row.ts),
        open=float(row.open_price), high=float(row.high_price), low=float(row.low_price),
        close=float(row.close_price), volume=int(row.volume or 0),
    )


def _row_dict(model, row) -> dict:
    out = {}
    for column in model.__table__.columns:
        if column.name == "id":
            continue
        value = getattr(row, column.key)
        out[column.key] = float(value) if isinstance(value, Decimal) else value
    return out


def ensure_warmup_history(session_factory, rest_broker, symbol, security_id, exchange_segment, now) -> None:
    """Step 1. Dhan's intraday endpoint takes date-only bounds with an
    exclusive end, so [today-WARMUP_DAYS, today) returns exactly the prior
    sessions. When older coverage already exists but ends before that window,
    the request is stretched back to touch it -- a disjoint request would
    REPLACE the tracked coverage (see historical_data_service._plan_fetch) and
    a stock's years of backtest history would read as missing."""
    end = datetime.combine((_utc(now) + _IST).date(), time(), tzinfo=timezone.utc)
    start = end - timedelta(days=WARMUP_DAYS)
    with session_scope(session_factory) as session:
        coverage = LibCandlesHistorical.get_coverage(session, symbol, exchange_segment, "1min")
    if coverage is not None and _utc(coverage[1]) < start:
        start = _utc(coverage[1])
    ensure_data_available(session_factory, rest_broker, symbol, security_id, exchange_segment, "1min", start, end)
    fill_history_holes(session_factory, rest_broker, symbol, security_id, exchange_segment, start, end)


# (symbol, exchange_segment) -> earlier days already re-asked in this process:
# a minute the broker never has (no trades) must not cost a call every restart
# of the same process -- one call per stock per process start at most
_history_checked: dict = {}
_CORE_MINUTES = 360  # 09:15-15:14 IST; Dhan has no 15:15-15:29 history for most stocks since 2026-08-03


def fill_history_holes(session_factory, rest_broker, symbol, security_id, exchange_segment, start, end) -> int:
    """Earlier days (ALGO-39): coverage is one range per stock, so a hole
    INSIDE an older day (the app was down for two hours, a restart skipped a
    minute) was never seen. Every trading day in [start, end) -- weekends and
    NSE holidays skipped, market_calendar.py -- missing any 1-min candle
    09:15-15:14 IST is re-fetched, all such days in ONE call; only the
    missing minutes are saved. Returns how many were saved."""
    key = (symbol, exchange_segment)
    checked = _history_checked.setdefault(key, set())
    days = [d for d in market_calendar.trading_days(market_calendar.ist_date(start), market_calendar.ist_date(end - timedelta(minutes=1)))
            if d not in checked]
    if not days:
        return 0
    with session_scope(session_factory) as session:
        have = {_utc(r.ts) for ops in (LibCandlesHistorical, LibCandles)
                for r in ops.get_range(session, symbol, exchange_segment, "1min", start, end)}
    holes = {d: set(market_calendar.session_minutes(d)[:_CORE_MINUTES]) - have for d in days}
    holes = {d: m for d, m in holes.items() if m}
    checked.update(days)
    if not holes:
        return 0
    first, last = min(holes), max(holes)
    fetched = rest_broker.get_historical_data(
        symbol, security_id, exchange_segment, "1min",
        datetime.combine(first, time(), tzinfo=timezone.utc), datetime.combine(last + timedelta(days=1), time(), tzinfo=timezone.utc))
    wanted = set().union(*holes.values())
    new = [(symbol, exchange_segment, c) for c in fetched if _utc(c.timestamp) in wanted]
    if new:
        LibCandlesHistorical.persist_bulk(session_factory, new)
    logger.info("Gap fill: %s (%s) earlier days %s..%s: %d hole minute(s), %d filled in one call",
                symbol, exchange_segment, first, last, len(wanted), len(new))
    return len(new)


def _history_1min(session_factory, symbol, exchange_segment, day_start, days: int = WARMUP_DAYS) -> list:
    """Prior sessions' 1-min candles (the last `days` calendar days):
    candles_historical plus whatever earlier-day rows candles_today still
    holds (it has no EOD archiver yet), de-duplicated by timestamp."""
    since = day_start - timedelta(days=days + 1)
    before = day_start - timedelta(seconds=1)
    by_ts = {}
    with session_scope(session_factory) as session:
        for ops in (LibCandles, LibCandlesHistorical):  # historical wins on overlap
            for row in ops.get_range(session, symbol, exchange_segment, "1min", since, before):
                by_ts[_utc(row.ts)] = _to_candle(symbol, "1min", row)
    return [by_ts[ts] for ts in sorted(by_ts)]


def prepare_instrument(
    symbol: str, exchange_segment: str, security_id: str, rest_broker, session_factory, activity_engine,
    now: datetime | None = None,
) -> dict:
    """Steps 1-4 above. Never raises -- a failure in any step is logged and
    the instrument still goes live (the engine just has less history).
    Returns a small summary for logging/tests."""
    summary = {"skipped": False, "warmup_candles": 0, "loaded_activities": 0, "loaded_indicators": 0,
               "replayed_warm": 0, "replayed_recorded": 0}
    if activity_engine is None or activity_engine.has_state(symbol, exchange_segment):
        summary["skipped"] = True
        return summary

    now = _utc(now or datetime.now(timezone.utc))
    day_start = today_start_utc(now)

    try:
        ensure_warmup_history(session_factory, rest_broker, symbol, security_id, exchange_segment, now)
    except Exception as exc:
        logger.warning("Recovery: history fetch failed for %s (%s), warming up on what's stored: %s",
                       symbol, exchange_segment, exc)

    try:
        history = _history_1min(session_factory, symbol, exchange_segment, day_start)
        # a private aggregator rolls the 1-min history up to 3/5-min exactly
        # like the live path does, feeding every closed candle to warm_up
        warm = CandleAggregator(on_candle_closed=activity_engine.warm_up)
        for candle in history:
            warm.ingest_historical_1min(symbol, exchange_segment, candle)
        if history:
            warm.flush_all(as_of=day_start)
        summary["warmup_candles"] = len(history)

        with session_scope(session_factory) as session:
            instrument_id = LibSymbols.get_instrument_id(session, symbol, exchange_segment)
            saved_acts, saved_inds, today_candles, watermark = [], [], {}, {}
            for tf in TIMEFRAMES:
                if instrument_id is not None:
                    acts = LibActivities.get_for_instrument_range(session, instrument_id, tf, day_start, None)
                    inds = LibCandleIndicators.get_for_instrument_range(session, instrument_id, tf, day_start, None)
                    saved_acts += [_row_dict(InstrumentActivity, r) for r in acts]
                    saved_inds += [_row_dict(CandleIndicators, r) for r in inds]
                    watermark[tf] = max((_utc(r.ts) for r in inds), default=None)
                today_candles[tf] = [_to_candle(symbol, tf, r)
                                     for r in LibCandles.get_range(session, symbol, exchange_segment, tf, day_start, None)]
        activity_engine.load_saved(saved_acts, saved_inds)
        summary["loaded_activities"], summary["loaded_indicators"] = len(saved_acts), len(saved_inds)

        for tf in TIMEFRAMES:
            mark = watermark.get(tf)
            for candle in today_candles[tf]:
                if mark is not None and candle.timestamp <= mark:
                    # today's already-recorded part: advance pattern state too (ALGO-32)
                    activity_engine.warm_up(symbol, exchange_segment, candle, evaluate_patterns=True)
                    summary["replayed_warm"] += 1
                else:
                    activity_engine.on_candle_closed(symbol, exchange_segment, candle)
                    summary["replayed_recorded"] += 1
    except Exception:
        logger.exception("Recovery: engine preparation failed for %s (%s)", symbol, exchange_segment)

    logger.info("Recovery: %s (%s) %s", symbol, exchange_segment, summary)
    return summary


def rebuild_today(
    symbol: str, exchange_segment: str, security_id: str, rest_broker, session_factory, activity_engine,
    now: datetime | None = None,
) -> dict:
    """Re-runs one instrument's day through the engine after gap fill saved
    minutes BEHIND its last candle (ALGO-39, user's choice 2026-11-02): its
    signals, patterns and indicator rows for today were computed without
    them. Drops its engine state and today rows (memory and DB), then runs
    prepare_instrument again, which warms up on history and records today
    from the stored candles.

    Holds the engine's flush lock and lock throughout, so a flush can't save
    the dropped rows and a live candle can't land in the half-built state;
    other stocks' live candles wait those few seconds. Never raises."""
    summary = {"skipped": True}
    if activity_engine is None:
        return summary
    now = _utc(now or datetime.now(timezone.utc))
    day_start = today_start_utc(now)
    try:
        with session_scope(session_factory) as session:
            instrument_id = LibSymbols.get_instrument_id(session, symbol, exchange_segment)
        with activity_engine._flush_lock, activity_engine._lock:
            dropped = activity_engine.forget_instrument(symbol, exchange_segment, instrument_id)
            if instrument_id is not None:
                LibActivities.delete_for_instrument_since(session_factory, instrument_id, day_start)
                LibCandleIndicators.delete_for_instrument_since(session_factory, instrument_id, day_start)
            summary = prepare_instrument(symbol, exchange_segment, security_id, rest_broker, session_factory,
                                         activity_engine, now=now)
            summary["dropped"] = dropped
    except Exception:
        logger.exception("Gap fill: day re-run failed for %s (%s)", symbol, exchange_segment)
    logger.info("Gap fill: re-ran today for %s (%s) %s", symbol, exchange_segment, summary)
    return summary
