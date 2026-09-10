from __future__ import annotations

from datetime import datetime

from flask import Blueprint, g, jsonify, request

from db.models import CandleToday

candles_bp = Blueprint("candles", __name__)

_VALID_TIMEFRAMES = {"1min", "3min", "5min"}


@candles_bp.get("/api/candles")
def get_candles():
    symbol = request.args.get("symbol")
    exchange_segment = request.args.get("exchange_segment")
    timeframe = request.args.get("timeframe", "1min")

    if not symbol or not exchange_segment:
        return jsonify({"error": "symbol and exchange_segment are required"}), 400
    if timeframe not in _VALID_TIMEFRAMES:
        return jsonify({"error": f"timeframe must be one of {sorted(_VALID_TIMEFRAMES)}"}), 400

    query = g.db_session.query(CandleToday).filter_by(
        symbol=symbol, exchange_segment=exchange_segment, timeframe=timeframe,
    )

    from_param = request.args.get("from")
    to_param = request.args.get("to")
    if from_param:
        query = query.filter(CandleToday.ts >= _parse_ts(from_param))
    if to_param:
        query = query.filter(CandleToday.ts <= _parse_ts(to_param))

    rows = query.order_by(CandleToday.ts).all()
    return jsonify([_serialize(row) for row in rows])


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _serialize(row: CandleToday) -> dict:
    ts = row.ts.replace(tzinfo=None) if row.ts.tzinfo else row.ts
    return {
        "ts": ts.isoformat() + "Z",
        "open": float(row.open_price),
        "high": float(row.high_price),
        "low": float(row.low_price),
        "close": float(row.close_price),
        "volume": row.volume,
    }
