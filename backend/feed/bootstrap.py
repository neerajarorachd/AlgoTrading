from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from brokers.models import BrokerAPIError, BrokerConnectionError
from db.models import SubscribedSymbol
from db.session import session_scope

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
) -> None:
    """`broker` opens/owns the live feed socket; `rest_broker` (defaults to the
    same instance) is used for hydration's get_quote() calls — separate tokens
    for each duty in the real app, same instance for both in tests."""
    rest_broker = rest_broker if rest_broker is not None else broker
    thread = threading.Thread(target=_open_feed_socket, args=(broker, market_feed), daemon=True, name="broker-ws-feed")
    thread.start()

    _wait_for_socket_ready(broker, timeout=socket_ready_timeout)
    _hydrate(rest_broker, market_feed, session_factory)
    _schedule_daily_flush(aggregator, flush_hour_utc, flush_minute_utc)


def _open_feed_socket(broker, market_feed) -> None:
    """Runs forever on a dedicated background thread — never call this from a request thread.

    DhanBroker.subscribe_feed's first-ever call opens the WS connection via
    WebSocketApp.run_forever(), which blocks the calling thread for the life of the
    connection — and since that happens before the method sends its own subscribe
    message, that first call's own instrument list would be silently dropped. An
    empty list costs nothing here. Every later subscribe_feed call (real
    instruments, from hydration below or a live POST /api/symbols) is non-blocking.
    """
    broker.connect()
    broker.subscribe_feed([], market_feed._on_broker_tick)


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


def _hydrate(broker, market_feed, session_factory) -> None:
    """Re-subscribes every active symbol from a prior session.

    One row's broker call failing (rate limit, transient network issue, a
    since-delisted symbol) must not prevent every other registered symbol
    from coming back online, and must never crash the whole app at startup —
    log it and move on to the rest.
    """
    with session_scope(session_factory) as session:
        rows = session.query(SubscribedSymbol).filter_by(active=True).all()
        for row in rows:
            try:
                quote = broker.get_quote(row.symbol, row.security_id, row.exchange_segment)
                row.previous_close = quote.close
                market_feed.subscribe({
                    "security_id": row.security_id,
                    "exchange_segment": row.exchange_segment,
                    "symbol": row.symbol,
                    "exchange": row.exchange,
                    "segment": row.segment,
                    "previous_close": float(quote.close),
                    "ltp": float(quote.ltp),
                })
            except (BrokerAPIError, BrokerConnectionError) as exc:
                logger.warning("Hydration: skipping %s (%s) — %s", row.symbol, row.exchange_segment, exc)
            except Exception:
                logger.exception("Hydration: unexpected error subscribing %s (%s)", row.symbol, row.exchange_segment)


def _schedule_daily_flush(aggregator, hour_utc: int, minute_utc: int) -> None:
    def _flush_and_reschedule():
        aggregator.flush_all(as_of=datetime.now(timezone.utc))
        _schedule_daily_flush(aggregator, hour_utc, minute_utc)

    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=minute_utc, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    timer = threading.Timer((target - now).total_seconds(), _flush_and_reschedule)
    timer.daemon = True
    timer.start()
