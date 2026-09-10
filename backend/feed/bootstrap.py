from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from db.models import SubscribedSymbol
from db.session import session_scope

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
) -> None:
    thread = threading.Thread(target=_open_feed_socket, args=(broker, market_feed), daemon=True, name="broker-ws-feed")
    thread.start()

    _wait_for_socket_ready(broker, timeout=socket_ready_timeout)
    _hydrate(broker, market_feed, session_factory)
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
    This pokes at private attributes — acknowledged wart; a proper fix is adding
    a real on_open callback + threading.Event to DhanBroker itself, later.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ws = getattr(broker, "_ws", None)
        app = getattr(ws, "app", None) if ws is not None else None
        if app is not None and getattr(app, "sock", None) is not None:
            return
        time.sleep(0.1)


def _hydrate(broker, market_feed, session_factory) -> None:
    with session_scope(session_factory) as session:
        rows = session.query(SubscribedSymbol).filter_by(active=True).all()
        for row in rows:
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
