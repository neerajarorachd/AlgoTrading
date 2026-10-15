"""Pattern table API (ALGO-31). System patterns plus the user's own; system
patterns are read-only (only `enabled` may change) -- edit a copy instead.

There is no login yet, so the "user" is owner_id=1 for every request; when
users arrive, owner_id comes from the session instead of USER_ID below."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request

from condition_evaluator import validate_condition
from db.ops import LibPatterns as ops_patterns
from db.ops import LibSystemSettings as ops_settings

patterns_bp = Blueprint("patterns", __name__)

USER_ID = 1  # single user until the users table exists


def _factory():
    return current_app.extensions["db_session_factory"]


@patterns_bp.get("/api/patterns")
def list_patterns():
    return jsonify({"patterns": ops_patterns.list_patterns(
        g.db_session, owner_id=USER_ID,
        enabled_only=request.args.get("enabled") == "1",
        pattern_type=request.args.get("type") or None,
        category=request.args.get("category") or None,
    )})


@patterns_bp.get("/api/patterns/<int:pattern_id>")
def get_pattern(pattern_id: int):
    p = ops_patterns.get_pattern(g.db_session, pattern_id)
    return (jsonify(p), 200) if p else (jsonify({"error": "pattern not found"}), 404)


@patterns_bp.post("/api/patterns")
def create_pattern():
    try:
        new_id = ops_patterns.create_pattern(_factory(), request.get_json(force=True) or {}, owner_id=USER_ID)
    except ops_patterns.PatternError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"id": new_id}), 201


@patterns_bp.put("/api/patterns/<int:pattern_id>")
def update_pattern(pattern_id: int):
    try:
        ops_patterns.update_pattern(_factory(), pattern_id, request.get_json(force=True) or {})
    except ops_patterns.PatternError as exc:
        status = 404 if "not found" in str(exc) else 403 if "read-only" in str(exc) else 400
        return jsonify({"error": str(exc)}), status
    return jsonify({"ok": True})


@patterns_bp.post("/api/patterns/<int:pattern_id>/copy")
def copy_pattern(pattern_id: int):
    body = request.get_json(silent=True) or {}
    try:
        new_id = ops_patterns.copy_pattern(_factory(), pattern_id, owner_id=USER_ID, key=body.get("key"))
    except ops_patterns.PatternError as exc:
        return jsonify({"error": str(exc)}), 404 if "not found" in str(exc) else 400
    return jsonify({"id": new_id}), 201


@patterns_bp.delete("/api/patterns/<int:pattern_id>")
def disable_pattern(pattern_id: int):
    """Patterns are disabled, never deleted -- hits and backtests refer to them."""
    try:
        ops_patterns.update_pattern(_factory(), pattern_id, {"enabled": False})
    except ops_patterns.PatternError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"ok": True})


RECKONER_PATH = Path(__file__).resolve().parents[2] / "docs" / "condition_ready_reckoner.md"


@patterns_bp.get("/api/condition-reckoner")
def get_condition_reckoner():
    """The condition language's ready reckoner (docs/condition_ready_reckoner.md),
    shown from the condition maker's "Ready reckoner" button (ALGO-34). The
    doc stays the single source -- the UI renders this text."""
    try:
        return jsonify({"markdown": RECKONER_PATH.read_text(encoding="utf-8")})
    except OSError:
        return jsonify({"error": "ready reckoner not found"}), 404


@patterns_bp.post("/api/patterns/validate-condition")
def validate_pattern_condition():
    """Live parse check for the pattern editor: {formula} -> {error|null}."""
    formula = (request.get_json(force=True) or {}).get("formula", "")
    return jsonify({"error": validate_condition(formula.strip(), None, None, ops_patterns.PATTERN_CONTEXT_NAMES)
                    if formula.strip() else "a condition is required"})


@patterns_bp.get("/api/pattern-settings")
def pattern_settings():
    values = ops_settings.load_by_keys(g.db_session, ["use_per_stock_weightage", "pattern_formed_threshold"])
    return jsonify({"use_per_stock_weightage": bool(values.get("use_per_stock_weightage", 0)),
                    "pattern_formed_threshold": values.get("pattern_formed_threshold", 0.8)})
