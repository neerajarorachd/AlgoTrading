"""Signal-strength configuration (ALGO-6): everything is editable so it can
be calibrated -- per-signal minimums (1-min base), timeframe multipliers, the
target share, and recalibration from history (preview, then apply).

Single user until login exists: owner_id 1 is "my overrides"; system rows
are edited through `scope=system`."""
from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from db.ops import LibSignalStrength as ops
from db.ops import LibSystemSettings
from signal_strength import PRICE_SCALED_MEASURES, STRENGTH_MEASURES, scale_minimums

signal_strength_bp = Blueprint("signal_strength", __name__)

USER_ID = 1
SETTING_KEYS = ("strength_multiplier_3min", "strength_multiplier_5min", "strength_target_share", "strength_calibration_sessions")


def _factory():
    return current_app.extensions["db_session_factory"]


@signal_strength_bp.get("/api/signal-strength")
def get_signal_strength():
    session = g.db_session
    system = ops.load_rules(session)
    mine = ops.load_rules(session, owner_id=USER_ID)
    multipliers = ops.load_multipliers(session)
    by_tf = {tf: scale_minimums(mine, tf, multipliers) for tf in ("1min", "3min", "5min")}
    signals = []
    for signal, measures in STRENGTH_MEASURES.items():
        signals.append({
            "signal": signal,
            "measures": [{
                "measure": m, "formula": f, "scales_with_timeframe": m in PRICE_SCALED_MEASURES,
                "system_min": system.get(signal, {}).get(m),
                "min": mine.get(signal, {}).get(m),  # what applies to me (my override or the system value)
                "overridden": mine.get(signal, {}).get(m) != system.get(signal, {}).get(m),
                "by_timeframe": {tf: by_tf[tf].get(signal, {}).get(m) for tf in by_tf},
            } for m, f in measures.items()],
        })
    settings = LibSystemSettings.load_by_keys(session, SETTING_KEYS)
    return jsonify({"signals": signals, "settings": settings})


@signal_strength_bp.put("/api/signal-strength/rules")
def set_rule():
    """{signal, measure, min, scope?: 'mine'|'system'} -- set a 1-min minimum."""
    body = request.get_json(force=True) or {}
    try:
        value = float(body["min"])
        owner = None if body.get("scope") == "system" else USER_ID
        ops.set_rule(_factory(), body.get("signal", ""), body.get("measure", ""), value, owner_id=owner)
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc) if isinstance(exc, ops.StrengthRuleError) else "signal, measure and a numeric min are required"}), 400
    return jsonify({"ok": True})


@signal_strength_bp.put("/api/signal-strength/settings")
def set_settings():
    """Any of the multipliers / target share / calibration sessions."""
    body = request.get_json(force=True) or {}
    unknown = set(body) - set(SETTING_KEYS)
    if unknown:
        return jsonify({"error": f"unknown setting(s): {', '.join(sorted(unknown))}"}), 400
    for key, value in body.items():
        try:
            value = float(value)
        except (TypeError, ValueError):
            return jsonify({"error": f"{key} must be a number"}), 400
        if value <= 0 or (key == "strength_target_share" and value >= 1):
            return jsonify({"error": f"{key} is out of range"}), 400
        LibSystemSettings.set_value(_factory(), key, value)
    return jsonify({"ok": True})


@signal_strength_bp.post("/api/signal-strength/calibrate")
def calibrate():
    """Recalibrate from history. {target?, sessions?, apply?: false}. Without
    apply it only previews (read-only); with apply it writes the system
    minimums and multipliers. Takes ~1 min over the DB tunnel."""
    from signal_strength_calibration import apply as apply_proposal
    from signal_strength_calibration import compute
    body = request.get_json(silent=True) or {}
    settings = LibSystemSettings.load_by_keys(g.db_session, SETTING_KEYS)
    target = float(body.get("target", settings.get("strength_target_share", 0.4)))
    sessions = int(body.get("sessions", settings.get("strength_calibration_sessions", 20)))
    if not 0 < target < 1 or not 1 <= sessions <= 120:
        return jsonify({"error": "target must be between 0 and 1, sessions between 1 and 120"}), 400
    proposal = compute(_factory(), target=target, sessions=sessions)
    if body.get("apply"):
        apply_proposal(_factory(), proposal)
    return jsonify({**proposal, "applied": bool(body.get("apply"))})
