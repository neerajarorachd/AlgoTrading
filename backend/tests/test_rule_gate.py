from datetime import datetime, timedelta, timezone

import pandas as pd

import recommendation_engine as re_mod
import rule_gate as rg
from condition_evaluator import formula_operands_available, frame_from_rows, required_lookback
from db.models import GuidingScenario, Recommendation, RecommendationSystem, Strategy, SubscribedSymbol
from db.ops import LibRecommendationSystems, LibStrategies, LibSystemSettings
from stats_utils import wilson_lower_bound

T0 = datetime(2026, 9, 18, 4, 0, tzinfo=timezone.utc)  # 09:30 IST


def _rows(volumes, rsi=50.0, day_offset=0):
    return [
        {"ts": T0 + timedelta(days=day_offset, minutes=i), "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
         "volume": v, "rsi": rsi}
        for i, v in enumerate(volumes)
    ]


def _tree(*leaves, operator="AND"):
    return {"operator": operator, "groups": [], "conditions": [
        {"left_formula": left, "operator": op, "right_formula": right} for left, op, right in leaves
    ]}


# ------------------------------------------------------------------ evaluator helpers

def test_required_lookback_covers_offsets_slope_and_mean():
    assert required_lookback("close > 1") == 1
    assert required_lookback("close[-3] < open[-1]") == 4
    assert required_lookback("slope(rsi, 5) > 0") == 6
    assert required_lookback("mean(volume, 5) > 100") == 5


def test_operands_unavailable_when_history_is_too_short():
    df = frame_from_rows(_rows([100, 200, 300]))
    assert formula_operands_available("volume", df) is True
    assert formula_operands_available("mean(volume, 3)", df) is True
    assert formula_operands_available("mean(volume, 5)", df) is False  # only 3 candles
    assert formula_operands_available("close[-3]", df) is False


def test_operands_unavailable_when_an_indicator_value_is_missing():
    df = frame_from_rows(_rows([100, 200]))  # no macd_line in these rows
    assert formula_operands_available("rsi", df) is True
    assert formula_operands_available("macd_line", df) is False


def test_offsets_do_not_reach_into_the_previous_day():
    rows = _rows([100, 200], day_offset=-1) + _rows([300], day_offset=0)
    df = frame_from_rows(rows)
    assert formula_operands_available("volume[-1]", df) is False  # today has only its own first candle


# ------------------------------------------------------------------ evaluate_rule / evaluate_rules

def test_rule_passes_and_fails_on_the_last_row():
    df = frame_from_rows(_rows([100, 200, 300, 400, 500]))
    assert rg.evaluate_rule(_tree(("mean(volume, 5)", ">", "250")), df) == (rg.PASSED, None)
    assert rg.evaluate_rule(_tree(("mean(volume, 5)", ">", "350")), df) == (rg.FAILED, None)


def test_rule_with_any_missing_operand_is_skipped_as_a_whole():
    df = frame_from_rows(_rows([100, 200]))
    # first leaf is fine, second lacks history -> the WHOLE rule is skipped, no partial AND
    tree = _tree(("rsi", "<", "40"), ("mean(volume, 5)", ">", "1"))
    assert rg.evaluate_rule(tree, df) == (rg.SKIPPED, "insufficient data")


def test_non_formula_leaf_skips_the_rule():
    df = frame_from_rows(_rows([100]))
    tree = {"operator": "AND", "groups": [], "conditions": [{"element_code": "hammer"}]}
    outcome, reason = rg.evaluate_rule(tree, df)
    assert outcome == rg.SKIPPED and "unsupported" in reason


def test_no_rows_at_all_is_insufficient_data():
    assert rg.evaluate_rules([("R1", _tree(("rsi", "<", "40")))], [], "all") == (
        True, "rule skipped: R1 (insufficient data)")


def test_all_mode_requires_every_evaluable_rule_to_pass():
    rows = _rows([100, 200, 300], rsi=30.0)
    rules = [("R1", _tree(("rsi", "<", "40"))), ("R2", _tree(("volume", ">", "1000")))]
    allowed, note = rg.evaluate_rules(rules, rows, "all")
    assert allowed is False and note == "rules failed: R2"


def test_any_mode_needs_just_one_passing_rule():
    rows = _rows([100, 200, 300], rsi=30.0)
    rules = [("R1", _tree(("rsi", "<", "40"))), ("R2", _tree(("volume", ">", "1000")))]
    assert rg.evaluate_rules(rules, rows, "any") == (True, None)
    failing_only = [("R2", _tree(("volume", ">", "1000")))]
    assert rg.evaluate_rules(failing_only, rows, "any")[0] is False


def test_a_skipped_rule_is_neutral_not_a_failure():
    rows = _rows([100, 200, 300], rsi=30.0)
    rules = [("R1", _tree(("rsi", "<", "40"))), ("R2", _tree(("mean(volume, 9)", ">", "1")))]
    allowed, note = rg.evaluate_rules(rules, rows, "all")
    assert allowed is True and note == "rule skipped: R2 (insufficient data)"


def test_no_rules_means_no_gate():
    assert rg.evaluate_rules([], _rows([1]), "all") == (True, None)


# ------------------------------------------------------------------ rules_for_pattern (DB)

def _make_rule(session, parent_id, name, patterns, tree):
    return LibStrategies.create(session, {
        "name": name, "strategy_type": "recommendation_rule", "parent_id": parent_id,
        "pattern_filter": patterns,
    }, tree=tree)


def test_a_rule_can_cover_many_patterns_and_a_pattern_many_rules(session_factory):
    with session_factory() as session:
        parent = LibStrategies.create(session, {"name": "RS1 parent", "strategy_type": "recommendation_parent"}, None)
        _make_rule(session, parent, "Rule 1", "A,B,C,D,E", _tree(("rsi", "<", "40")))
        _make_rule(session, parent, "Rule 2", "C,D,E,F", _tree(("volume", ">", "1")))
        LibStrategies.create(session, {"name": "unrelated", "strategy_type": "entry", "pattern_filter": "A"},
                              tree=_tree(("rsi", "<", "1")))
        session.commit()

        names = lambda p: sorted(n for n, _ in rg.rules_for_pattern(session, parent, p))  # noqa: E731
        assert names("A") == ["Rule 1"]
        assert names("D") == ["Rule 1", "Rule 2"]
        assert names("F") == ["Rule 2"]
        assert names("G") == []


def test_ensure_parent_strategies_creates_one_parent_and_is_idempotent(session_factory):
    LibRecommendationSystems.seed_defaults(session_factory, re_mod.RECOMMENDATION_SYSTEM_SEED)
    LibRecommendationSystems.ensure_parent_strategies(session_factory)
    LibRecommendationSystems.ensure_parent_strategies(session_factory)  # second call: no duplicate
    with session_factory() as session:
        rs1 = LibRecommendationSystems.get_by_code(session, "RS1")
        parent = LibStrategies.get_by_id(session, rs1.strategy_id)
        assert parent.name == "RS1 (parent)" and parent.strategy_type == "recommendation_parent"
        assert session.query(Strategy).filter_by(strategy_type="recommendation_parent").count() == 1


# ------------------------------------------------------------------ generate_recommendations integration

def _setup_rs1_with_rule(session_factory, tree, combine="all"):
    LibSystemSettings.seed_defaults(session_factory, re_mod.SYSTEM_SETTING_SEED)
    LibRecommendationSystems.seed_defaults(session_factory, re_mod.RECOMMENDATION_SYSTEM_SEED)
    with session_factory() as session:
        inst = SubscribedSymbol(symbol="RELIANCE", exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
                                security_id="S1", previous_close=100.0)
        session.add(inst)
        session.commit()
        parent = LibStrategies.create(session, {"name": "RS1 parent", "strategy_type": "recommendation_parent"}, None)
        _make_rule(session, parent, "Rule 1", "hammer", tree)
        rs = LibRecommendationSystems.get_by_code(session, "RS1")
        rs.strategy_id, rs.rule_combine_mode = parent, combine
        session.add(GuidingScenario(
            instrument_id=inst.id, timeframe="3min", pattern="hammer", window_kind="2y", direction="bull",
            band_min=0.5, band_max=1.5, checkpoint=20, sample_count=10, win_count=8, win_pct=0.8,
            wilson_score=wilson_lower_bound(8, 10, 0.95), win_pct_threshold_applied=0.70,
            min_sample_size_applied=10,
        ))
        session.commit()
        return inst.id


def _activity(instrument_id):
    return {"instrument_id": instrument_id, "timeframe": "3min", "activity": "hammer", "intensity": 1.0,
            "close_price": 101.0, "ts": datetime.now(timezone.utc)}


def _live(session_factory, instrument_id, provider):
    return re_mod.on_activities(session_factory, "RELIANCE", "NSE_EQ", [_activity(instrument_id)],
                                rule_rows_provider=provider)


def test_failing_rule_persists_a_rejected_row_and_queues_nothing(session_factory):
    inst = _setup_rs1_with_rule(session_factory, _tree(("rsi", "<", "40")))
    assert _live(session_factory, inst, lambda *a: _rows([100, 200], rsi=70.0)) == []
    with session_factory() as session:
        row = session.query(Recommendation).one()
    assert row.status == "rejected" and row.veto_reason == "rules failed: Rule 1"


def test_passing_rule_queues_and_stamps_rs1(session_factory):
    inst = _setup_rs1_with_rule(session_factory, _tree(("rsi", "<", "40")))
    queued = _live(session_factory, inst, lambda *a: _rows([100, 200], rsi=30.0))
    assert len(queued) == 1
    with session_factory() as session:
        row = session.query(Recommendation).one()
        rs1 = LibRecommendationSystems.get_by_code(session, "RS1")
    assert row.status == "queued" and row.rule_note is None and row.recommendation_system_id == rs1.id


def test_skipped_rule_still_queues_with_an_insufficient_data_note(session_factory):
    inst = _setup_rs1_with_rule(session_factory, _tree(("mean(volume, 5)", ">", "1")))
    queued = _live(session_factory, inst, lambda *a: _rows([100, 200]))
    assert len(queued) == 1
    with session_factory() as session:
        row = session.query(Recommendation).one()
    assert row.status == "queued" and row.rule_note == "rule skipped: Rule 1 (insufficient data)"


def test_a_crashing_provider_degrades_to_a_skipped_rule(session_factory):
    inst = _setup_rs1_with_rule(session_factory, _tree(("rsi", "<", "40")))

    def boom(*a):
        raise RuntimeError("engine gone")

    assert len(_live(session_factory, inst, boom)) == 1
    with session_factory() as session:
        assert session.query(Recommendation).one().rule_note == "rule skipped: error"


def test_without_a_provider_behavior_is_unchanged(session_factory):
    inst = _setup_rs1_with_rule(session_factory, _tree(("rsi", "<", "40")))
    assert len(re_mod.on_activities(session_factory, "RELIANCE", "NSE_EQ", [_activity(inst)])) == 1
    with session_factory() as session:
        row = session.query(Recommendation).one()
    assert row.recommendation_system_id is None and row.rule_note is None
