from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request

from brokers.models import BrokerAPIError, BrokerConnectionError
from db.ops import LibCandlesHistorical as ops_candles_historical
from db.ops import LibSymbols as ops_symbols
from historical_data_service import ensure_data_available

historical_data_bp = Blueprint("historical_data", __name__)


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


@historical_data_bp.get("/api/historical-data/coverage")
def get_coverage():
    instrument_id = request.args.get("instrument_id", type=int)
    timeframe = request.args.get("timeframe")
    if not instrument_id or not timeframe:
        return jsonify({"error": "instrument_id and timeframe are required"}), 400

    symbol_row = ops_symbols.get_by_id(g.db_session, instrument_id)
    if symbol_row is None:
        return jsonify({"error": f"Unknown instrument_id: {instrument_id}"}), 404

    last_ts = ops_candles_historical.get_last_ts(
        g.db_session, symbol_row.symbol, symbol_row.exchange_segment, timeframe,
    )
    return jsonify({"last_ts": last_ts.isoformat() if last_ts is not None else None})


@historical_data_bp.post("/api/historical-data/backfill")
def backfill():
    body = request.get_json(silent=True) or {}
    instrument_id = body.get("instrument_id")
    timeframe = body.get("timeframe")
    start_date = body.get("start_date")
    end_date = body.get("end_date")
    if not instrument_id or not timeframe or not start_date or not end_date:
        return jsonify({"error": "instrument_id, timeframe, start_date, end_date are required"}), 400

    symbol_row = ops_symbols.get_by_id(g.db_session, instrument_id)
    if symbol_row is None:
        return jsonify({"error": f"Unknown instrument_id: {instrument_id}"}), 404

    rest_broker = current_app.extensions["broker"]
    session_factory = current_app.extensions["db_session_factory"]
    try:
        summary = ensure_data_available(
            session_factory, rest_broker, symbol_row.symbol, symbol_row.security_id,
            symbol_row.exchange_segment, timeframe, _parse_date(start_date), _parse_date(end_date),
        )
    except (BrokerAPIError, BrokerConnectionError) as exc:
        return jsonify({"error": f"Broker historical fetch failed: {exc}"}), 502

    return jsonify(summary)
