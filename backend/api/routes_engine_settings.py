"""Engine Settings admin API — reads/writes engine_settings (db/models.py's
EngineSetting), the calibrated-override table activity_engine.py's own
ActivityEngine reads at construction (load_engine_settings). This is the
"promote a backtest-proven value to live" step: a backtest can already
compare candidate values per-run (Strategy.swing_lookback, see
order_backtest.py's engine_config_from_strategy), but live trading runs
exactly ONE shared ActivityEngine (app.py, no override) that only ever
reads the value stored here — there was previously no way to set it except
hand-written SQL.

Only KNOWN keys (activity_engine.ENGINE_SETTING_DEFAULTS) are listed or
accepted — never an arbitrary string nothing reads, which would just be a
silently-dead row. GET always returns one row per known key, merging any
stored override onto the module default, so the page can show "default,
never calibrated" vs "overridden" even though EngineSetting itself starts
empty by design (see its own docstring)."""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from activity_engine import ENGINE_SETTING_DEFAULTS, ENGINE_SETTING_DESCRIPTIONS
from db.ops import LibSettings as ops_settings

engine_settings_bp = Blueprint("engine_settings", __name__)


def _serialize_all(session) -> list:
    overrides = ops_settings.load_all(session)
    return [
        {
            "key": key,
            "default_value": default,
            "value": overrides.get(key, default),
            "is_override": key in overrides,
            "description": ENGINE_SETTING_DESCRIPTIONS.get(key, ""),
        }
        for key, default in sorted(ENGINE_SETTING_DEFAULTS.items())
    ]


@engine_settings_bp.route("/api/engine-settings", methods=["GET"])
def list_settings():
    return jsonify(_serialize_all(g.db_session))


@engine_settings_bp.route("/api/engine-settings/<key>", methods=["PUT"])
def put_setting(key):
    if key not in ENGINE_SETTING_DEFAULTS:
        return jsonify({"error": f"unknown engine setting key: {key!r}"}), 400
    body = request.get_json(silent=True) or {}
    if "value" not in body:
        return jsonify({"error": "value is required"}), 400
    try:
        value = float(body["value"])
    except (TypeError, ValueError):
        return jsonify({"error": "value must be numeric"}), 400
    ops_settings.set_value(g.db_session, key, value, description=ENGINE_SETTING_DESCRIPTIONS.get(key))
    g.db_session.flush()
    return jsonify(_serialize_all(g.db_session))


@engine_settings_bp.route("/api/engine-settings/<key>", methods=["DELETE"])
def delete_setting(key):
    if key not in ENGINE_SETTING_DEFAULTS:
        return jsonify({"error": f"unknown engine setting key: {key!r}"}), 400
    ops_settings.delete(g.db_session, key)
    g.db_session.flush()
    return jsonify(_serialize_all(g.db_session))
