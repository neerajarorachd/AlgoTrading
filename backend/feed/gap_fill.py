from __future__ import annotations

import logging
import threading
from datetime import datetime, time, timedelta, timezone

import market_calendar
from brokers.models import Candle
from db.ops.LibCandles import get_last_ts, get_range, persist_bulk as persist_candles_bulk
from db.ops.LibSymbols import get_active
from db.session import session_scope

import api.ws_live as ws_live
from feed.engine_recovery import prepare_instrument, rebuild_today

logger = logging.getLogger(__name__)

# 09:15 IST = 03:45 UTC, same calendar date both ways (no midnight rollover to
# worry about) — matches the UTC-throughout convention already established in
# feed/candle_aggregator.py and feed/bootstrap.py's EOD flush timer.
MARKET_OPEN_HOUR_UTC = 3
MARKET_OPEN_MINUTE_UTC = 45

# 15:30 IST market close = 10:00 UTC — same value feed/bootstrap.py's daily
# flush timer fires at, reused here to bound when the periodic scanner (below)
# actually does anything.
MARKET_CLOSE_HOUR_UTC = 10
MARKET_CLOSE_MINUTE_UTC = 0

_DEFAULT_SCAN_INTERVAL_SECONDS = 300

# In-memory "how far have we already checked" watermark, per (symbol,
# exchange_segment) — process-lifetime only, deliberately not persisted (see
# backfill_missing_candles docstring). Cleared implicitly by a process
# restart, which is correct: a fresh process should re-derive its starting
# point from real candle data, same as the original one-shot backfill.
_last_scanned_through: dict[tuple[str, str], datetime] = {}

# Guards against two callers backfilling the same symbol at once — e.g. the
# periodic scanner's tick landing while hydration's own startup backfill is
# still working through a dozen-plus symbols. Both calls are individually
# safe (idempotent, rate-limit-throttled), but running them concurrently for
# the same symbol just doubles the load on an already-throttled connection
# for no benefit — the second caller should skip, not queue up behind it.
_in_progress: set[tuple[str, str]] = set()

# Minutes the broker did not return (no trades, or Dhan's missing 15:15-15:29),
# per instrument for one IST day: (day, {minute, ...}). Only minutes 10+ minutes
# old count -- Dhan can lag on the newest ones (ALGO-39).
_known_absent: dict[tuple[str, str], tuple] = {}
_ABSENT_AFTER_MINUTES = 10
_in_progress_lock = threading.Lock()


def backfill_missing_candles(symbol, exchange_segment, security_id, rest_broker, session_factory, aggregator,
                             activity_engine=None) -> None:
    """Fills every missing 1-min candle of today -- ahead of the last stored
    one (the feed was down) AND behind it (a hole the live feed skipped, e.g.
    a minute around a restart) -- with ONE broker call (ALGO-39). Dhan's
    intraday endpoint takes date bounds and returns the whole session anyway,
    so the call covers every hole at once; only the missing minutes are kept.

    - Minutes after the last stored candle go through the live aggregator
      and the ActivityEngine in order, as before (3-/5-min rollups follow).
    - Minutes before it (holes) are saved, their 3-/5-min candles rebuilt
      from the day's 1-min, and the stock's whole day is re-run through the
      engine (engine_recovery.rebuild_today) so its signals, patterns and
      colours include them -- the user's choice, 2026-11-02.
    - Minutes Dhan doesn't return (no trades, or Dhan's missing 15:15-15:29)
      are remembered once they are 10+ minutes old, so the 5-minute scanner
      doesn't ask for them again; a closed day (weekend / NSE holiday, see
      market_calendar.py) is never requested at all.

    Runs synchronously -- callers use spawn_backfill() for a background
    thread. The whole body is one try/except: this runs unsupervised and
    must never surface as an unhandled thread exception.
    """
    key = (symbol, exchange_segment)
    with _in_progress_lock:
        if key in _in_progress:
            logger.info("Backfill for %s (%s) already in progress elsewhere — skipping", symbol, exchange_segment)
            return
        _in_progress.add(key)

    try:
        now = datetime.now(timezone.utc)
        today = market_calendar.ist_date(now)
        if not market_calendar.is_trading_day(today):
            return
        to_ts = now.replace(second=0, microsecond=0)
        market_open = _todays_market_open()
        stored = _stored_1min_today(session_factory, symbol, exchange_segment, market_open)
        absent = _known_absent.get(key)
        if absent is None or absent[0] != today:
            absent = _known_absent[key] = (today, set())
        missing = [m for m in market_calendar.missing_minutes(today, stored, until=to_ts) if m not in absent[1]]
        if not missing:
            return  # caught up -- no message, avoids "up to date" spam

        ws_live.broadcast_backfill_status(
            symbol, exchange_segment, "started",
            f"Fetching {len(missing)} missing minute(s) since {missing[0].isoformat().replace('+00:00', 'Z')}",
        )
        fetched = rest_broker.get_historical_data(symbol, security_id, exchange_segment, "1min", missing[0], to_ts)
        want = set(missing)
        got = sorted((c for c in fetched if c.timestamp in want), key=lambda c: c.timestamp)
        got_ts = {c.timestamp for c in got}
        settled = to_ts - timedelta(minutes=_ABSENT_AFTER_MINUTES)
        absent[1].update(m for m in missing if m not in got_ts and m < settled)

        last_known = max(stored) if stored else None
        holes = [c for c in got if last_known is not None and c.timestamp < last_known]
        ahead = [c for c in got if last_known is None or c.timestamp > last_known]

        closed = []  # (symbol, exchange_segment, Candle) -- 1-min plus rollups, saved in one go
        # Deliberately no candle_closed broadcast per candle (socketio's
        # threading mode makes each emit a blocking call); the UI re-fetches
        # on the "done" status below. Every closed candle goes through the
        # ActivityEngine (2026-10-08), with no per-activity broadcast.
        def _collect(sym, seg, c):
            closed.append((sym, seg, c))
            if activity_engine is not None and not holes:
                try:
                    activity_engine.on_candle_closed(sym, seg, c)
                except Exception:
                    logger.exception("Backfill: activity engine failed for %s (%s) at %s", sym, seg, c.timestamp)

        if holes:
            persist_candles_bulk(session_factory, [(symbol, exchange_segment, c) for c in holes])
        for candle in ahead:
            aggregator.ingest_historical_1min(symbol, exchange_segment, candle, on_candle_closed=_collect)
        persist_candles_bulk(session_factory, closed)
        if holes:
            rebuild_rollups(session_factory, symbol, exchange_segment, [c.timestamp for c in holes], last_known)
            if activity_engine is not None:
                # the day re-run covers the minutes ahead too (all saved by now)
                rebuild_today(symbol, exchange_segment, security_id, rest_broker, session_factory, activity_engine)
        _last_scanned_through[key] = to_ts
        ws_live.broadcast_backfill_status(symbol, exchange_segment, "done", f"Backfilled {len(got)} candles")
        if holes:
            logger.info("Backfill: %s (%s) filled %d hole minute(s) behind %s and re-ran its day",
                        symbol, exchange_segment, len(holes), last_known)
    except Exception as e:
        logger.warning("Backfill failed for %s (%s): %s", symbol, exchange_segment, e)
        ws_live.broadcast_backfill_status(symbol, exchange_segment, "failed", str(e))
    finally:
        with _in_progress_lock:
            _in_progress.discard(key)


def _stored_1min_today(session_factory, symbol, exchange_segment, market_open) -> list:
    with session_scope(session_factory) as session:
        return [(r.ts.replace(tzinfo=timezone.utc) if r.ts.tzinfo is None else r.ts)
                for r in get_range(session, symbol, exchange_segment, "1min", market_open, None)]


def rebuild_rollups(session_factory, symbol, exchange_segment, filled, last_known) -> int:
    """Re-saves the 3-/5-min candles whose bucket holds a filled minute, from
    the day's stored 1-min. Only buckets that ended before the last stored
    minute: the bucket still forming is the live aggregator's to emit."""
    if not filled:
        return 0
    first = min(filled)
    with session_scope(session_factory) as session:
        rows = get_range(session, symbol, exchange_segment, "1min", first - timedelta(minutes=5), last_known)
        ones = [Candle(symbol=symbol, timeframe="1min",
                       timestamp=r.ts.replace(tzinfo=timezone.utc) if r.ts.tzinfo is None else r.ts,
                       open=float(r.open_price), high=float(r.high_price), low=float(r.low_price),
                       close=float(r.close_price), volume=int(r.volume or 0)) for r in rows]
    out = []
    for tf, n in (("3min", 3), ("5min", 5)):
        buckets = {m - timedelta(minutes=m.minute % n) for m in filled}
        for start in sorted(buckets):
            if start + timedelta(minutes=n) > last_known:
                continue
            members = [c for c in ones if start <= c.timestamp < start + timedelta(minutes=n)]
            if members:
                out.append((symbol, exchange_segment, Candle(
                    symbol=symbol, timeframe=tf, timestamp=start, open=members[0].open,
                    high=max(c.high for c in members), low=min(c.low for c in members),
                    close=members[-1].close, volume=sum(c.volume for c in members))))
    persist_candles_bulk(session_factory, out)  # existing rows -> per-row upsert fallback
    return len(out)


def spawn_backfill(symbol, exchange_segment, security_id, rest_broker, session_factory, aggregator,
                   activity_engine=None) -> None:
    threading.Thread(
        target=backfill_missing_candles,
        args=(symbol, exchange_segment, security_id, rest_broker, session_factory, aggregator, activity_engine),
        daemon=True, name=f"gap-fill-{symbol}",
    ).start()


def backfill_then_subscribe(instrument, rest_broker, session_factory, aggregator, market_feed,
                            activity_engine=None) -> None:
    """Backfill a symbol's historical candles BEFORE subscribing it to the
    live feed, not after (which is what subscribe-then-backfill used to do).

    Ordering matters here: if the live feed is already subscribed while
    backfill is still working through older minutes, a live tick can finalize
    "now"'s 1-min candle before the backfill has ingested everything before
    it — ingest_historical_1min (and the 3-/5-min rollup logic built on top
    of it) assumes candles always arrive in chronological order, and a
    historical candle landing after a newer live one breaks that assumption.
    Doing backfill first, then subscribing, means the first live tick that
    can possibly arrive is already >= the backfill's own cutoff — no
    interleaving is possible by construction, not by locking.
    """
    # whole-day patterns/indicators first (warm-up history + today's stored
    # candles, see feed/engine_recovery.py), then the missing minutes up to
    # now through the same engine, then live
    prepare_instrument(
        instrument["symbol"], instrument["exchange_segment"], instrument["security_id"],
        rest_broker, session_factory, activity_engine,
    )
    backfill_missing_candles(
        instrument["symbol"], instrument["exchange_segment"], instrument["security_id"],
        rest_broker, session_factory, aggregator, activity_engine,
    )
    market_feed.subscribe(instrument)


def spawn_backfill_then_subscribe(instrument, rest_broker, session_factory, aggregator, market_feed,
                                  activity_engine=None) -> None:
    threading.Thread(
        target=backfill_then_subscribe,
        args=(instrument, rest_broker, session_factory, aggregator, market_feed, activity_engine),
        daemon=True, name=f"add-symbol-{instrument['symbol']}",
    ).start()


def _last_known_ts(session_factory, symbol, exchange_segment):
    """Latest 1-min candle timestamp for *today* only — candles_today has no EOD
    archiver yet (deferred, see CLAUDE.md), so a prior day's rows can still be
    sitting in the table on a fresh trading day. A stale yesterday-close
    timestamp must not be mistaken for "already caught up today," or a fresh
    day would never correctly start its backfill from this morning's market
    open. The date-cutoff comparison is done in Python (not pushed into the SQL
    filter) to sidestep any naive/aware datetime mismatch between what got
    stored and what gets compared — this table is small, one extra row read
    costs nothing."""
    with session_scope(session_factory) as session:
        ts = get_last_ts(session, symbol, exchange_segment, "1min")
        if ts is None:
            return None
        ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        return ts if ts >= _todays_market_open() else None


def _todays_market_open() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=MARKET_OPEN_HOUR_UTC, minute=MARKET_OPEN_MINUTE_UTC, second=0, microsecond=0)


def start_gap_scanner(
    rest_broker, session_factory, aggregator, interval_seconds: int = _DEFAULT_SCAN_INTERVAL_SECONDS,
    activity_engine=None,
) -> None:
    """Periodically re-checks every active symbol for a gap since its last known
    candle and backfills it, on a recurring background timer.

    The one-shot backfill (spawn_backfill, above) only ever runs once per symbol,
    at subscribe/hydration time — it has no way to notice a gap that opens up
    *while already subscribed* (a WS drop-and-reconnect, a missed tick run, etc.).
    This closes that hole by re-running the same backfill_missing_candles check on
    a timer; it's already idempotent (a caught-up symbol is a cheap no-op), so
    there's no new machinery here beyond calling it again periodically.
    """
    def _tick():
        try:
            scan_for_gaps(rest_broker, session_factory, aggregator, activity_engine=activity_engine)
        except Exception:
            logger.exception("Gap scanner: unexpected error during a scan cycle")
        finally:
            # reschedule even on failure — one bad cycle must never silently end
            # the recurring scan for the rest of the trading day
            timer = threading.Timer(interval_seconds, _tick)
            timer.daemon = True
            timer.start()

    timer = threading.Timer(interval_seconds, _tick)
    timer.daemon = True
    timer.start()


def scan_for_gaps(rest_broker, session_factory, aggregator, now: datetime | None = None,
                  activity_engine=None) -> None:
    """One scan cycle: skip entirely outside market hours (nothing new to fetch,
    and the broker's historical API has nothing beyond the close anyway), else
    re-run the same backfill check used at subscribe time for every active
    symbol, sequentially — DhanBroker's own REST throttle already paces the
    calls, no need for a thread per symbol here."""
    now = now or datetime.now(timezone.utc)
    if not _is_market_hours(now):
        logger.info("Gap scanner: skipping cycle, outside market hours (%s UTC)", now.strftime("%H:%M:%S"))
        return

    with session_scope(session_factory) as session:
        targets = [
            (row.symbol, row.exchange_segment, row.security_id)
            for row in get_active(session)
        ]

    logger.info("Gap scanner: checking %d active symbol(s) for gaps", len(targets))
    for symbol, exchange_segment, security_id in targets:
        backfill_missing_candles(symbol, exchange_segment, security_id, rest_broker, session_factory, aggregator,
                                 activity_engine)
    logger.info("Gap scanner: cycle complete")


def _is_market_hours(now: datetime) -> bool:
    market_open = time(MARKET_OPEN_HOUR_UTC, MARKET_OPEN_MINUTE_UTC)
    market_close = time(MARKET_CLOSE_HOUR_UTC, MARKET_CLOSE_MINUTE_UTC)
    return market_open <= now.timetz().replace(tzinfo=None) <= market_close
