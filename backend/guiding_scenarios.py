"""Precomputed, weekly-regenerated "this pattern + intensity band has
historically won often enough to trust" data — see db/models.py's
GuidingScenario/GuidingScenarioIndicatorStat for the full schema rationale.
Replaces RS1's old live per-signal PatternOutcome analysis
(recommendation_engine.py, built 2026-09-15) with a cheap, indexed lookup
against a small precomputed table, mined once a week instead of computed on
every live signal — the same problem this session's storage-normalization
pass is fighting: pattern_outcomes alone is 300k+ rows for one stock's
1-min history.

Explicit product decision (2026-09-16): the decision gate is pattern +
intensity band ONLY — indicator state/trend must never interfere in the
take-call/pass decision. They're still computed and stored
(GuidingScenarioIndicatorStat), scoped to each qualifying scenario's own
occurrence subset, purely for education/further analysis and future
volume/SL-target sizing — match_guiding_scenario never reads them.

Two independent lookback windows are generated and stored side by side
(window_kind "2y"/"3m", see WINDOW_LOOKBACK_DAYS) — no combination rule
decided yet; a caller can consult one, the other, or both.

Revised 2026-09-16 after the first real run only found 8 qualifying
scenarios for HINDCOPPER ("not acceptable... find internal scenarios"):
(1) min_sample_size default lowered 10 -> 7 (recommendation_min_sample_size),
and (2) a band is now checked against ALL 5 PatternOutcome checkpoints
(5/10/15/20/30 candles), not just one fixed one — "review internal
threshold data... if there are 50% acceptance, don't discard it" — a band
that misses the bar at one checkpoint can still be a real 70%+ scenario at
another, and that used to be silently thrown away. See
_best_qualifying_checkpoint's own docstring for the exact mechanism and its
stated trade-off (5 chances to qualify instead of 1 — deliberate, not an
oversight). The win_pct_threshold gate itself (70%) is UNCHANGED — this
searches harder for evidence, it does not lower the bar evidence must
clear.

Regeneration is delete-and-bulk-reinsert per (instrument_id, timeframe,
window_kind) — the qualifying set can genuinely gain or lose members week
to week; simpler than diffing, and matches the "clean re-fetch over patch"
approach already used for HINDCOPPER's own historical data-quality fix
earlier this session.

No scheduler is wired here, matching this project's own established
convention (see strategy_generation.py's identical stance, and feed/
gap_fill.py's design philosophy of lightweight on-demand work over standing
infrastructure): this module is a plain, idempotent, callable function;
running it weekly is a manual/external-scheduler decision.
backend/scripts/generate_guiding_scenarios.py is the manually-runnable
entry point.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from db.models import GuidingScenario, GuidingScenarioIndicatorStat
from db.ops import LibPatternOutcomes, LibSystemSettings
from db.session import session_scope
from stats_utils import wilson_lower_bound
from timeframes import candle_duration

WINDOW_LOOKBACK_DAYS: Dict[str, int] = {"2y": 730, "3m": 90}

DEFAULT_BANDS = 3

# dimension -> (indicator_state_from_outcomes's own top-level key, "state"|"trend")
_DIMENSION_KEYS: Tuple[Tuple[str, str, str], ...] = (
    ("rsi_state", "rsi", "state"), ("rsi_trend", "rsi", "trend"),
    ("macd_state", "macd", "state"), ("macd_trend", "macd", "trend"),
    ("stoch_state", "stoch", "state"), ("stoch_trend", "stoch", "trend"),
)


def _resolve_settings(
    session_factory, min_sample_size: Optional[int],
    win_pct_threshold: Optional[float], wilson_confidence: Optional[float],
) -> Tuple[int, float, float]:
    """Falls back to the SAME SystemSetting keys recommendation_engine.py
    already seeds (recommendation_min_sample_size/win_pct_threshold/
    wilson_confidence) for whichever of the 3 tuning knobs the caller
    didn't pass explicitly — no new settings invented for this module.
    recommendation_qualifying_checkpoint (a single fixed checkpoint) was
    removed 2026-09-16 alongside the single-checkpoint gate it configured
    — see _best_qualifying_checkpoint's own docstring."""
    if None not in (min_sample_size, win_pct_threshold, wilson_confidence):
        return min_sample_size, win_pct_threshold, wilson_confidence
    with session_scope(session_factory) as session:
        settings = LibSystemSettings.load_by_keys(session, (
            "recommendation_min_sample_size", "recommendation_win_pct_threshold", "recommendation_wilson_confidence",
        ))
    return (
        min_sample_size if min_sample_size is not None else int(settings.get("recommendation_min_sample_size", 7)),
        win_pct_threshold if win_pct_threshold is not None else settings.get("recommendation_win_pct_threshold", 0.70),
        wilson_confidence if wilson_confidence is not None else settings.get("recommendation_wilson_confidence", 0.95),
    )


def _majority_direction(outcomes) -> Optional[str]:
    """A band's occurrences all share one pattern, and PatternOutcome's own
    `direction` is a pure function of the pattern name (pattern_outcome_
    analysis._direction_for) — so every outcome in a band has the identical
    direction (or all None, for a direction-less pattern like rectangle).
    Just takes the first non-None value rather than assuming a fixed
    position, so an all-None band still returns None cleanly."""
    return next((o.direction for o in outcomes if o.direction in ("bull", "bear")), None)


def _best_qualifying_checkpoint(
    stats: dict, min_sample_size: int, win_pct_threshold: float, wilson_confidence: float,
) -> Optional[Tuple[int, dict, float]]:
    """Scans ALL of PatternOutcome's checkpoints (5/10/15/20/30 candles,
    LibPatternOutcomes.CHECKPOINTS) for one band's own stats, instead of
    gating on a single fixed checkpoint — "review internal threshold data,
    don't discard a band just because it's 50% at one checkpoint"
    (2026-09-16): a band that misses the bar at 20 candles can still be a
    genuine 70%+ scenario measured at 10, and that evidence must not be
    silently thrown away just because a different checkpoint used to be
    "the" one. Returns (checkpoint, checkpoint_stats, wilson_score) for
    whichever qualifying checkpoint scores highest, or None if none of the
    5 clears the bar. Trade-off, stated plainly: checking 5 checkpoints
    per band is 5 chances to clear the bar rather than 1, which the user
    explicitly asked for (a deliberate loosening, not an oversight) — the
    win_pct_threshold/min_sample_size gate itself is unchanged, still
    applied per checkpoint exactly as before."""
    best = None
    for cp_n in LibPatternOutcomes.CHECKPOINTS:
        cp = stats["checkpoints"][cp_n]
        total, matched, win_pct = cp["total"], cp["matched"], cp["pct"]
        if total < min_sample_size or win_pct is None or win_pct < win_pct_threshold:
            continue
        score = wilson_lower_bound(matched, total, wilson_confidence)
        if best is None or score > best[2]:
            best = (cp_n, cp, score)
    return best


def _indicator_stat_rows(outcomes, checkpoint: int, wilson_confidence: float) -> List[dict]:
    """The informational/education layer for one qualifying band — marginal
    win-rate breakdown per indicator state/trend, scoped to ONLY this
    band's own occurrence subset (not the whole pattern). Never read by
    match_guiding_scenario; see this module's own docstring."""
    states = LibPatternOutcomes.indicator_state_from_outcomes(outcomes)
    rows: List[dict] = []
    for dimension, indicator, sub in _DIMENSION_KEYS:
        for label_band in states[indicator][sub]:
            cp = label_band["checkpoints"][checkpoint]
            total, matched, win_pct = cp["total"], cp["matched"], cp["pct"]
            if total == 0 or win_pct is None:
                continue
            rows.append({
                "dimension": dimension, "label": label_band["label"],
                "sample_count": total, "win_count": matched, "win_pct": win_pct,
                "wilson_score": wilson_lower_bound(matched, total, wilson_confidence),
            })
    return rows


def generate_guiding_scenarios(
    session_factory, instrument_id: int, timeframe: str, window_kind: str,
    min_sample_size: Optional[int] = None,
    win_pct_threshold: Optional[float] = None, wilson_confidence: Optional[float] = None,
    bands: int = DEFAULT_BANDS,
) -> dict:
    """Mines this instrument/timeframe's PatternOutcome history (over
    window_kind's own lookback, see WINDOW_LOOKBACK_DAYS) for intensity
    bands whose win rate clears win_pct_threshold with enough sample_count
    AT ANY of PatternOutcome's 5 checkpoints (5/10/15/20/30 candles — see
    _best_qualifying_checkpoint; a band is no longer discarded just because
    ONE particular checkpoint missed the bar), scores the best-qualifying
    checkpoint via Wilson lower bound, and REPLACES (delete + bulk-insert)
    this instrument/timeframe/window_kind's whole GuidingScenario set with
    exactly the qualifying bands — plus, for each qualifying band, its own
    GuidingScenarioIndicatorStat breakdown (computed at that SAME winning
    checkpoint).

    One InstrumentHistoryCache load for the whole instrument/timeframe (2
    queries total), not one query per pattern — same reasoning as
    strategy_generation.rank_patterns.

    Returns {"window_kind", "scenarios_written", "indicator_stats_written",
    "patterns_scanned"}."""
    if window_kind not in WINDOW_LOOKBACK_DAYS:
        raise ValueError(f"unknown window_kind {window_kind!r}, expected one of {tuple(WINDOW_LOOKBACK_DAYS)}")

    min_sample_size, win_pct_threshold, wilson_confidence = _resolve_settings(
        session_factory, min_sample_size, win_pct_threshold, wilson_confidence,
    )

    ts_to = datetime.now(timezone.utc)
    ts_from = ts_to - candle_duration("1day") * WINDOW_LOOKBACK_DAYS[window_kind]

    scenario_rows: List[dict] = []
    indicator_rows_per_scenario: List[List[dict]] = []

    with session_scope(session_factory) as session:
        cache = LibPatternOutcomes.InstrumentHistoryCache(session, instrument_id, [timeframe], ts_from, ts_to)
        patterns = cache.patterns_in_scope()

        for pattern in patterns:
            band_stats = cache.intensity_banded_analysis(pattern, bands=bands)
            band_outcome_lists = cache.band_outcomes(pattern, bands=bands)
            for stats, outcomes in zip(band_stats, band_outcome_lists):
                best = _best_qualifying_checkpoint(stats, min_sample_size, win_pct_threshold, wilson_confidence)
                if best is None:
                    continue
                cp_n, cp, score = best
                scenario_rows.append({
                    "instrument_id": instrument_id, "timeframe": timeframe, "pattern": pattern,
                    "window_kind": window_kind, "direction": _majority_direction(outcomes),
                    "band_min": stats["intensity_min"], "band_max": stats["intensity_max"],
                    "checkpoint": cp_n, "sample_count": cp["total"], "win_count": cp["matched"],
                    "win_pct": cp["pct"], "wilson_score": score,
                    "win_pct_threshold_applied": win_pct_threshold, "min_sample_size_applied": min_sample_size,
                    "generated_at": datetime.now(timezone.utc),
                })
                indicator_rows_per_scenario.append(_indicator_stat_rows(outcomes, cp_n, wilson_confidence))

        session.query(GuidingScenario).filter_by(
            instrument_id=instrument_id, timeframe=timeframe, window_kind=window_kind,
        ).delete()
        session.flush()

        scenario_objs = [GuidingScenario(**row) for row in scenario_rows]
        session.add_all(scenario_objs)
        session.flush()  # need each new row's own .id before inserting its children

        indicator_count = 0
        indicator_objs: List[GuidingScenarioIndicatorStat] = []
        for scenario_obj, indicator_rows in zip(scenario_objs, indicator_rows_per_scenario):
            for indicator_row in indicator_rows:
                indicator_objs.append(GuidingScenarioIndicatorStat(guiding_scenario_id=scenario_obj.id, **indicator_row))
                indicator_count += 1
        session.add_all(indicator_objs)

    return {
        "window_kind": window_kind, "scenarios_written": len(scenario_rows),
        "indicator_stats_written": indicator_count, "patterns_scanned": len(patterns),
    }


def match_guiding_scenario(
    session, instrument_id: int, timeframe: str, pattern: str, window_kind: str, intensity: float,
) -> Optional[GuidingScenario]:
    """The live lookup — one indexed query against the small precomputed
    table (instrument_id, timeframe, pattern, window_kind + intensity
    falling within a stored band), no scan of raw PatternOutcome. Returns
    the matched row, or None — a pattern/intensity with nothing stored
    (never analyzed, or analyzed but never cleared the win% bar) is a
    clean, fast "pass," not an error. A live intensity outside every
    stored band's own range is simply no match — unlike recommendation_
    engine's old _find_intensity_band, this does NOT fall back to the
    nearest edge band: a precomputed guiding scenario is a specific,
    already-decided claim ("this exact range has historically won"), not a
    live approximation to stretch to fit whatever the signal happens to be."""
    return (
        session.query(GuidingScenario)
        .filter(
            GuidingScenario.instrument_id == instrument_id,
            GuidingScenario.timeframe == timeframe,
            GuidingScenario.pattern == pattern,
            GuidingScenario.window_kind == window_kind,
            GuidingScenario.band_min <= intensity,
            GuidingScenario.band_max >= intensity,
        )
        .order_by(GuidingScenario.wilson_score.desc())
        .first()
    )
