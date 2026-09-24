from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, g, jsonify, request

from condition_evaluator import describe_grammar, validate_condition
from db.models import Strategy, StrategyElement
from db.ops import LibStrategies as ops_strategies
from db.ops import LibStrategyElements as ops_elements
from strategy_generation import DEFAULT_TOP_N, generate_best_patterns_strategy

strategies_bp = Blueprint("strategies", __name__)

_VALID_GROUP_OPERATORS = {"AND", "OR"}
_VALID_CONDITION_OPERATORS = {">", ">=", "<", "<=", "==", "!="}
_STRATEGY_METADATA_FIELDS = ("name", "strategy_type", "description", "family", "parent_id")

# Order-management + SL/target formula fields (see Strategy's own docstring
# in db/models.py) — accepted on create/update and included in the GET
# response, same nullable/additive convention as the rest of this table.
# Split by how each needs (de)serializing: SQL Numeric columns come back as
# Decimal (real SQL Server/pyodbc) or float (SQLite) and need normalizing
# via _num(); Integer/Boolean/String columns pass through as-is; the 3 time
# columns need "HH:MM" string <-> datetime.time conversion.
_STRATEGY_NUMERIC_FIELDS = (
    "sl_atr_multiplier", "sl_fixed_value", "target_risk_reward_ratio", "target_fixed_value",
    "capital_per_trade", "round_trip_cost_rate",
    "order1_margin_multiplier", "order2_margin_multiplier", "order3_margin_multiplier",
    "max_daily_loss_pct", "max_fill_price_drift_pct", "min_avg_volume_multiple",
)
_STRATEGY_PLAIN_ORDER_MGMT_FIELDS = (
    "direction", "sl_formula_type", "target_formula_type",
    "max_vol_per_call", "max_orders_at_a_time", "daily_max_trade_count",
    "exit_at_loss", "exit_at_loss_count", "first_order_quantity", "pattern_filter",
    # Risk-management additions from the HINDCOPPER double_top investigation
    # (2026-09-17) — see Strategy's own column comments in db/models.py.
    "exit_on_macd_reversal", "win_streak_multipliers",
    "spread_fills", "max_fill_candles", "max_exit_candles",
    "liquidity_safety_divisor", "itemized_costs", "compounding",
    # Pre-entry liquidity gate, added 2026-09-18 -- see Strategy's own
    # column comment. min_avg_volume_multiple lives in the NUMERIC list
    # above (Numeric(8,4)); the lookback window itself is a plain int.
    "min_avg_volume_lookback",
)
_STRATEGY_ORDER_MGMT_FIELDS = _STRATEGY_NUMERIC_FIELDS + _STRATEGY_PLAIN_ORDER_MGMT_FIELDS
_STRATEGY_TIME_FIELDS = ("trading_start_time", "new_order_end_time", "trading_end_time")


def _serialize_element(row: StrategyElement) -> dict:
    return {
        "code": row.code, "element_type": row.element_type,
        "source": row.source, "description": row.description,
    }


def _num(value):
    """SQL Numeric columns come back as Decimal (real SQL Server/pyodbc) or
    float (SQLite in tests) — normalize either to a plain JSON-safe float."""
    return None if value is None else float(value)


def _time_str(value) -> str | None:
    return None if value is None else value.strftime("%H:%M")


def _parse_time_str(value: str):
    return None if value is None else datetime.strptime(value, "%H:%M").time()


def _serialize_strategy(row: Strategy) -> dict:
    payload = {
        "id": row.id, "name": row.name, "strategy_type": row.strategy_type,
        "description": row.description, "family": row.family, "parent_id": row.parent_id,
        "active": row.active,
    }
    for key in _STRATEGY_NUMERIC_FIELDS:
        payload[key] = _num(getattr(row, key))
    for key in _STRATEGY_PLAIN_ORDER_MGMT_FIELDS:
        payload[key] = getattr(row, key)
    for key in _STRATEGY_TIME_FIELDS:
        payload[key] = _time_str(getattr(row, key))
    return payload


def _order_mgmt_fields_from_body(body: dict) -> dict:
    fields = {k: body[k] for k in _STRATEGY_ORDER_MGMT_FIELDS if k in body}
    for key in _STRATEGY_TIME_FIELDS:
        if key in body:
            fields[key] = _parse_time_str(body[key])
    return fields


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
        if cond.get("left_formula"):
            # FORMULA leaf: validated by dry-running the real evaluator
            error = validate_condition(cond["left_formula"], cond.get("operator"), cond.get("right_formula"))
            if error is not None:
                return f"invalid formula condition: {error}"
            if cond.get("operator") is not None and cond["operator"] not in _VALID_CONDITION_OPERATORS:
                return f"unknown condition operator: {cond['operator']!r}"
            continue
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


@strategies_bp.get("/api/strategy-fields")
def get_strategy_fields():
    return jsonify(describe_grammar())


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
    fields.update(_order_mgmt_fields_from_body(body))
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
    fields.update(_order_mgmt_fields_from_body(body))
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


@strategies_bp.post("/api/strategies/generate-best-patterns")
def generate_best_patterns():
    """Creates or updates an "OR of best-performing patterns" Strategy —
    2026-09-15, explicit: "get the best performing patterns and create an
    oring strategy... it should be updated after every backtest... updated
    after every week." Idempotent by name: calling this again later (e.g.
    a scheduled weekly re-run, not built yet — no live deployment exists
    to run it on) updates the SAME Strategy row rather than creating a
    duplicate. body: {instrument_id, timeframe, strategy_name, top_n?,
    checkpoint?, strategy_type?}."""
    body = request.get_json(silent=True) or {}
    instrument_id = body.get("instrument_id")
    timeframe = body.get("timeframe")
    strategy_name = body.get("strategy_name")
    if not instrument_id or not timeframe or not strategy_name:
        return jsonify({"error": "instrument_id, timeframe, strategy_name are required"}), 400

    session_factory = current_app.extensions["db_session_factory"]
    result = generate_best_patterns_strategy(
        session_factory, instrument_id, timeframe, strategy_name,
        top_n=body.get("top_n", DEFAULT_TOP_N),
        strategy_type=body.get("strategy_type", "RS1_best_patterns"),
    )
    status = 201 if result["action"] == "created" else 200
    return jsonify(result), status
