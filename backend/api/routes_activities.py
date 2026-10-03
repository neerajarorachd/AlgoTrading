from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from flask import Blueprint, g, jsonify, request

from db.ops import LibActivities as ops_activities
from db.ops import LibPatternOutcomes as ops_pattern_outcomes
from db.ops import LibSymbols as ops_symbols
from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

activities_bp = Blueprint("activities", __name__)


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _direction_for(pattern: str) -> str:
    if pattern in BULLISH_PATTERNS:
        return "bullish"
    if pattern in BEARISH_PATTERNS:
        return "bearish"
    return "neutral"


def _median(values: List[float]) -> Optional[float]:
    return statistics.median(values) if values else None


# Short, human-readable labels for the Market Watch event list -- most
# pattern codes read fine just title-cased (the fallback below), these few
# are the ones that don't (explicit examples named, 2026-10-01: "doji
# formed, macd crossover, stocastic crossover").
_ACTIVITY_LABEL_OVERRIDES = {
    "doji": "Doji formed", "hammer": "Hammer formed", "shooting_star": "Shooting star formed",
    "macd_bullish_cross": "MACD bullish crossover", "macd_bearish_cross": "MACD bearish crossover",
    "stoch_bullish_cross": "Stochastic bullish crossover", "stoch_bearish_cross": "Stochastic bearish crossover",
    "ma_golden_cross": "Golden cross (MA21/MA50)", "ma_death_cross": "Death cross (MA21/MA50)",
    "swing_high": "Swing high confirmed", "swing_low": "Swing low confirmed",
    "bb_squeeze": "Bollinger Band squeeze", "bb_widening": "Bollinger Band widening",
}


def _activity_label(activity: str) -> str:
    return _ACTIVITY_LABEL_OVERRIDES.get(activity, activity.replace("_", " ").capitalize())


def _iso_z(dt) -> str:
    """Matches routes_recommendations.py's own _iso convention: these
    DATETIME columns are naive-but-UTC, so a bare isoformat() would let the
    frontend's `new Date(iso)` silently parse it as browser-local time."""
    dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
    return dt.isoformat() + "Z"


_IST_OFFSET = timedelta(hours=5, minutes=30)


def _today_ist_date():
    return (datetime.now(timezone.utc) + _IST_OFFSET).date()


@activities_bp.get("/api/activities/counts")
def get_activity_counts():
    """Today's (IST trading day) event counts by category for one
    instrument -- the Market Watch row's "5 candle formations, 3
    indicator crossovers" badges, a quick-glance summary before opening
    the full /recent list. Reads the STORED rollup (InstrumentActivityDailyCount,
    kept current at write time) rather than scanning instrument_activity live
    on every request — see db/ops/LibActivities.py's own docstring on why."""
    instrument_id = request.args.get("instrument_id", type=int)
    if not instrument_id:
        return jsonify({"error": "instrument_id is required"}), 400
    return jsonify(ops_activities.get_daily_counts(g.db_session, instrument_id, _today_ist_date()))


@activities_bp.get("/api/activities/recent")
def get_recent_activities():
    """Per-instrument event feed for the Market Watch page's events list —
    newest first, human-readable label + timestamp. Not the same shape as
    /occurrences (that one's a historical backtesting/analysis query over a
    date range; this is "what just happened on this stock")."""
    instrument_id = request.args.get("instrument_id", type=int)
    if not instrument_id:
        return jsonify({"error": "instrument_id is required"}), 400
    timeframe = request.args.get("timeframe")
    limit = request.args.get("limit", default=20, type=int)
    rows = ops_activities.get_recent(g.db_session, instrument_id, timeframe, limit)
    return jsonify([
        {
            "activity": r.activity, "label": _activity_label(r.activity), "activity_type": r.activity_type,
            "timeframe": r.timeframe, "ts": _iso_z(r.ts),
        }
        for r in rows
    ])


@activities_bp.get("/api/activities/occurrences")
def get_occurrences():
    """Backtesting type 1: "just count the occurrences in a certain
    period" — no trade simulation, a pure aggregation over already-
    detected activities. `patterns` (comma-separated activity codes) lets
    the caller restrict to specific formations rather than always
    returning every pattern — pick from GET /api/strategy-elements'
    "event" elements.

    Each pattern row also carries the analysis columns built on top of
    the existing, untouched raw data (explicit instruction, 2026-09-15):
    `intensity_median` (from InstrumentActivity.intensity, already
    computed per-occurrence by activity_engine.py's detectors),
    `expected_result` (this pattern's traditionally assumed direction —
    same source as `direction`, just the name the UI uses for this
    column), `actual_result_matched`/`actual_result_total`/
    `actual_result_pct` (how often price actually closed on the expected
    side of entry 20 candles later — PatternOutcome.pct_change_20), and
    `up_median_pct`/`down_median_pct`/`range_median_pct` (medians of
    PatternOutcome's own max_favorable_pct/max_adverse_pct — the best/
    worst price seen in the next 20 candles, i.e. literally "up X% / down
    Y%" from entry). All of these come back `None`/0 for a pattern that
    hasn't had `pattern_outcome_analysis.py` run for this instrument/
    timeframe yet — this endpoint never computes them itself, only reads
    what's already there.

    Raw per-occurrence lists (`intensity_values`, `up_values`,
    `down_values`, `range_values`) ride along too — NOT for direct
    display, but so a caller aggregating across MULTIPLE instruments (a
    watchlist, in the frontend) can correctly recombine medians itself
    instead of incorrectly averaging several already-computed medians
    together."""
    instrument_id = request.args.get("instrument_id", type=int)
    timeframes_s = request.args.get("timeframes")
    from_s = request.args.get("from")
    to_s = request.args.get("to")
    patterns_s = request.args.get("patterns")
    if not instrument_id or not timeframes_s or not from_s or not to_s:
        return jsonify({"error": "instrument_id, timeframes, from, to are required"}), 400

    symbol_row = ops_symbols.get_by_id(g.db_session, instrument_id)
    if symbol_row is None:
        return jsonify({"error": f"Unknown instrument_id: {instrument_id}"}), 404

    timeframes = timeframes_s.split(",")
    patterns = patterns_s.split(",") if patterns_s else None
    ts_from, ts_to = _parse_date(from_s), _parse_date(to_s)

    rows = ops_activities.count_by_pattern(g.db_session, instrument_id, timeframes, ts_from, ts_to, patterns)
    intensity_by_pattern = ops_activities.intensity_values_by_pattern(
        g.db_session, instrument_id, timeframes, ts_from, ts_to, patterns,
    )
    outcome_by_pattern = ops_pattern_outcomes.raw_values_by_pattern(
        g.db_session, instrument_id, timeframes, ts_from, ts_to, patterns,
    )

    total = 0
    bullish = bearish = neutral = 0
    by_kind: dict = {}
    pattern_rows = []
    for activity_type, activity, count in rows:
        total += count
        direction = _direction_for(activity)
        if direction == "bullish":
            bullish += count
        elif direction == "bearish":
            bearish += count
        else:
            neutral += count
        by_kind[activity_type] = by_kind.get(activity_type, 0) + count

        intensity_values = intensity_by_pattern.get(activity, [])
        outcome = outcome_by_pattern.get(activity, {})
        pct20_values = outcome.get("pct_values", {}).get(20, [])
        up_values = outcome.get("up_values", [])
        down_values = outcome.get("down_values", [])
        range_values = outcome.get("range_values", [])

        # "Behaved as expected" only means something for a pattern with a
        # predefined direction — a neutral pattern (e.g. rectangle) has no
        # expectation to check pct_change_20 against, so its actual-result
        # is left at 0/0 (None%) rather than silently picking a direction.
        if direction == "bullish":
            actual_matched = sum(1 for p in pct20_values if p > 0)
        elif direction == "bearish":
            actual_matched = sum(1 for p in pct20_values if p < 0)
        else:
            actual_matched = 0
        actual_total = len(pct20_values) if direction != "neutral" else 0

        pattern_rows.append({
            "activity_type": activity_type, "pattern": activity, "count": count, "direction": direction,
            "expected_result": direction,
            "intensity_median": _median(intensity_values), "intensity_count": len(intensity_values),
            "up_median_pct": _median(up_values), "down_median_pct": _median(down_values),
            "range_median_pct": _median(range_values),
            "actual_result_matched": actual_matched, "actual_result_total": actual_total,
            "actual_result_pct": (actual_matched / actual_total) if actual_total else None,
            "intensity_values": intensity_values, "up_values": up_values,
            "down_values": down_values, "range_values": range_values,
        })
    pattern_rows.sort(key=lambda r: -r["count"])

    return jsonify({
        "symbol": symbol_row.symbol, "instrument_id": instrument_id,
        "timeframes": timeframes, "from": from_s, "to": to_s,
        "total": total, "bullish": bullish, "bearish": bearish, "neutral": neutral,
        "by_kind": by_kind, "patterns": pattern_rows,
    })


@activities_bp.get("/api/activities/intensity-analysis")
def get_intensity_analysis():
    """"analyze 5, 10, 15, 20, 30 candles and share analysis of all of
    these candles linked with intensity" (explicit instruction,
    2026-09-15) — one pattern at a time (unlike /occurrences, which
    covers every pattern in scope at once): splits this pattern's
    occurrences into intensity bands (lowest to highest) and reports the
    actual-result match rate at every checkpoint, plus overall price
    range, per band.

    Also carries `indicator_states` (explicit follow-up instruction, same
    day: "Now implement indicator states") — the same per-checkpoint
    breakdown, but banded by RSI/MACD/Stochastic state at the moment the
    pattern fired instead of by intensity (see
    LibPatternOutcomes.indicator_state_analysis's own docstring for why
    this needs a LEFT OUTER join and can legitimately come back with an
    empty list for an indicator that has no CandleIndicators coverage in
    scope). Kept on this one endpoint rather than a separate route since
    the frontend always wants both together when a pattern row is
    expanded — one fetch, not two."""
    instrument_id = request.args.get("instrument_id", type=int)
    timeframes_s = request.args.get("timeframes")
    from_s = request.args.get("from")
    to_s = request.args.get("to")
    pattern = request.args.get("pattern")
    bands = request.args.get("bands", default=3, type=int)
    if not instrument_id or not timeframes_s or not from_s or not to_s or not pattern:
        return jsonify({"error": "instrument_id, timeframes, from, to, pattern are required"}), 400

    symbol_row = ops_symbols.get_by_id(g.db_session, instrument_id)
    if symbol_row is None:
        return jsonify({"error": f"Unknown instrument_id: {instrument_id}"}), 404

    timeframes = timeframes_s.split(",")
    ts_from, ts_to = _parse_date(from_s), _parse_date(to_s)

    bands_result = ops_pattern_outcomes.intensity_banded_analysis(
        g.db_session, instrument_id, timeframes, ts_from, ts_to, pattern, bands=bands,
    )
    indicator_states = ops_pattern_outcomes.indicator_state_analysis(
        g.db_session, instrument_id, timeframes, ts_from, ts_to, pattern,
    )
    return jsonify({
        "symbol": symbol_row.symbol, "instrument_id": instrument_id, "pattern": pattern,
        "checkpoints": list(ops_pattern_outcomes.CHECKPOINTS), "bands": bands_result,
        "indicator_states": indicator_states,
    })
