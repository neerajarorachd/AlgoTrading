"""Recommendation Systems admin API — scan settings plus the RS's pattern
rules. A rule is NOT a new entity: it is a child Strategy (parent_id ==
the RS's parent strategy, pattern_filter = the patterns it covers), so rules
are created/edited/deleted through the ordinary /api/strategies endpoints
(the one shared StrategyDesigner); this API only lists them with the RS.
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from db.models import RecommendationSystem, RecommendationSystemPatternDefault, Strategy
from db.ops import LibRecommendationSystems as ops_rs
from db.ops import LibStrategies as ops_strategies
from pattern_defaults import FALLBACK_PATTERN
from rule_gate import summarize_tree

recommendation_systems_bp = Blueprint("recommendation_systems", __name__)

_VALID_TIMEFRAMES = {"1min", "3min", "5min"}
_VALID_COMBINE_MODES = {"all", "any"}
_PLAIN_FIELDS = ("name", "is_active", "watchlist_id", "timeframe", "top_n_per_run", "rule_combine_mode")
_OVERRIDE_FIELDS = (
    "fallback_capital",  # numeric setting; grouped here because it is serialized/validated the same way
    "win_pct_threshold_override", "min_sample_size_override", "wilson_confidence_override",
    "expiry_candles_override", "analysis_lookback_days_override", "max_regenerations_override",
    "analysis_start_time_minutes_override",
)


def _num(value):
    return None if value is None else float(value)


def _serialize(rs: RecommendationSystem) -> dict:
    payload = {
        "id": rs.id, "code": rs.code, "kind": rs.kind, "strategy_id": rs.strategy_id,
        "analysis_mode": rs.analysis_mode,
    }
    for key in _PLAIN_FIELDS:
        payload[key] = getattr(rs, key)
    for key in _OVERRIDE_FIELDS:
        payload[key] = _num(getattr(rs, key))
    return payload


def _rules(session, rs: RecommendationSystem) -> list:
    if rs.strategy_id is None:
        return []
    children = (
        session.query(Strategy).filter(Strategy.parent_id == rs.strategy_id, Strategy.active.is_(True))
        .order_by(Strategy.id).all()
    )
    return [
        {
            "id": c.id, "name": c.name,
            "patterns": [p.strip() for p in (c.pattern_filter or "").split(",") if p.strip()],
            "summary": summarize_tree(ops_strategies.get_tree(session, c.id) or {}),
        }
        for c in children
    ]


@recommendation_systems_bp.get("/api/recommendation-systems")
def list_systems():
    return jsonify([_serialize(rs) for rs in ops_rs.list_all(g.db_session)])


@recommendation_systems_bp.get("/api/recommendation-systems/<int:rs_id>")
def get_system(rs_id):
    rs = ops_rs.get_by_id(g.db_session, rs_id)
    if rs is None:
        return "", 404
    return jsonify({**_serialize(rs), "rules": _rules(g.db_session, rs)})


_SIZE_MODES = {"max_qty", "available_fund"}


def _serialize_default(d: RecommendationSystemPatternDefault) -> dict:
    return {
        "pattern": d.pattern, "size_mode": d.size_mode, "max_qty": d.max_qty,
        "fund_pct": _num(d.fund_pct), "sl_pct": _num(d.sl_pct), "target_pct": _num(d.target_pct),
    }


def _defaults(session, rs_id: int) -> list:
    rows = session.query(RecommendationSystemPatternDefault).filter_by(recommendation_system_id=rs_id).all()
    return [_serialize_default(d) for d in sorted(rows, key=lambda d: (d.pattern != FALLBACK_PATTERN, d.pattern))]


def _validate_default(d: dict):
    if d.get("size_mode") not in _SIZE_MODES:
        return f"size_mode must be one of {sorted(_SIZE_MODES)}"
    if d["size_mode"] == "max_qty" and not (isinstance(d.get("max_qty"), int) and d["max_qty"] > 0):
        return "max_qty must be a positive integer for size_mode max_qty"
    if d["size_mode"] == "available_fund" and not (d.get("fund_pct") and 0 < d["fund_pct"] <= 100):
        return "fund_pct must be between 0 and 100 for size_mode available_fund"
    for key in ("sl_pct", "target_pct"):
        if not (isinstance(d.get(key), (int, float)) and d[key] > 0):
            return f"{key} must be a positive number"
    return None


@recommendation_systems_bp.get("/api/recommendation-systems/<int:rs_id>/pattern-defaults")
def get_pattern_defaults(rs_id):
    if ops_rs.get_by_id(g.db_session, rs_id) is None:
        return "", 404
    return jsonify(_defaults(g.db_session, rs_id))


@recommendation_systems_bp.put("/api/recommendation-systems/<int:rs_id>/pattern-defaults")
def put_pattern_defaults(rs_id):
    """Replaces the RS's whole defaults list. body: {"defaults": [...]}; one
    row must have pattern "*" (the mandatory 'all other patterns' fallback)."""
    if ops_rs.get_by_id(g.db_session, rs_id) is None:
        return "", 404
    rows = (request.get_json(silent=True) or {}).get("defaults")
    if not isinstance(rows, list):
        return jsonify({"error": "defaults must be a list"}), 400
    patterns = [r.get("pattern") for r in rows]
    if FALLBACK_PATTERN not in patterns:
        return jsonify({"error": "a fallback row (pattern '*') is required"}), 400
    if len(set(patterns)) != len(patterns) or not all(patterns):
        return jsonify({"error": "each pattern may appear once, and none may be blank"}), 400
    for r in rows:
        error = _validate_default(r)
        if error:
            return jsonify({"error": f"{r['pattern']}: {error}"}), 400

    g.db_session.query(RecommendationSystemPatternDefault).filter_by(recommendation_system_id=rs_id).delete()
    for r in rows:
        g.db_session.add(RecommendationSystemPatternDefault(
            recommendation_system_id=rs_id, pattern=r["pattern"], size_mode=r["size_mode"],
            max_qty=r.get("max_qty") if r["size_mode"] == "max_qty" else None,
            fund_pct=r.get("fund_pct") if r["size_mode"] == "available_fund" else None,
            sl_pct=r["sl_pct"], target_pct=r["target_pct"],
        ))
    g.db_session.flush()
    return jsonify(_defaults(g.db_session, rs_id))


@recommendation_systems_bp.put("/api/recommendation-systems/<int:rs_id>")
def update_system(rs_id):
    rs = ops_rs.get_by_id(g.db_session, rs_id)
    if rs is None:
        return "", 404
    body = request.get_json(silent=True) or {}

    if "timeframe" in body and body["timeframe"] not in _VALID_TIMEFRAMES:
        return jsonify({"error": f"timeframe must be one of {sorted(_VALID_TIMEFRAMES)}"}), 400
    if "rule_combine_mode" in body and body["rule_combine_mode"] not in _VALID_COMBINE_MODES:
        return jsonify({"error": "rule_combine_mode must be 'all' or 'any'"}), 400
    if "top_n_per_run" in body and (not isinstance(body["top_n_per_run"], int) or body["top_n_per_run"] < 1):
        return jsonify({"error": "top_n_per_run must be a positive integer"}), 400
    if "name" in body and not str(body["name"]).strip():
        return jsonify({"error": "name cannot be empty"}), 400

    for key in _PLAIN_FIELDS + _OVERRIDE_FIELDS:
        if key in body:
            setattr(rs, key, body[key])
    g.db_session.flush()
    return jsonify({**_serialize(rs), "rules": _rules(g.db_session, rs)})
