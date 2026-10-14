"""Market Watch colour grid API. One batched call for every active
instrument (never one request per row, same lesson already learned from
EventsCell's own count badges).

Default (ALGO-33, 2026-10-14): colours from the PATTERN scores the live
engine computes on every candle close -- see colour_scoring.py. `bucket` is
the colour key (dark_green ... red) so the grid's tiles, row dot, button and
status sort keep working off one field.

`?timeframe=all` (2026-10-14): each row also carries `by_timeframe`
({1min/3min/5min: colour result}); its own colour is the strongest of the
three, except that pink/red on ANY timeframe still wins (red over pink) --
an avoid signal isn't cancelled by a green on another timeframe.

`?mode=legacy` keeps the earlier raw-event momentum score (watch_scoring.py)
available -- replaced as the default, not deleted."""
from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

import watch_scoring
from colour_scoring import COLOUR_HEX, COLOUR_LABEL, COLOUR_VALUE, colour_for
from db.ops import LibPatterns
from db.ops import LibSymbols as ops_symbols
from db.ops import LibSystemSettings

watch_scores_bp = Blueprint("watch_scores", __name__)

_TIMEFRAMES = ("1min", "3min", "5min")
_VALID_TIMEFRAMES = set(_TIMEFRAMES)


def combine_timeframes(by_tf: dict) -> dict:
    """One row colour from the per-timeframe results (see module docstring)."""
    results = list(by_tf.values())
    for warning in ("red", "pink"):
        hit = [r for r in results if r["colour"] == warning]
        if hit:
            best = dict(max(results, key=lambda r: r["top_score"]))
            best.update(colour=warning, colour_value=COLOUR_VALUE[warning],
                        warnings=sorted({w for r in hit for w in r["warnings"]}))
            return best
    return dict(max(results, key=lambda r: (r["colour_value"], r["top_score"])))


@watch_scores_bp.get("/api/watch-scores")
def get_watch_scores():
    timeframe = request.args.get("timeframe", "3min")
    is_all = timeframe == "all"
    if timeframe not in _VALID_TIMEFRAMES and not (is_all and request.args.get("mode") != "legacy"):
        return jsonify({"error": f"timeframe must be one of {sorted(_VALID_TIMEFRAMES)} or all"}), 400
    try:
        window = int(request.args.get("window", watch_scoring.WINDOW_CANDLES))
    except ValueError:
        return jsonify({"error": "window must be an integer"}), 400
    if window <= 0:
        return jsonify({"error": "window must be positive"}), 400

    instruments = ops_symbols.get_active(g.db_session)
    engine = current_app.extensions.get("activity_engine")
    if request.args.get("mode") == "legacy":
        # the live engine's unflushed rows count too -- it only writes to the DB
        # at 15:30 IST, so without it the grid showed yesterday's activity all day
        scores = watch_scoring.score_instruments(
            g.db_session, [i.id for i in instruments], timeframe, window, engine=engine,
        )
        return jsonify({"scores": scores, "bucket_colors": watch_scoring.BUCKET_COLORS})

    evaluator = getattr(engine, "pattern_evaluator", None) if engine is not None else None
    settings = LibSystemSettings.load_by_keys(g.db_session, ["use_per_stock_weightage"])
    per_stock = bool(settings.get("use_per_stock_weightage", 0))
    stock_weights = LibPatterns.load_stock_weights(g.db_session) if per_stock else {}
    scores = []
    patterns = evaluator.patterns if evaluator else []
    threshold = evaluator.threshold if evaluator else 0.8
    for inst in instruments:
        def weight_of(p, iid=inst.id):
            if per_stock and (p.id, iid) in stock_weights:
                return stock_weights[(p.id, iid)]
            return p.weight

        def score_tf(tf, inst=inst, weight_of=weight_of):
            matches = evaluator.scores.get((inst.symbol, inst.exchange_segment, tf), {}) if evaluator else {}
            return colour_for(matches, patterns, threshold, weight_of)

        if is_all:
            by_tf = {tf: score_tf(tf) for tf in _TIMEFRAMES}
            result = {**combine_timeframes(by_tf), "by_timeframe": by_tf}
        else:
            result = score_tf(timeframe)
        scores.append({"instrument_id": inst.id, "bucket": result["colour"], **result})
    return jsonify({
        "scores": scores, "bucket_colors": COLOUR_HEX,
        "bucket_labels": COLOUR_LABEL, "bucket_values": COLOUR_VALUE, "mode": "patterns",
        "timeframe": timeframe,
    })
