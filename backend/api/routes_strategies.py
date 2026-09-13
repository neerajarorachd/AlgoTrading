from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from db.models import Strategy, StrategyElement
from db.ops import LibStrategies as ops_strategies
from db.ops import LibStrategyElements as ops_elements

strategies_bp = Blueprint("strategies", __name__)

_VALID_GROUP_OPERATORS = {"AND", "OR"}
_VALID_CONDITION_OPERATORS = {">", ">=", "<", "<=", "==", "!="}
_STRATEGY_METADATA_FIELDS = ("name", "strategy_type", "description", "family", "parent_id")


def _serialize_element(row: StrategyElement) -> dict:
    return {
        "code": row.code, "element_type": row.element_type,
        "source": row.source, "description": row.description,
    }


def _serialize_strategy(row: Strategy) -> dict:
    return {
        "id": row.id, "name": row.name, "strategy_type": row.strategy_type,
        "description": row.description, "family": row.family, "parent_id": row.parent_id,
        "active": row.active,
    }


def _validate_tree(tree: dict, elements: dict) -> str | None:
    """Returns an error message, or None if the tree is well-formed. Checks
    structure (operator is AND/OR, every element_code — including a
    condition's compared_element_code — is a real, known StrategyElement)
    but doesn't yet enforce every numeric-vs-event operator nuance; that's
    left to the (not-yet-built) evaluator's own stricter checks."""
    if tree is None:
        return None
    if tree.get("operator") not in _VALID_GROUP_OPERATORS:
        return f"group operator must be AND or OR, got {tree.get('operator')!r}"
    for cond in tree.get("conditions", []):
        code = cond.get("element_code")
        if code not in elements:
            return f"unknown element_code: {code!r}"
        operator = cond.get("operator")
        if operator is not None and operator not in _VALID_CONDITION_OPERATORS:
            return f"unknown condition operator: {operator!r}"
        compared = cond.get("compared_element_code")
        if compared is not None and compared not in elements:
            return f"unknown compared_element_code: {compared!r}"
    for child in tree.get("groups", []):
        error = _validate_tree(child, elements)
        if error is not None:
            return error
    return None


@strategies_bp.get("/api/strategy-elements")
def list_strategy_elements():
    rows = ops_elements.get_all(g.db_session)
    return jsonify([_serialize_element(row) for row in rows])


@strategies_bp.get("/api/strategies")
def list_strategies():
    rows = ops_strategies.get_all(g.db_session)
    return jsonify([_serialize_strategy(row) for row in rows])


@strategies_bp.get("/api/strategies/<int:strategy_id>")
def get_strategy(strategy_id):
    row = ops_strategies.get_by_id(g.db_session, strategy_id)
    if row is None:
        return "", 404
    payload = _serialize_strategy(row)
    payload["tree"] = ops_strategies.get_tree(g.db_session, strategy_id)
    return jsonify(payload)


@strategies_bp.post("/api/strategies")
def create_strategy():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    strategy_type = body.get("strategy_type")
    if not name or not strategy_type:
        return jsonify({"error": "name and strategy_type are required"}), 400

    tree = body.get("tree")
    elements = {e.code for e in ops_elements.get_all(g.db_session)}
    error = _validate_tree(tree, elements)
    if error is not None:
        return jsonify({"error": error}), 400

    fields = {"name": name, "strategy_type": strategy_type}
    for key in ("description", "family", "parent_id"):
        if key in body:
            fields[key] = body[key]
    strategy_id = ops_strategies.create(g.db_session, fields, tree)

    row = ops_strategies.get_by_id(g.db_session, strategy_id)
    payload = _serialize_strategy(row)
    payload["tree"] = ops_strategies.get_tree(g.db_session, strategy_id)
    return jsonify(payload), 201


@strategies_bp.put("/api/strategies/<int:strategy_id>")
def update_strategy(strategy_id):
    row = ops_strategies.get_by_id(g.db_session, strategy_id)
    if row is None:
        return "", 404

    body = request.get_json(silent=True) or {}
    tree = body.get("tree")
    elements = {e.code for e in ops_elements.get_all(g.db_session)}
    error = _validate_tree(tree, elements)
    if error is not None:
        return jsonify({"error": error}), 400

    fields = {k: body[k] for k in _STRATEGY_METADATA_FIELDS if k in body}
    ops_strategies.update_fields(g.db_session, strategy_id, fields)
    ops_strategies.replace_tree(g.db_session, strategy_id, tree)

    updated = ops_strategies.get_by_id(g.db_session, strategy_id)
    payload = _serialize_strategy(updated)
    payload["tree"] = ops_strategies.get_tree(g.db_session, strategy_id)
    return jsonify(payload)


@strategies_bp.delete("/api/strategies/<int:strategy_id>")
def delete_strategy(strategy_id):
    row = ops_strategies.get_by_id(g.db_session, strategy_id)
    if row is None:
        return "", 404

    ops_strategies.delete(g.db_session, strategy_id)
    return "", 204
