"""Hit review (2026-10-16): why each of today's pattern hits / raw signals
fired, as recorded live by the engine (hit_explain). Opened from the counts in
the chart's Signals / Patterns tabs, as a Workspace tab.

GET /api/review?kind=pattern|signal&key=<code>&symbol=<sym>&exchange_segment=NSE_EQ
    one stock, every timeframe; add all=1 for every stock.
"""
from __future__ import annotations

from datetime import timedelta, timezone

from flask import Blueprint, current_app, jsonify, request

from signal_strength import STRENGTH_MEASURES

review_bp = Blueprint("review", __name__)

IST = timedelta(hours=5, minutes=30)
TIMEFRAMES = ("1min", "3min", "5min")


def _time(ts):
    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return (ts + IST).strftime("%H:%M")


def _iso(ts):
    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@review_bp.get("/api/review")
def get_review():
    kind = request.args.get("kind")
    code = request.args.get("key")
    symbol = request.args.get("symbol")
    segment = request.args.get("exchange_segment", "NSE_EQ")
    every_stock = request.args.get("all") in ("1", "true")
    if kind not in ("pattern", "signal") or not code:
        return jsonify({"error": "kind (pattern|signal) and key are required"}), 400
    if not symbol and not every_stock:
        return jsonify({"error": "symbol is required (or all=1)"}), 400
    engine = current_app.extensions.get("activity_engine")
    evaluator = getattr(engine, "pattern_evaluator", None) if engine is not None else None

    def wanted(key):
        return every_stock or (key[0] == symbol and key[1] == segment)

    if kind == "pattern":
        p = next((x for x in (evaluator.patterns if evaluator else []) if x.key == code), None)
        source = evaluator.explanations if evaluator else {}
        hits = [{**{k: v for k, v in e.items() if k != "ts"}, "symbol": key[0], "time": _time(e["ts"]), "ts": _iso(e["ts"])}
                for key, entries in source.items() if wanted(key) for e in entries if e["pattern"] == code]
        definition = {
            "kind": "pattern", "key": code, "name": (p.name or p.key) if p else code,
            "type": p.pattern_type if p else None, "weight": p.weight if p else None,
            "conditions": [{"formula": f} for f in (p.formulas if p else [])],
            "formed_at": evaluator.threshold if evaluator else 0.8,
        }
    else:
        source = engine.signal_explanations if engine is not None else {}
        hits = [{**{k: v for k, v in e.items() if k != "ts"}, "symbol": key[0], "time": _time(e["ts"]), "ts": _iso(e["ts"])}
                for key, entries in source.items() if wanted(key) for e in entries if e["signal"] == code]
        strength = evaluator.strength if evaluator else {}
        definition = {
            "kind": "signal", "key": code, "name": code.replace("_", " ").capitalize(),
            "measures": [{"measure": m, "formula": f,
                          "minimum": {tf: strength.get(tf, {}).get(code, {}).get(m) for tf in TIMEFRAMES}}
                         for m, f in STRENGTH_MEASURES.get(code, {}).items()],
        }
    hits.sort(key=lambda h: (h["symbol"], h["ts"], h["timeframe"]))
    return jsonify({**definition, "symbol": None if every_stock else symbol, "hits": hits})
