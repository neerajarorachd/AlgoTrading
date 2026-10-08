from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from brokers.models import BrokerAPIError, BrokerConnectionError
from db.ops.LibSymbols import get_active
from db.session import session_scope
from feed.gap_fill import backfill_then_subscribe, start_gap_scanner

logger = logging.getLogger(__name__)

# 15:30 IST market close = 10:00 UTC. Candles are aggregated in UTC throughout
# (see feed/candle_aggregator.py), so the flush timer stays in UTC too rather
# than converting back and forth.
_DEFAULT_FLUSH_HOUR_UTC = 10
_DEFAULT_FLUSH_MINUTE_UTC = 0


def start_feed(
    broker, market_feed, session_factory, aggregator,
    flush_hour_utc: int = _DEFAULT_FLUSH_HOUR_UTC,
    flush_minute_utc: int = _DEFAULT_FLUSH_MINUTE_UTC,
    socket_ready_timeout: float = 5.0,
    rest_broker=None,
    activity_engine=None,
) -> None:
    """`broker` opens/owns the live feed socket; `rest_broker` (defaults to the
    same instance) is used for hydration's get_quote() calls — separate tokens
    for each duty in the real app, same instance for both in tests.
    `activity_engine`, if given, gets flushed to instrument_activity on the
    same EOD timer as the candle aggregator (see ActivityEngine's own
    docstring for why it buffers in memory rather than writing per
    detection) — optional so existing tests that don't care about it don't
    need to supply one.

    Connects and starts capturing immediately, unconditionally — this is
    exactly what session_scheduler.py's "starting mid-session" branch wants
    (e.g. starting the backend partway through market hours), and is kept
    as its own function, just now composed from connect_feed()/
    start_capture() below, for any other caller (tests) that wants the old
    all-at-once behavior unchanged."""
    rest_broker = rest_broker if rest_broker is not None else broker
    connect_feed(broker, market_feed, socket_ready_timeout=socket_ready_timeout)
    start_capture(
        rest_broker, market_feed, session_factory, aggregator,
        flush_hour_utc=flush_hour_utc, flush_minute_utc=flush_minute_utc,
        activity_engine=activity_engine,
    )


def connect_feed(broker, market_feed, socket_ready_timeout: float = 5.0) -> None:
    """Opens the WS connection and waits for it to be ready — no
    hydration/subscribe yet. Split out of start_feed() so
    session_scheduler.py can open the connection at 08:50 IST without
    starting to capture data until 09:08 IST."""
    thread = threading.Thread(target=_open_feed_socket, args=(broker, market_feed), daemon=True, name="broker-ws-feed")
    thread.start()
    _wait_for_socket_ready(broker, timeout=socket_ready_timeout)


def start_capture(
    rest_broker, market_feed, session_factory, aggregator,
    flush_hour_utc: int = _DEFAULT_FLUSH_HOUR_UTC,
    flush_minute_utc: int = _DEFAULT_FLUSH_MINUTE_UTC,
    activity_engine=None,
) -> None:
    """Hydrates/subscribes every active symbol, then wires up the existing
    EOD flush timer and gap scanner — unchanged from what start_feed()
    always did here, just now callable on its own so session_scheduler.py
    can trigger it at 09:08 IST specifically, after connect_feed() already
    opened the socket at 08:50."""
    _hydrate(rest_broker, market_feed, session_factory, aggregator=aggregator, activity_engine=activity_engine)
    _schedule_daily_flush(aggregator, flush_hour_utc, flush_minute_utc, activity_engine)
    if activity_engine is not None:
        _schedule_periodic_flush(activity_engine)
    start_gap_scanner(rest_broker, session_factory, aggregator, activity_engine=activity_engine)


def _open_feed_socket(broker, market_feed) -> None:
    """Runs forever on a dedicated background thread — never call this from a request thread.

    DhanBroker.subscribe_feed's first-ever call opens the WS connection via
    WebSocketApp.run_forever(), which blocks the calling thread for the life of the
    connection — and since that happens before the method sends its own subscribe
    message, that first call's own instrument list would be silently dropped. An
    empty list costs nothing here. Every later subscribe_feed call (real
    instruments, from hydration below or a live POST /api/symbols) is non-blocking.

    Exits quietly (info log) rather than an uncaught-thread traceback when
    run_forever() returns/raises because something deliberately closed the
    socket (session_scheduler.py's 15:30 stop) -- before a market-hours
    session scheduler existed this thread ran for the process lifetime and
    this never fired, so there was nothing to catch.
    """
    try:
        broker.connect()
        broker.subscribe_feed([], market_feed._on_broker_tick)
    except Exception:
        logger.info("broker-ws-feed thread exiting (feed stopped or connection closed)")


def _wait_for_socket_ready(broker, timeout: float) -> None:
    """Polls DhanBroker's private WS handle for readiness.

    self._ws is assigned before the blocking handshake inside run_forever()
    actually completes, so subscribing too early can race the real connection.
    Verified against the live Dhan feed (2026-09-10): checking only
    `app.sock is not None` is NOT sufficient — the sock object exists before
    the handshake finishes, and sending on it that early raises
    WebSocketConnectionClosedException. `sock.connected` is the reliable
    signal (confirmed against the real WS: a subscribe sent right after this
    check passes succeeds, and a real tick round-trips correctly end to end).
    This still pokes at private attributes — acknowledged wart; a proper fix
    is adding a real on_open callback + threading.Event to DhanBroker itself.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ws = getattr(broker, "_ws", None)
        app = getattr(ws, "app", None) if ws is not None else None
        sock = getattr(app, "sock", None) if app is not None else None
        if sock is not None and getattr(sock, "connected", False):
            return
        time.sleep(0.05)


def _hydrate(broker, market_feed, session_factory, aggregator=None, activity_engine=None) -> None:
    """Re-subscribes every active symbol from a prior session.

    One row's broker call failing (rate limit, transient network issue, a
    since-delisted symbol) must not prevent every other registered symbol
    from coming back online, and must never crash the whole app at startup —
    log it and move on to the rest.

    `aggregator` is optional so existing tests that only care about the
    subscribe behavior don't need to supply one — when given, each row is
    backfilled BEFORE it's subscribed to the live feed, not after (see
    feed/gap_fill.py's backfill_then_subscribe docstring for why order
    matters), sequentially on one background thread — a dozen-plus
    registered symbols each opening their own DB session on their own
    thread, all at once, was starving the connection pool and silently
    failing most of them.
    """
    targets = []
    with session_scope(session_factory) as session:
        rows = get_active(session)
        for row in rows:
            try:
                quote = broker.get_quote(row.symbol, row.security_id, row.exchange_segment)
                row.previous_close = quote.close
                instrument = {
                    "security_id": row.security_id,
                    "exchange_segment": row.exchange_segment,
                    "symbol": row.symbol,
                    "exchange": row.exchange,
                    "segment": row.segment,
                    "previous_close": float(quote.close),
                    "open": float(quote.open),
                    "ltp": float(quote.ltp),
                }
                if aggregator is None:
                    # no aggregator given (tests that only care about
                    # subscribe behavior) — nothing to backfill against, so
                    # just subscribe immediately, same as always
                    market_feed.subscribe(instrument)
                else:
                    targets.append(instrument)
            except (BrokerAPIError, BrokerConnectionError) as exc:
                logger.warning("Hydration: skipping %s (%s) — %s", row.symbol, row.exchange_segment, exc)
            except Exception:
                logger.exception("Hydration: unexpected error subscribing %s (%s)", row.symbol, row.exchange_segment)

    if aggregator is not None and targets:
        _backfill_and_subscribe_all(targets, broker, session_factory, aggregator, market_feed, activity_engine)


def _backfill_and_subscribe_all(targets, broker, session_factory, aggregator, market_feed, activity_engine=None) -> None:
    """Backfills, then subscribes, every symbol sequentially on one
    background thread — not spawn_backfill's one-thread-per-symbol (see
    _hydrate's docstring for why concurrent was actually causing failures,
    not just risk), and not subscribe-then-backfill (see
    feed/gap_fill.py's backfill_then_subscribe for the ordering reason)."""
    def _run():
        for i, instrument in enumerate(targets):
            logger.info("Hydration: [%d/%d] starting %s (%s)", i + 1, len(targets), instrument["symbol"], instrument["exchange_segment"])
            try:
                backfill_then_subscribe(instrument, broker, session_factory, aggregator, market_feed, activity_engine)
            except Exception:
                logger.exception(
                    "Hydration: [%d/%d] %s (%s) raised uncaught", i + 1, len(targets),
                    instrument["symbol"], instrument["exchange_segment"],
                )
            logger.info("Hydration: [%d/%d] live %s (%s)", i + 1, len(targets), instrument["symbol"], instrument["exchange_segment"])

    threading.Thread(target=_run, daemon=True, name="hydration-backfill").start()


PERIODIC_FLUSH_SECONDS = 15 * 60


def _schedule_periodic_flush(activity_engine, interval_seconds: int = PERIODIC_FLUSH_SECONDS) -> None:
    """Writes the ActivityEngine's not-yet-saved patterns/indicator rows to
    the DB every 15 minutes (2026-10-08: "store pattern data in db every 15
    mins") -- the engine keeps serving today's data from memory; this bounds
    what a crash can lose to 15 minutes, which restart recovery
    (feed/engine_recovery.py) then recomputes from stored candles. Once per
    engine: start_capture() runs again every trading day, and must not stack
    a second timer chain on the first."""
    if getattr(activity_engine, "_periodic_flush_scheduled", False):
        return
    activity_engine._periodic_flush_scheduled = True

    def _tick():
        try:
            n = activity_engine.flush()
            if n:
                logger.info("Periodic flush: wrote %d activity/activities (+ indicator rows)", n)
        except Exception:
            logger.exception("Periodic flush failed -- rows stay in memory, retried next cycle")
        finally:
            timer = threading.Timer(interval_seconds, _tick)
            timer.daemon = True
            timer.start()

    timer = threading.Timer(interval_seconds, _tick)
    timer.daemon = True
    timer.start()


def _schedule_daily_flush(aggregator, hour_utc: int, minute_utc: int, activity_engine=None, _rescheduling=False) -> None:
    # once per aggregator, same reason as _schedule_periodic_flush: the
    # session scheduler calls start_capture() every trading day, and each
    # call used to start another self-rescheduling chain of this timer
    if not _rescheduling:
        if getattr(aggregator, "_daily_flush_scheduled", False):
            return
        aggregator._daily_flush_scheduled = True

    def _flush_and_reschedule():
        try:
            aggregator.flush_all(as_of=datetime.now(timezone.utc))
            if activity_engine is not None:
                n = activity_engine.flush()
                if n:
                    logger.info("EOD flush: wrote %d buffered activity/activities to instrument_activity", n)
        except Exception:
            logger.exception("EOD flush failed")
        finally:
            _schedule_daily_flush(aggregator, hour_utc, minute_utc, activity_engine, _rescheduling=True)

    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=minute_utc, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    timer = threading.Timer((target - now).total_seconds(), _flush_and_reschedule)
    timer.daemon = True
    timer.start()
