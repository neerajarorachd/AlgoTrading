from __future__ import annotations

from datetime import datetime

from flask import Blueprint, g, jsonify, request

from db.models import CandleToday
from db.ops import LibCandles as ops_candles

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

    from_param = request.args.get("from")
    to_param = request.args.get("to")
    ts_from = _parse_ts(from_param) if from_param else None
    ts_to = _parse_ts(to_param) if to_param else None

    rows = ops_candles.get_range(
        g.db_session, symbol, exchange_segment, timeframe, ts_from=ts_from, ts_to=ts_to,
    )
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
