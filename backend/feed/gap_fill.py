from __future__ import annotations

import threading
from datetime import datetime, timezone

from db.models import CandleToday
from db.session import session_scope

import api.ws_live as ws_live

# 09:15 IST = 03:45 UTC, same calendar date both ways (no midnight rollover to
# worry about) — matches the UTC-throughout convention already established in
# feed/candle_aggregator.py and feed/bootstrap.py's EOD flush timer.
MARKET_OPEN_HOUR_UTC = 3
MARKET_OPEN_MINUTE_UTC = 45


def backfill_missing_candles(symbol, exchange_segment, security_id, rest_broker, session_factory, aggregator) -> None:
    """Fetches whatever 1-min candles are missing between the last one we have and
    now, and feeds them through the aggregator so 3-/5-min rollups backfill too.

    Runs synchronously — callers use spawn_backfill() to run this on a background
    thread instead, since a historical-data REST call shouldn't block a
    registration response or startup hydration. The whole body is one try/except
    (not just the historical fetch) — this runs unsupervised on a background
    thread, so any failure here (including the DB lookup itself) must never
    surface as an unhandled thread exception, matching the same "never crash"
    discipline as hydration and the broker's own rate-limit handling.
    """
    try:
        from_ts = _last_known_ts(session_factory, symbol, exchange_segment) or _todays_market_open()
        to_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        if from_ts >= to_ts:
            return  # already caught up — no message needed, avoids noisy "up to date" spam

        ws_live.broadcast_backfill_status(
            symbol, exchange_segment, "started",
            f"Fetching missing candles since {from_ts.isoformat().replace('+00:00', 'Z')}",
        )
        candles = rest_broker.get_historical_data(symbol, security_id, exchange_segment, "1min", from_ts, to_ts)
        for candle in candles:
            aggregator.ingest_historical_1min(symbol, exchange_segment, candle)
        ws_live.broadcast_backfill_status(symbol, exchange_segment, "done", f"Backfilled {len(candles)} candles")
    except Exception as e:
        ws_live.broadcast_backfill_status(symbol, exchange_segment, "failed", str(e))


def spawn_backfill(symbol, exchange_segment, security_id, rest_broker, session_factory, aggregator) -> None:
    threading.Thread(
        target=backfill_missing_candles,
        args=(symbol, exchange_segment, security_id, rest_broker, session_factory, aggregator),
        daemon=True, name=f"gap-fill-{symbol}",
    ).start()


def _last_known_ts(session_factory, symbol, exchange_segment):
    with session_scope(session_factory) as session:
        latest = (
            session.query(CandleToday.ts)
            .filter_by(symbol=symbol, exchange_segment=exchange_segment, timeframe="1min")
            .order_by(CandleToday.ts.desc())
            .first()
        )
        if latest is None:
            return None
        ts = latest[0]
        return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _todays_market_open() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=MARKET_OPEN_HOUR_UTC, minute=MARKET_OPEN_MINUTE_UTC, second=0, microsecond=0)
