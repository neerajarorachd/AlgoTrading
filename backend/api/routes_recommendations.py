"""Read-only views over the recommendation queue (db/ops/LibRecommendations.py)
— no POST endpoint in this round, since nothing legitimate would call
mark_selected yet (no order-placement consumer exists). See
recommendation_engine.py's own module docstring for the full picture."""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, g, jsonify, request

from db.models import Recommendation, RecommendationOutcome
from db.ops import LibRecommendations as ops_recommendations
from recommendation_outcomes import pattern_summary

recommendations_bp = Blueprint("recommendations", __name__)


def _num(value):
    """SQL Numeric columns come back as Decimal (real SQL Server/pyodbc) or
    float (SQLite in tests) — normalize either to a plain JSON-safe float."""
    return None if value is None else float(value)


def _iso(dt):
    """These columns are always naive-but-UTC (SQL Server's DATETIME has no
    tzinfo concept — see db/ops/LibCandles.py's own persist path), but a
    bare .isoformat() drops that fact silently. Without the trailing "Z"
    here, the frontend's `new Date(iso)` parses the string as browser-LOCAL
    time instead of UTC — the same class of bug just fixed for candle
    timestamps (see dhan_feed.py's _base_tick), just one layer up. Matches
    routes_candles.py's own _serialize convention."""
    if dt is None:
        return None
    dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
    return dt.isoformat() + "Z"


def _serialize(row: Recommendation) -> dict:
    return {
        "id": row.id, "source": row.source, "recommendation_system_id": row.recommendation_system_id,
        "instrument_id": row.instrument_id, "timeframe": row.timeframe,
        "pattern": row.pattern, "direction": row.direction,
        "entry_price": _num(row.entry_price), "detected_ts": _iso(row.detected_ts),
        "signal_intensity": _num(row.signal_intensity),
        "signal_rsi_state": row.signal_rsi_state, "signal_rsi_trend": row.signal_rsi_trend,
        "signal_macd_state": row.signal_macd_state, "signal_macd_trend": row.signal_macd_trend,
        "signal_stoch_state": row.signal_stoch_state, "signal_stoch_trend": row.signal_stoch_trend,
        "signal_rsi": _num(row.signal_rsi), "signal_macd_line": _num(row.signal_macd_line),
        "signal_macd_signal": _num(row.signal_macd_signal), "signal_stoch_k": _num(row.signal_stoch_k),
        "band_kind": row.band_kind, "band_label": row.band_label,
        "band_min": _num(row.band_min), "band_max": _num(row.band_max),
        "qualifying_checkpoint": row.qualifying_checkpoint, "sample_count": row.sample_count,
        "win_count": row.win_count, "win_pct": _num(row.win_pct), "wilson_score": _num(row.wilson_score),
        "veto_reason": row.veto_reason, "rule_note": row.rule_note,
        "suggested_quantity": row.suggested_quantity,
        "suggested_sl_price": _num(row.suggested_sl_price),
        "suggested_target_price": _num(row.suggested_target_price),
        "confirmations_checked": row.confirmations_checked, "confirmations_passed": row.confirmations_passed,
        "status": row.status,
        "generated_at": _iso(row.generated_at), "expires_at": _iso(row.expires_at),
        "selected_at": _iso(row.selected_at),
        "regeneration_count": row.regeneration_count,
        # Added 2026-09-16 alongside the guiding-scenarios redesign — which
        # precomputed GuidingScenario row and lookback window justified this
        # recommendation (see recommendation_engine.generate_recommendations).
        "guiding_scenario_id": row.guiding_scenario_id, "window_kind": row.window_kind,
    }


@recommendations_bp.get("/api/recommendations/pending")
def get_pending():
    instrument_id = request.args.get("instrument_id", type=int)
    timeframe = request.args.get("timeframe")
    rows = ops_recommendations.get_pending(g.db_session, instrument_id, timeframe)
    return jsonify([_serialize(r) for r in rows])


@recommendations_bp.get("/api/recommendations/history")
def get_history():
    """Every recommendation regardless of status/expiry — see
    LibRecommendations.list_recent's own docstring for why this differs
    from /pending (most of a day's signals are, correctly, already
    expired by the time anyone looks at them)."""
    instrument_id = request.args.get("instrument_id", type=int)
    timeframe = request.args.get("timeframe")
    since_param = request.args.get("since")
    since = datetime.fromisoformat(since_param.replace("Z", "+00:00")) if since_param else None
    limit = request.args.get("limit", default=200, type=int)
    pattern = request.args.get("pattern")
    rows = ops_recommendations.list_recent(g.db_session, instrument_id, timeframe, since, limit, pattern)
    outcomes = {
        o.recommendation_id: o for o in g.db_session.query(RecommendationOutcome).filter(
            RecommendationOutcome.recommendation_id.in_([r.id for r in rows]))
    } if rows else {}
    return jsonify([{**_serialize(r), "outcome": _serialize_outcome(outcomes.get(r.id))} for r in rows])


def _serialize_outcome(o):
    if o is None:
        return None
    return {
        "status": o.status, "candles_observed": o.candles_observed, "checkpoint": o.checkpoint,
        "checkpoint_pct": _num(o.checkpoint_pct), "win": o.win,
        "first_hit": o.first_hit, "candles_to_hit": o.candles_to_hit,
        "pct_change": {n: _num(getattr(o, f"pct_change_{n}")) for n in (5, 10, 15, 20, 30)},
        "max_favorable_pct": _num(o.max_favorable_pct), "max_adverse_pct": _num(o.max_adverse_pct),
    }


@recommendations_bp.get("/api/recommendations/pattern-summary")
def get_pattern_summary():
    """Per-pattern history roll-up (counts by status, rule rejections, and
    win% of recommended vs rejected rows) -- see
    recommendation_outcomes.pattern_summary."""
    instrument_id = request.args.get("instrument_id", type=int)
    since_param = request.args.get("since")
    since = datetime.fromisoformat(since_param.replace("Z", "+00:00")).replace(tzinfo=None) if since_param else None
    return jsonify(pattern_summary(g.db_session, since, instrument_id))


@recommendations_bp.get("/api/recommendations/best-preview")
def get_best_preview():
    """Dry-run only — ranks the current queue by Wilson score ("best
    option wins") without calling mark_selected, so this is safe to call
    repeatedly (e.g. to sanity-check the ranking against real data)."""
    slots = request.args.get("slots", default=1, type=int)
    instrument_id = request.args.get("instrument_id", type=int)
    timeframe = request.args.get("timeframe")
    rows = ops_recommendations.pick_best(g.db_session, slots, instrument_id, timeframe)
    return jsonify([_serialize(r) for r in rows])
