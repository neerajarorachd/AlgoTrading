from __future__ import annotations

import logging
import threading
from datetime import datetime, time, timezone

from db.ops.LibCandles import get_last_ts, persist_bulk as persist_candles_bulk
from db.ops.LibSymbols import get_active
from db.session import session_scope

import api.ws_live as ws_live
from feed.engine_recovery import prepare_instrument

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
_in_progress_lock = threading.Lock()


def backfill_missing_candles(symbol, exchange_segment, security_id, rest_broker, session_factory, aggregator,
                             activity_engine=None) -> None:
    """Fetches whatever 1-min candles are missing between the last one we have and
    now, and feeds them through the aggregator so 3-/5-min rollups backfill too.

    The starting point is the later of: the last actual candle we have for
    *today* (_last_known_ts), or the last point a prior call already checked
    through (_last_scanned_through) — whether or not that call found any
    candles. Without the latter, an illiquid symbol with a genuinely empty
    stretch (or any other reason the broker returns no candles for a range)
    would never advance _last_known_ts, so the periodic scanner would re-ask
    the broker for the exact same already-checked range every cycle forever.
    Falls back to today's market open when neither is known yet.

    Runs synchronously — callers use spawn_backfill() to run this on a background
    thread instead, since a historical-data REST call shouldn't block a
    registration response or startup hydration. The whole body is one try/except
    (not just the historical fetch) — this runs unsupervised on a background
    thread, so any failure here (including the DB lookup itself) must never
    surface as an unhandled thread exception, matching the same "never crash"
    discipline as hydration and the broker's own rate-limit handling.
    """
    key = (symbol, exchange_segment)
    with _in_progress_lock:
        if key in _in_progress:
            logger.info("Backfill for %s (%s) already in progress elsewhere — skipping", symbol, exchange_segment)
            return
        _in_progress.add(key)

    try:
        market_open = _todays_market_open()
        cached = _last_scanned_through.get(key)
        if cached is not None and cached < market_open:
            cached = None  # stale watermark left over from a previous day
        last_known = _last_known_ts(session_factory, symbol, exchange_segment)
        candidates = [c for c in (last_known, cached) if c is not None]
        from_ts = max(candidates) if candidates else market_open
        to_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        if from_ts >= to_ts:
            return  # already caught up — no message needed, avoids noisy "up to date" spam

        ws_live.broadcast_backfill_status(
            symbol, exchange_segment, "started",
            f"Fetching missing candles since {from_ts.isoformat().replace('+00:00', 'Z')}",
        )
        fetched = rest_broker.get_historical_data(symbol, security_id, exchange_segment, "1min", from_ts, to_ts)
        # Dhan's intraday endpoint only takes DATE bounds, so it returns the
        # whole session no matter how small the gap. Found live 2026-10-06:
        # a ~65-minute gap came back as 242 candles, and re-saving the whole
        # day (bulk insert fails on the duplicates -> one-by-one upserts over
        # the tunnel) took ~4.5 min per symbol, so a restart left most
        # symbols off the live feed for ~an hour. Re-feeding already-seen
        # minutes through the aggregator also broke its chronological-order
        # assumption. Keep only minutes that are missing (after the last one
        # stored) and already closed (before the current, still-forming one).
        candles = [
            c for c in fetched
            if from_ts <= c.timestamp < to_ts and (last_known is None or c.timestamp > last_known)
        ]
        closed = []  # collect (symbol, exchange_segment, Candle) — 1-min plus any
        # 3-/5-min rollups they trigger — instead of letting each one open its own
        # DB round-trip; a few hundred historical candles at one-round-trip-apiece
        # over a networked DB was the actual bottleneck making backfill slow.
        #
        # Deliberately NOT broadcasting candle_closed per candle here (unlike
        # the live-tick path) — socketio's "threading" async_mode makes each
        # emit() a real blocking call, and a few hundred of them per symbol
        # was itself a major chunk of backfill's wall-clock time. The
        # frontend doesn't need incremental pushes for a historical catch-up
        # anyway — it does one full REST re-fetch when it sees the "done"
        # backfill_status below (CandleChart.jsx), which is both faster and
        # a cleaner update than hundreds of incremental ones.
        #
        # Every closed candle also goes through the ActivityEngine (2026-10-08)
        # -- backfilled minutes used to skip it, so patterns/indicators for any
        # stretch the live feed missed (startup, add-symbol, a WS drop) were
        # simply never computed. No per-activity broadcast or recommendation
        # for these: they're historical by the time they're seen, and the UI
        # re-fetches on the "done" status below.
        def _collect(sym, seg, c):
            closed.append((sym, seg, c))
            if activity_engine is not None:
                try:
                    activity_engine.on_candle_closed(sym, seg, c)
                except Exception:
                    logger.exception("Backfill: activity engine failed for %s (%s) at %s", sym, seg, c.timestamp)

        for candle in candles:
            aggregator.ingest_historical_1min(symbol, exchange_segment, candle, on_candle_closed=_collect)
        persist_candles_bulk(session_factory, closed)
        _last_scanned_through[key] = to_ts  # mark this range checked, even if candles came back empty
        ws_live.broadcast_backfill_status(symbol, exchange_segment, "done", f"Backfilled {len(candles)} candles")
    except Exception as e:
        logger.warning("Backfill failed for %s (%s): %s", symbol, exchange_segment, e)
        ws_live.broadcast_backfill_status(symbol, exchange_segment, "failed", str(e))
    finally:
        with _in_progress_lock:
            _in_progress.discard(key)


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
