"""Market Watch colored-cell grid API -- see watch_scoring.py's own module
docstring for the scoring design. One batched call for every active
instrument (never one request per row, same lesson already learned from
EventsCell's own count badges)."""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

import watch_scoring
from db.ops import LibSymbols as ops_symbols

watch_scores_bp = Blueprint("watch_scores", __name__)

_VALID_TIMEFRAMES = {"1min", "3min", "5min"}


@watch_scores_bp.get("/api/watch-scores")
def get_watch_scores():
    timeframe = request.args.get("timeframe", "3min")
    if timeframe not in _VALID_TIMEFRAMES:
        return jsonify({"error": f"timeframe must be one of {sorted(_VALID_TIMEFRAMES)}"}), 400
    try:
        window = int(request.args.get("window", watch_scoring.WINDOW_CANDLES))
    except ValueError:
        return jsonify({"error": "window must be an integer"}), 400
    if window <= 0:
        return jsonify({"error": "window must be positive"}), 400

    instruments = ops_symbols.get_active(g.db_session)
    scores = watch_scoring.score_instruments(g.db_session, [i.id for i in instruments], timeframe, window)
    return jsonify({"scores": scores, "bucket_colors": watch_scoring.BUCKET_COLORS})
