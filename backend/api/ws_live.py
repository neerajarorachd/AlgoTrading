from __future__ import annotations

from flask_socketio import SocketIO, join_room, leave_room

from config import cors_origins

# threading mode (not eventlet/gevent) avoids monkey-patch friction with
# websocket-client's real OS threads, used by DhanBroker's own feed connection.
# At this system's scale (a handful of browser tabs) that tradeoff is a non-issue.
socketio = SocketIO(async_mode="threading")


def init_app(app) -> SocketIO:
    socketio.init_app(app, cors_allowed_origins=cors_origins())

    @socketio.on("subscribe_ticks")
    def _handle_subscribe(data):
        for room in (data or {}).get("rooms", []):
            join_room(room)

    @socketio.on("unsubscribe_ticks")
    def _handle_unsubscribe(data):
        for room in (data or {}).get("rooms", []):
            leave_room(room)

    return socketio


def room_for(exchange: str, symbol: str) -> str:
    return f"{exchange}:{symbol}"


def room_for_exchange_segment(exchange_segment: str, symbol: str) -> str:
    # ticks carry `exchange` (e.g. "NSE"), depth/candle events carry `exchange_segment`
    # (e.g. "NSE_EQ") — deriving the same room key from both keeps a single room per
    # instrument that a browser tab joins once and gets all three event types from.
    return room_for(exchange_segment.split("_", 1)[0], symbol)


def broadcast_tick(payload: dict) -> None:
    socketio.emit("tick", payload, room=room_for(payload["exchange"], payload["symbol"]))


def broadcast_depth(payload: dict) -> None:
    socketio.emit("depth", payload, room=room_for_exchange_segment(payload["exchange_segment"], payload["symbol"]))


def broadcast_backfill_status(symbol: str, exchange_segment: str, status: str, message: str) -> None:
    socketio.emit("backfill_status", {
        "type": "backfill_status", "symbol": symbol, "status": status, "message": message,
    }, room=room_for_exchange_segment(exchange_segment, symbol))


def broadcast_candle_closed(symbol: str, exchange_segment: str, candle) -> None:
    ts = candle.timestamp
    ts_str = ts.isoformat().replace("+00:00", "Z") if ts.tzinfo else ts.isoformat() + "Z"
    socketio.emit("candle_closed", {
        "type": "candle_closed",
        "symbol": symbol,
        "timeframe": candle.timeframe,
        "candle": {
            "ts": ts_str, "open": candle.open, "high": candle.high,
            "low": candle.low, "close": candle.close, "volume": candle.volume,
        },
    }, room=room_for_exchange_segment(exchange_segment, symbol))
