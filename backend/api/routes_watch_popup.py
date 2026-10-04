"""Market Watch buy/sell suggestion popup API — see watch_order_popup.py's
own module docstring for the data design."""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

import watch_order_popup
from db.ops import LibSymbols as ops_symbols

watch_popup_bp = Blueprint("watch_popup", __name__)

_VALID_TIMEFRAMES = {"1min", "3min", "5min"}


@watch_popup_bp.get("/api/instruments/<int:instrument_id>/order-popup")
def get_order_popup(instrument_id):
    if ops_symbols.get_by_id(g.db_session, instrument_id) is None:
        return jsonify({"error": "instrument not found"}), 404
    timeframe = request.args.get("timeframe", "3min")
    if timeframe not in _VALID_TIMEFRAMES:
        return jsonify({"error": f"timeframe must be one of {sorted(_VALID_TIMEFRAMES)}"}), 400
    ltp_param = request.args.get("ltp")
    if ltp_param is not None:
        try:
            ltp_param = float(ltp_param)
        except ValueError:
            return jsonify({"error": "ltp must be numeric"}), 400

    data = watch_order_popup.order_popup_data(g.db_session, instrument_id, timeframe, ltp=ltp_param)
    if data is None:
        return jsonify({"error": "no directional signal for this instrument yet"}), 404
    return jsonify(data)
