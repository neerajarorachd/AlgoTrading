"""User preferences API (2026-10-15): the UI's last-used settings, per user.

GET  /api/preferences          -> {key: value, ...}
PUT  /api/preferences          body {key: value, ...} -- merged in; null removes a key

Single user until login exists (owner_id 1), same as the patterns API."""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from db.ops import LibUserPreferences as ops

preferences_bp = Blueprint("preferences", __name__)

USER_ID = 1


@preferences_bp.get("/api/preferences")
def get_preferences():
    return jsonify(ops.get_all(g.db_session, USER_ID))


@preferences_bp.put("/api/preferences")
def put_preferences():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "body must be a JSON object of key: value"}), 400
    try:
        ops.set_many(g.db_session, USER_ID, body)
    except ops.PreferenceError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(ops.get_all(g.db_session, USER_ID))
