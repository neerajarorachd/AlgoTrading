"""Auto-generates (and re-generates) an "OR of best-performing patterns"
Strategy — 2026-09-15, explicit: "get the best performing patterns and
create an oring strategy. suppose there are 5 best performing patterns,
then generate a strategy having 5 parts connected with oring... it should
be updated after every backtest. suppose we schedule backtest on weekly
basis, it should be updated after every week."

Implemented via order_backtest.py's own EngineConfig.pattern_filter (added
earlier the same session to close a different gap — simulate() previously
traded every registered pattern blended together with no way to isolate
one): a comma-separated pattern list IS an OR by construction — simulate()
opens a trade the moment ANY listed pattern fires, never requiring more
than one at once (see pattern_filter's own docstring in order_backtest.py).
This is the mechanism that actually drives real backtest behavior today.
The separate StrategyCondition/StrategyConditionGroup tree does support an
explicit OR operator, but is NOT read by order_backtest.py at all
(confirmed by direct code inspection the same session) — populating it
here would be a second, functionally-inert representation that could drift
out of sync with pattern_filter, so it's deliberately left alone.

Idempotent by design (create-once, update-thereafter, matched by the
strategy's own deterministic name) specifically so this can be called
repeatedly on a schedule ("updated after every week") without creating a
new Strategy row each time — no actual scheduler exists yet (matches this
project's own "no standing infrastructure without a real live deployment"
stance, same reasoning as the deferred live-feed polling system, see
project memory); this function is what a future weekly job would call.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from db.ops import LibPatternOutcomes, LibStrategies
from stats_utils import wilson_lower_bound
from timeframes import candle_duration

DEFAULT_TOP_N = 5
DEFAULT_CHECKPOINT = 20
DEFAULT_MIN_SAMPLE_SIZE = 10
DEFAULT_WILSON_CONFIDENCE = 0.95
DEFAULT_LOOKBACK_DAYS = 730  # "complete 2 years data"

# Shared order-management defaults reused from this session's own real
# HINDCOPPER tests (VWAP-rejection, hammer) — not permanent, just a
# sensible starting point a caller can override via order_mgmt_overrides.
# Trading-window fields are deliberately left unset so EngineConfig's own
# defaults (09:15-14:00, squareoff 14:50) apply unless a caller says
# otherwise — no narrower window was requested for this strategy type.
_DEFAULT_ORDER_MGMT: Dict[str, object] = {
    "capital_per_trade": 200000, "sl_formula_type": "fixed_percent", "sl_fixed_value": 0.002,
    "target_formula_type": "fixed_percent", "target_fixed_value": 0.005,
    "first_order_quantity": 1, "order1_margin_multiplier": 1.0,
    "order2_margin_multiplier": 1.0, "order3_margin_multiplier": 5.0,
    "exit_at_loss": True, "exit_at_loss_count": 1, "max_orders_at_a_time": 1,
}


def rank_patterns(
    session, instrument_id: int, timeframe: str, ts_from: datetime, ts_to: datetime,
    checkpoint: int = DEFAULT_CHECKPOINT, min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE,
    wilson_confidence: float = DEFAULT_WILSON_CONFIDENCE,
) -> List[dict]:
    """Every pattern with enough analyzed history for this instrument/
    timeframe, ranked by Wilson-score lower bound (the same "best option
    wins" metric RS1/guiding_scenarios.py already use — accounts for
    sample size, not just raw win%) at the given checkpoint. A pattern
    below min_sample_size
    is excluded entirely, not just ranked low — same discipline as
    generate_recommendations' own primary gate. Uses ONE
    InstrumentHistoryCache load (2 queries total) regardless of how many
    distinct patterns this instrument has, not one query per pattern."""
    cache = LibPatternOutcomes.InstrumentHistoryCache(session, instrument_id, [timeframe], ts_from, ts_to)
    ranked: List[dict] = []
    for pattern in cache.patterns_in_scope():
        stats = cache.pattern_level_analysis(pattern)
        if stats is None:
            continue
        cp = stats["checkpoints"].get(checkpoint)
        if cp is None or cp["total"] < min_sample_size or cp["pct"] is None:
            continue
        score = wilson_lower_bound(cp["matched"], cp["total"], wilson_confidence)
        ranked.append({
            "pattern": pattern, "sample_count": cp["total"], "win_count": cp["matched"],
            "win_pct": cp["pct"], "wilson_score": score,
        })
    ranked.sort(key=lambda r: r["wilson_score"], reverse=True)
    return ranked


def generate_best_patterns_strategy(
    session_factory, instrument_id: int, timeframe: str,
    strategy_name: str, top_n: int = DEFAULT_TOP_N,
    checkpoint: int = DEFAULT_CHECKPOINT, min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE,
    wilson_confidence: float = DEFAULT_WILSON_CONFIDENCE, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    strategy_type: str = "RS1_best_patterns",
    order_mgmt_overrides: Optional[Dict[str, object]] = None,
) -> dict:
    """Ranks every pattern with enough history (rank_patterns), takes the
    top `top_n`, and creates-or-updates ONE Strategy row whose
    pattern_filter is those pattern names joined with a comma — an OR of
    however many "parts" actually qualify (fewer than top_n if the
    instrument doesn't yet have that many well-sampled patterns; never
    padded with weak ones just to hit the count).

    Matched for update by `strategy_name` (deterministic, e.g. the caller
    passes "RS1_best_patterns_hindcopper") — calling this again later (a
    scheduled weekly re-run) updates the SAME Strategy row in place rather
    than creating a duplicate, so anything already pointing at this
    strategy_id (a saved backtest comparison, a UI bookmark) keeps working
    after a refresh."""
    ts_to = datetime.now(timezone.utc)
    ts_from = ts_to - candle_duration("1day") * lookback_days

    with session_factory() as session:
        ranked = rank_patterns(
            session, instrument_id, timeframe, ts_from, ts_to, checkpoint, min_sample_size, wilson_confidence,
        )
        selected = ranked[:top_n]
        pattern_filter = ",".join(r["pattern"] for r in selected)

        fields = dict(_DEFAULT_ORDER_MGMT)
        if order_mgmt_overrides:
            fields.update(order_mgmt_overrides)
        fields.update({
            "name": strategy_name, "strategy_type": strategy_type, "direction": "both",
            "pattern_filter": pattern_filter,
            "description": (
                f"Auto-generated OR of the top {len(selected)} performing patterns "
                f"(checkpoint={checkpoint}, min_sample_size={min_sample_size}): " +
                "; ".join(f"{r['pattern']} ({r['win_pct']:.1%}, n={r['sample_count']})" for r in selected)
            ),
        })

        existing = LibStrategies.get_by_name(session, strategy_name)
        if existing is None:
            strategy_id = LibStrategies.create(session, fields, tree=None)
            action = "created"
        else:
            LibStrategies.update_fields(session, existing.id, fields)
            strategy_id = existing.id
            action = "updated"
        session.commit()

    return {
        "strategy_id": strategy_id, "action": action, "pattern_filter": pattern_filter,
        "selected_patterns": selected, "all_ranked": ranked,
    }
