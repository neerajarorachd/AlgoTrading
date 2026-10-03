"""Bridges a live pattern detection to the precomputed guiding-scenario
lookup (backend/guiding_scenarios.py), gates the result against SystemSetting
(see db/models.SystemSetting), and persists ONE db/ops/LibRecommendations.
Recommendation row per pattern occurrence.

**Redesigned 2026-09-16 — guiding-scenario lookup, not live analysis.**
Before this date, RS1 evaluated every live signal by querying raw
PatternOutcome directly (intensity_banded_analysis/pattern_level_analysis
for a PRIMARY gate, indicator_state_analysis as a marginal CONFIRMATION/
veto step), and a second system RS2 ran a genuinely different engine (an
exact joint combination match via LibPatternOutcomes.combination_analysis).
Both were real, built and tested. The redesign removes RS2 and RS3 entirely
and changes RS1's own mechanism:
  - guiding_scenarios.generate_guiding_scenarios() mines PatternOutcome
    WEEKLY (not live) for (pattern, intensity band) combinations whose win
    rate clears win_pct_threshold with a real sample size, and stores ONLY
    those winners in a small GuidingScenario table — the same statistical
    gate RS1 always used, just computed in advance instead of per signal.
  - A live pattern firing does ONE cheap indexed lookup
    (guiding_scenarios.match_guiding_scenario) instead of a live DB
    analysis — no more per-signal intensity_banded_analysis/
    pattern_level_analysis queries.
  - Indicator state/trend (RSI/MACD/Stochastic) is EXPLICITLY OUT of the
    decision gate now — "should not interfere in the decision." It's still
    computed and stored (GuidingScenarioIndicatorStat), scoped to each
    qualifying scenario's own occurrence subset, purely for education and
    future volume/SL-target sizing. There is no live confirmation/veto step
    anymore — a matched guiding scenario is queued outright.
  - The old "no intensity -> whole-pattern fallback" is also gone: pattern
    + intensity band are BOTH required by the guiding-scenario decision
    gate, so a live signal with no intensity value simply can't be
    evaluated by this mechanism (generate_recommendations returns None).
  - Two independent lookback windows exist (window_kind "2y"/"3m", see
    guiding_scenarios.WINDOW_LOOKBACK_DAYS) — no combination rule decided
    yet; generate_recommendations takes window_kind explicitly rather than
    guessing, defaulting to "2y" as the more statistically robust option
    until a real decision is made (see its own docstring).

This is explicitly NOT part of backtesting (db/ops/LibBacktestRuns.py,
scripts/run_backtest.py, which simulate actual position-sized trades over
history) — it consumes ANALYSIS output (PatternOutcome/GuidingScenario, a
neutral "what happened after this pattern fired" statistic, no capital or
position sizing involved) as its one existing data source. `source` on
every Recommendation row names which analysis system produced it — today
always SOURCE = "pattern_outcome_bands" below, the only one that exists;
a future second analysis system (a different model entirely, not
necessarily anything to do with PatternOutcome) can populate the same
Recommendation table with its own source tag, no schema change needed.

The user's own stated direction: a "system command" running on the
(not-yet-deployed) live server will start each analysis system running
each trading day at a SystemSetting-driven time
(recommendation_analysis_start_time_minutes, default 555 = 09:15 IST, not
hardcoded — "9:15 or may be 10:15, all defined in settings"). That server
command doesn't exist yet (no live deployment exists to run it on — see
CLAUDE.md) and isn't built in this round; the setting is seeded now so the
value is already in place once it is. Weekly regeneration of guiding
scenarios is the same story — see guiding_scenarios.py's own docstring for
why no scheduler is wired up here either.

RS1 (db/models.RecommendationSystem, db/ops/LibRecommendationSystems.py) is
the only standing "recommendation system" row — it owns WHERE/WHAT to scan
(watchlist_id, timeframe, top_n_per_run) and may override a subset of the
SystemSetting keys below for itself only (LibRecommendationSystems.
resolve_settings). "AM1", per the user's own clarification ("AM1 is an
internal engine of RS1, basically analysis logic"), is NOT a separate
module — it IS generate_recommendations() below, called once per candidate
RS1's (not-yet-built) polling loop finds. A caller identifies which RS is
running by passing recommendation_system_id; it is stamped on every
resulting Recommendation row (separately from `source`, which names the
analysis algorithm itself, not the orchestrating RS) and threaded through
regeneration. Passing no recommendation_system_id (as every existing
caller/test still does) falls back to pure global SystemSetting behavior.

Entry points, all plain callable functions — NOT a running background
scheduler/poller, matching this project's own established preference for
lightweight polling-on-demand over standing infrastructure (see
feed/gap_fill.py, which replaced the originally-planned candle_gap_queue):
  - generate_recommendations(): call once per live pattern firing. As of
    2026-09-16 this IS wired into the live app — see on_activities() below
    and app.py's _make_on_candle_closed, the first real live call site
    (mirrors prediction_tracker.PredictionTracker.on_activities's existing
    shape).
  - on_activities(symbol, exchange_segment, activities): the live call-site
    adapter — takes exactly the list ActivityEngine.on_candle_closed just
    returned, calls generate_recommendations() for each pattern+intensity
    activity found. Never raises — a lookup failure must not break the live
    candle-close path (mirrors activity_engine.py's own swing-detection
    resilience convention).
  - sweep_and_regenerate(): call on whatever polling cadence a caller
    chooses (a manual script, a future periodic task) to expire stale
    queued rows and "go for analysis again" for eligible ones.

Explicitly OUT OF SCOPE this round: placing or simulating a real broker
order, any volume/SL-target sizing formula (GuidingScenarioIndicatorStat
data is laid down for this but not consumed yet), and a rule for combining
the 2y/3m windows. db/ops/LibRecommendations.mark_selected is a separate,
deliberate, caller-invoked step for exactly this reason (see its own
docstring) — nothing in this module ever calls it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import guiding_scenarios
import pattern_defaults
import rule_gate as rule_gate_mod
from db.ops import LibRecommendations, LibRecommendationSystems, LibSystemSettings
from db.session import session_scope
from timeframes import candle_duration

logger = logging.getLogger(__name__)

SOURCE = "pattern_outcome_bands"

# The window a live signal is checked against when a caller doesn't specify
# one explicitly — "2y" chosen as the more statistically robust default
# (larger sample) until a real combination-rule decision is made (see this
# module's own docstring); NOT a permanent design commitment.
DEFAULT_WINDOW_KIND = "2y"

# Seeded once at app startup (app.py), same spot SYSTEM_SETTING_SEED is
# seeded from. RS1's own code is the user's own literal term throughout
# design discussion ("at present RS1"), kept verbatim rather than invented.
# watchlist_id is left None — an operator must assign one via a future
# admin action before RS1's (not-yet-built) polling loop can actually run;
# nothing in this round requires it to be set. RS2/RS3 (real, built and
# tested 2026-09-15) were removed 2026-09-16 once RS1's own mechanism was
# redesigned around precomputed guiding scenarios — see this module's own
# docstring and db/models.RecommendationSystem's docstring for why.
RECOMMENDATION_SYSTEM_SEED: Sequence[dict] = (
    {
        "code": "RS1", "name": "System default — guiding-scenario lookup",
        "kind": "system", "watchlist_id": None, "timeframe": "3min", "top_n_per_run": 3,
        "analysis_mode": "primary_confirmation",
    },
)

SYSTEM_SETTING_DEFAULTS: Dict[str, float] = {
    "recommendation_win_pct_threshold": 0.70,
    "recommendation_min_sample_size": 7,
    "recommendation_wilson_confidence": 0.95,
    "recommendation_expiry_candles": 1,
    "recommendation_queue_bandwidth": 5,
    "recommendation_analysis_lookback_days": 90,
    "recommendation_max_regenerations": 3,
    "recommendation_analysis_start_time_minutes": 555,  # 09:15 IST
}

# (key, default, description) — passed to LibSystemSettings.seed_defaults
# once at app startup, same spot seed_pattern_definitions/
# seed_strategy_elements are already called from in app.py.
# recommendation_veto_win_pct_max/recommendation_veto_min_sample_size
# (real settings, 2026-09-15) and recommendation_persist_rejected were
# removed 2026-09-16 alongside the live confirmation/veto step and the
# "rejected, persisted" status they existed for — indicator state no
# longer gates anything live, and a non-qualifying candidate is never
# written to GuidingScenario in the first place, so there's nothing left
# to reject-and-persist at the live stage — see this module's own
# docstring. recommendation_qualifying_checkpoint (a single fixed
# checkpoint) was ALSO removed 2026-09-16: guiding_scenarios.py now checks
# a band against all 5 PatternOutcome checkpoints instead of locking onto
# one ("review internal threshold data... don't discard it" — real user
# feedback after the first run only found 8 qualifying scenarios for
# HINDCOPPER). recommendation_min_sample_size lowered 10 -> 7 the same
# round, for the same reason.
SYSTEM_SETTING_SEED: Sequence[Tuple[str, float, str]] = (
    ("recommendation_win_pct_threshold", 0.70, "Minimum historical win rate (at whichever checkpoint qualifies) for a scenario to qualify"),
    ("recommendation_min_sample_size", 7, "Minimum occurrence count in a band before it's considered at all"),
    ("recommendation_wilson_confidence", 0.95, "Confidence level used for the Wilson score lower-bound ranking"),
    ("recommendation_expiry_candles", 1, "How many of the recommendation's own timeframe candles before it expires unfilled"),
    ("recommendation_queue_bandwidth", 5, "Max concurrent recommendations pick_best will select at once"),
    ("recommendation_analysis_lookback_days", 90, "Legacy live-analysis lookback — superseded by guiding_scenarios.WINDOW_LOOKBACK_DAYS, kept for any caller still reading it"),
    ("recommendation_max_regenerations", 3, "Cap on how many times an expired recommendation can trigger a fresh re-analysis"),
    (
        "recommendation_analysis_start_time_minutes", 555,
        "IST minutes-since-midnight the server-side analysis command should "
        "start each trading day — 555 = 09:15. Convert back via divmod(int(value), 60).",
    ),
)


def load_system_settings(session_factory, recommendation_system_id: Optional[int] = None) -> Dict[str, float]:
    """Global SystemSetting values, merged with the given RS's own
    *_override columns if it has any (None = pure global, unchanged
    behavior for every caller that predates RS1)."""
    with session_scope(session_factory) as session:
        settings = LibSystemSettings.load_by_keys(session, SYSTEM_SETTING_DEFAULTS.keys())
        if recommendation_system_id is None:
            return settings
        rs = LibRecommendationSystems.get_by_id(session, recommendation_system_id)
        return LibRecommendationSystems.resolve_settings(rs, settings)


def _setting(settings: Dict[str, float], key: str):
    return settings.get(key, SYSTEM_SETTING_DEFAULTS[key])


def generate_recommendations(
    session_factory,
    instrument_id: int, timeframe: str, pattern: str, direction: str,
    entry_price: float, detected_ts: datetime,
    intensity: Optional[float] = None,
    window_kind: str = DEFAULT_WINDOW_KIND,
    rsi_state: Optional[str] = None, rsi_trend: Optional[str] = None,
    macd_state: Optional[str] = None, macd_trend: Optional[str] = None,
    stoch_state: Optional[str] = None, stoch_trend: Optional[str] = None,
    rsi_value: Optional[float] = None, macd_line: Optional[float] = None,
    macd_signal: Optional[float] = None, stoch_value: Optional[float] = None,
    parent_recommendation_id: Optional[int] = None, regeneration_count: int = 0,
    recommendation_system_id: Optional[int] = None,
    rule_gate=None,
) -> Optional[dict]:
    """rule_gate: optional zero-arg callable returning (allowed, note) --
    see rule_gate.py. Called ONLY after a guiding scenario matched. A
    disallowed result persists the row as status "rejected" (audit trail for
    false-alarm tuning, never enters the queue) with the note in
    veto_reason/rule_note; an allowed result queues it, note (e.g. a skipped
    rule) kept in rule_note.

    ONE call per live pattern occurrence. Looks up guiding_scenarios.
    match_guiding_scenario for this instrument/timeframe/pattern/window_kind
    at the live signal's own intensity — pattern + intensity band are both
    required (see this module's own docstring); returns None immediately if
    `intensity` is None or nothing was ever precomputed for this pattern
    (never analyzed, or analyzed but never cleared the win% bar — a clean,
    fast "pass," not an error).

    Indicator state/trend (rsi_state etc.) is stored on the resulting row
    purely as signal context/audit trail — it never gates the decision, no
    live confirmation/veto step runs anymore.

    Persists at most ONE Recommendation row, stamped with
    recommendation_system_id, status "queued" — a non-match is simply
    nothing persisted (no "rejected" audit row is possible anymore: a
    candidate that would have failed the win%/sample-size gate was never
    written to GuidingScenario in the first place, so there's nothing left
    to reject at the live stage — recommendation_persist_rejected is
    therefore dead for this call path, removed alongside the old live
    analysis it existed for). Returns None when nothing was persisted;
    otherwise a dict with {"id", "status", "band_kind", "wilson_score",
    "guiding_scenario_id"}."""
    if intensity is None:
        return None

    settings = load_system_settings(session_factory, recommendation_system_id)
    expiry_candles = _setting(settings, "recommendation_expiry_candles")

    with session_scope(session_factory) as session:
        scenario = guiding_scenarios.match_guiding_scenario(
            session, instrument_id, timeframe, pattern, window_kind, intensity,
        )
        if scenario is None:
            return None
        matched = {
            "id": scenario.id, "band_min": float(scenario.band_min), "band_max": float(scenario.band_max),
            "checkpoint": scenario.checkpoint, "sample_count": scenario.sample_count,
            "win_count": scenario.win_count, "win_pct": float(scenario.win_pct),
            "wilson_score": float(scenario.wilson_score),
        }

    status = "queued"
    veto_reason = None
    rule_note = None
    if rule_gate is not None:
        allowed, rule_note = rule_gate()
        if rule_note is not None:
            rule_note = rule_note[:160]
        if not allowed:
            status, veto_reason = "rejected", rule_note
    generated_at = datetime.now(timezone.utc)
    expires_at = generated_at + candle_duration(timeframe) * expiry_candles
    row = {
        "source": SOURCE, "recommendation_system_id": recommendation_system_id,
        "instrument_id": instrument_id, "timeframe": timeframe,
        "pattern": pattern, "direction": direction, "entry_price": entry_price, "detected_ts": detected_ts,
        "signal_intensity": intensity,
        "signal_rsi_state": rsi_state, "signal_rsi_trend": rsi_trend,
        "signal_macd_state": macd_state, "signal_macd_trend": macd_trend,
        "signal_stoch_state": stoch_state, "signal_stoch_trend": stoch_trend,
        "signal_rsi": rsi_value, "signal_macd_line": macd_line,
        "signal_macd_signal": macd_signal, "signal_stoch_k": stoch_value,
        "band_kind": "intensity", "band_label": "guiding_scenario",
        "band_min": matched["band_min"], "band_max": matched["band_max"],
        "qualifying_checkpoint": matched["checkpoint"], "sample_count": matched["sample_count"],
        "win_count": matched["win_count"], "win_pct": matched["win_pct"], "wilson_score": matched["wilson_score"],
        "veto_reason": veto_reason, "rule_note": rule_note, "confirmations_checked": 0, "confirmations_passed": 0,
        "win_pct_threshold_applied": _setting(settings, "recommendation_win_pct_threshold"),
        "min_sample_size_applied": int(_setting(settings, "recommendation_min_sample_size")),
        "wilson_confidence_applied": _setting(settings, "recommendation_wilson_confidence"),
        "guiding_scenario_id": matched["id"], "window_kind": window_kind,
        "status": status, "generated_at": generated_at, "expires_at": expires_at,
        "regeneration_count": regeneration_count, "parent_recommendation_id": parent_recommendation_id,
    }

    try:  # a defaults lookup problem must never cost the recommendation itself
        row.update(pattern_defaults.suggestion_for(
            session_factory, recommendation_system_id, pattern, direction, entry_price))
    except Exception:
        logger.exception("recommendation_engine: suggested order failed for %s", pattern)

    row_id = LibRecommendations.create_one(session_factory, row)
    if row_id is None:
        return None
    return {
        "id": row_id, "status": status, "band_kind": "intensity",
        "wilson_score": matched["wilson_score"], "guiding_scenario_id": matched["id"],
    }


DEFAULT_RS_CODE = "RS1"


def _resolve_rs(session_factory, code: str = DEFAULT_RS_CODE) -> Optional[dict]:
    with session_scope(session_factory) as session:
        rs = LibRecommendationSystems.get_by_code(session, code)
        if rs is None or not rs.is_active:
            return None
        # order_size: the ONLY named constant a rule's formula may reference
        # (condition_evaluator.KNOWN_CONTEXT_NAMES) -- previously unwired
        # (every live rule_gate call passed context=None, so any rule using
        # it always SKIPPED with "unknown field 'order_size'"). Sourced from
        # the RS1 PARENT strategy's own max_vol_per_call ("Max qty per
        # order") rather than a new column -- a rule here is gating whether
        # to recommend a pattern at all, before any real per-trade sizing
        # exists, so there's no live computed order size to read; the
        # strategy's own configured ceiling is the closest real number and
        # is the conservative choice for a liquidity-style check (e.g.
        # "mean(volume,5) > order_size": if average volume clears the
        # LARGEST order this strategy would ever place, it clears any
        # smaller actual size too).
        from db.models import Strategy
        strategy = session.get(Strategy, rs.strategy_id)
        order_size = strategy.max_vol_per_call if strategy else None
        return {
            "id": rs.id, "strategy_id": rs.strategy_id, "combine_mode": rs.rule_combine_mode or "all",
            "order_size": order_size,
        }


def _make_rule_gate(session_factory, rs_info: dict, symbol: str, exchange_segment: str,
                    timeframe: str, pattern: str, rows_provider):
    """Zero-arg callable for generate_recommendations(rule_gate=...). Any
    failure degrades to "rule skipped: error" (neutral) -- a bug in a rule
    must neither silently pass-through-and-hide nor take down the live path."""
    def gate():
        try:
            with session_scope(session_factory) as session:
                rules = rule_gate_mod.rules_for_pattern(session, rs_info["strategy_id"], pattern)
            if not rules:
                return True, None
            needed = max(rule_gate_mod.rule_lookback(tree) for _, tree in rules)
            rows = rows_provider(symbol, exchange_segment, timeframe, needed)
            context = {"order_size": rs_info["order_size"]} if rs_info.get("order_size") is not None else None
            return rule_gate_mod.evaluate_rules(rules, rows, rs_info["combine_mode"], context=context)
        except Exception:
            logger.exception("recommendation_engine: rule gate failed for %s %s %s", symbol, exchange_segment, pattern)
            return True, "rule skipped: error"
    return gate


def on_activities(session_factory, symbol: str, exchange_segment: str, activities: List[dict],
                  rule_rows_provider=None) -> List[dict]:
    """rule_rows_provider: optional callable (symbol, exchange_segment,
    timeframe, n) -> the last n in-memory candle rows (ActivityEngine.
    recent_rows) -- when given, RS1's rules (rule_gate.py) gate each
    guiding-scenario match and rows are stamped with RS1's id. Omitted (every
    existing caller/test) = unchanged behavior.

    The live call-site adapter — takes exactly the list ActivityEngine.
    on_candle_closed just returned (see its own docstring for the dict
    shape: instrument_id/timeframe/ts/activity_type/activity/intensity/
    open_price/high_price/low_price/close_price — instrument_id/timeframe
    are read straight off each activity dict, same as PredictionTracker.
    on_activities, which this mirrors exactly, including its own signature).

    Skips any activity with no intensity value (nothing to look up — see
    generate_recommendations's own "pattern + intensity band both
    required"), and any activity whose pattern has no known direction —
    there is no `direction` field on the activity dict itself; direction is
    derived via prediction_tracker.BULLISH_PATTERNS/BEARISH_PATTERNS, the
    same lookup PredictionTracker.on_activities already uses, so a pattern
    untracked by either set (e.g. rectangle, a neutral formation) is simply
    skipped rather than guessed at.

    Never raises — a guiding-scenario lookup failure must not break the
    live candle-close path, matching activity_engine.py's own established
    resilience convention for optional live enrichment (swing detection
    uses the identical try/except pattern). Returns the list of generate_
    recommendations' own result dicts for every activity that actually got
    queued (each {"id", "status", "band_kind", "wilson_score",
    "guiding_scenario_id"}) — deliberately just the ids/scores, not a full
    serialized row: the caller (app.py's on-candle-close handler, which has
    real DB access this pure-logic module doesn't) fetches and serializes
    the full row itself if it needs one, e.g. to broadcast over WebSocket —
    keeps this module free of any web-layer dependency."""
    from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

    queued: List[dict] = []
    rs_info = None
    if rule_rows_provider is not None:
        try:
            rs_info = _resolve_rs(session_factory)
        except Exception:
            logger.exception("recommendation_engine: could not resolve %s", DEFAULT_RS_CODE)
    for activity in activities:
        intensity = activity.get("intensity")
        if intensity is None:
            continue
        pattern = activity["activity"]
        if pattern in BULLISH_PATTERNS:
            direction = "bull"
        elif pattern in BEARISH_PATTERNS:
            direction = "bear"
        else:
            continue
        gate = None
        if rs_info is not None and rs_info["strategy_id"] is not None:
            gate = _make_rule_gate(
                session_factory, rs_info, symbol, exchange_segment, activity["timeframe"], pattern,
                rule_rows_provider,
            )
        try:
            result = generate_recommendations(
                session_factory,
                instrument_id=activity["instrument_id"], timeframe=activity["timeframe"],
                pattern=pattern, direction=direction,
                entry_price=float(activity["close_price"]), detected_ts=activity["ts"],
                intensity=float(intensity),
                recommendation_system_id=rs_info["id"] if rs_info is not None else None,
                rule_gate=gate,
            )
        except Exception:
            logger.exception(
                "recommendation_engine: guiding-scenario lookup failed for %s %s %s",
                symbol, exchange_segment, pattern,
            )
            continue
        if result is not None and result["status"] == "queued":
            queued.append(result)
    return queued


def generate_recommendations_batch(
    session_factory, instrument_id: int, timeframe: str, signals: List[dict],
    recommendation_system_id: Optional[int] = None,
) -> List[Optional[dict]]:
    """Batch counterpart to generate_recommendations, for evaluating MANY
    already-known signals against ONE instrument at once (a replay, a
    manual multi-pattern scan, a future watchlist poll's per-instrument
    pass) — bulk-inserts via LibRecommendations.create_bulk instead of one
    create_one call per signal. As of the 2026-09-16 guiding-scenarios
    redesign there's no InstrumentHistoryCache batching win to chase here
    anymore (each lookup is already one cheap indexed query, not a live
    band computation) — this exists purely for the bulk-insert efficiency.

    Each entry in `signals` takes the same fields generate_recommendations
    does — pattern, direction, entry_price, detected_ts, intensity,
    window_kind (opt, defaults to DEFAULT_WINDOW_KIND), rsi_state, rsi_trend,
    macd_state, macd_trend, stoch_state, stoch_trend, parent_recommendation_id
    (opt), regeneration_count (opt, default 0). Returns one Optional[dict]
    result per input signal, same order, same shape as generate_
    recommendations's own return value."""
    settings = load_system_settings(session_factory, recommendation_system_id)
    expiry_candles = _setting(settings, "recommendation_expiry_candles")
    win_pct_threshold_applied = _setting(settings, "recommendation_win_pct_threshold")
    min_sample_size_applied = int(_setting(settings, "recommendation_min_sample_size"))
    wilson_confidence_applied = _setting(settings, "recommendation_wilson_confidence")

    results: List[Optional[dict]] = [None] * len(signals)
    rows_to_insert: List[dict] = []
    row_result_index: List[int] = []  # rows_to_insert[i] belongs at results[row_result_index[i]]

    with session_scope(session_factory) as session:
        for i, sig in enumerate(signals):
            intensity = sig.get("intensity")
            if intensity is None:
                continue
            window_kind = sig.get("window_kind", DEFAULT_WINDOW_KIND)
            scenario = guiding_scenarios.match_guiding_scenario(
                session, instrument_id, timeframe, sig["pattern"], window_kind, intensity,
            )
            if scenario is None:
                continue

            generated_at = datetime.now(timezone.utc)
            expires_at = generated_at + candle_duration(timeframe) * expiry_candles
            rows_to_insert.append({
                "source": SOURCE, "recommendation_system_id": recommendation_system_id,
                "instrument_id": instrument_id, "timeframe": timeframe,
                "pattern": sig["pattern"], "direction": sig["direction"],
                "entry_price": sig["entry_price"], "detected_ts": sig["detected_ts"],
                "signal_intensity": intensity,
                "signal_rsi_state": sig.get("rsi_state"), "signal_rsi_trend": sig.get("rsi_trend"),
                "signal_macd_state": sig.get("macd_state"), "signal_macd_trend": sig.get("macd_trend"),
                "signal_stoch_state": sig.get("stoch_state"), "signal_stoch_trend": sig.get("stoch_trend"),
                "signal_rsi": sig.get("rsi_value"), "signal_macd_line": sig.get("macd_line"),
                "signal_macd_signal": sig.get("macd_signal"), "signal_stoch_k": sig.get("stoch_value"),
                "band_kind": "intensity", "band_label": "guiding_scenario",
                "band_min": float(scenario.band_min), "band_max": float(scenario.band_max),
                "qualifying_checkpoint": scenario.checkpoint, "sample_count": scenario.sample_count,
                "win_count": scenario.win_count, "win_pct": float(scenario.win_pct),
                "wilson_score": float(scenario.wilson_score),
                "veto_reason": None, "confirmations_checked": 0, "confirmations_passed": 0,
                "win_pct_threshold_applied": win_pct_threshold_applied,
                "min_sample_size_applied": min_sample_size_applied,
                "wilson_confidence_applied": wilson_confidence_applied,
                "guiding_scenario_id": scenario.id, "window_kind": window_kind,
                "status": "queued", "generated_at": generated_at, "expires_at": expires_at,
                "regeneration_count": sig.get("regeneration_count", 0),
                "parent_recommendation_id": sig.get("parent_recommendation_id"),
            })
            row_result_index.append(i)

    ids = LibRecommendations.create_bulk(session_factory, rows_to_insert)
    for row_id, row, result_idx in zip(ids, rows_to_insert, row_result_index):
        if row_id is not None:
            results[result_idx] = {
                "id": row_id, "status": row["status"], "band_kind": row["band_kind"],
                "wilson_score": row["wilson_score"], "guiding_scenario_id": row["guiding_scenario_id"],
            }
    return results


def sweep_and_regenerate(session_factory, now: Optional[datetime] = None) -> Dict[str, int]:
    """Expires every past-due queued recommendation, then — capped by
    recommendation_max_regenerations — re-runs generate_recommendations
    against FRESH guiding-scenario data ("go for analysis again"), chaining
    the result via parent_recommendation_id. A signal at its regeneration
    cap is simply left "expired" — no error, no further attempt.
    max_regenerations is resolved per-row against that row's own
    recommendation_system_id (a batch can contain rows from several RS's,
    each with its own override) rather than once globally.

    A row swept from before the 2026-09-16 redesign has no window_kind
    (column added that day) — falls back to DEFAULT_WINDOW_KIND rather than
    erroring, same as generate_recommendations's own default."""
    expired = LibRecommendations.sweep_expired(session_factory, now)
    regenerated = 0
    for row in expired:
        settings = load_system_settings(session_factory, row["recommendation_system_id"])
        max_regenerations = int(_setting(settings, "recommendation_max_regenerations"))
        if row["regeneration_count"] >= max_regenerations:
            continue
        result = generate_recommendations(
            session_factory,
            instrument_id=row["instrument_id"], timeframe=row["timeframe"],
            pattern=row["pattern"], direction=row["direction"],
            entry_price=row["entry_price"], detected_ts=row["detected_ts"],
            intensity=row["signal_intensity"],
            window_kind=row.get("window_kind") or DEFAULT_WINDOW_KIND,
            rsi_state=row["signal_rsi_state"], rsi_trend=row["signal_rsi_trend"],
            macd_state=row["signal_macd_state"], macd_trend=row["signal_macd_trend"],
            stoch_state=row["signal_stoch_state"], stoch_trend=row["signal_stoch_trend"],
            rsi_value=row["signal_rsi"], macd_line=row["signal_macd_line"],
            macd_signal=row["signal_macd_signal"], stoch_value=row["signal_stoch_k"],
            parent_recommendation_id=row["id"], regeneration_count=row["regeneration_count"] + 1,
            recommendation_system_id=row["recommendation_system_id"],
        )
        if result is not None and result["status"] == "queued":
            regenerated += 1
    return {"expired": len(expired), "regenerated": regenerated}
