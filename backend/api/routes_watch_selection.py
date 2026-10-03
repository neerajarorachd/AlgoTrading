"""Per-instrument "what to watch" on Market Watch -- GET/PUT the
exclusion set (see InstrumentWatchExclusion's own docstring in
db/models.py: only exclusions are stored, so a fresh instrument watches
everything by default with no seeding needed)."""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from db.ops import LibSymbols as ops_symbols
from db.ops import LibWatchSelection as ops_watch

watch_selection_bp = Blueprint("watch_selection", __name__)

_VALID_ITEM_TYPES = {"pattern", "strategy"}


def _validate_exclusions(exclusions) -> str | None:
    if not isinstance(exclusions, list):
        return "excluded must be a list"
    for item in exclusions:
        if not isinstance(item, dict) or item.get("item_type") not in _VALID_ITEM_TYPES or not item.get("item_code"):
            return f"each excluded entry needs item_type in {sorted(_VALID_ITEM_TYPES)} and a non-empty item_code"
    return None


@watch_selection_bp.get("/api/instruments/<int:instrument_id>/watch-selection")
def get_watch_selection(instrument_id):
    if ops_symbols.get_by_id(g.db_session, instrument_id) is None:
        return jsonify({"error": "instrument not found"}), 404
    return jsonify({"excluded": ops_watch.get_exclusions(g.db_session, instrument_id)})


@watch_selection_bp.put("/api/instruments/<int:instrument_id>/watch-selection")
def put_watch_selection(instrument_id):
    if ops_symbols.get_by_id(g.db_session, instrument_id) is None:
        return jsonify({"error": "instrument not found"}), 404
    body = request.get_json(silent=True) or {}
    exclusions = body.get("excluded", [])
    error = _validate_exclusions(exclusions)
    if error:
        return jsonify({"error": error}), 400
    ops_watch.set_exclusions(g.db_session, instrument_id, exclusions)
    g.db_session.flush()
    return jsonify({"excluded": ops_watch.get_exclusions(g.db_session, instrument_id)})
