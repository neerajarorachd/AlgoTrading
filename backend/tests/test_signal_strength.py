"""ALGO-6: signal strength -- every formation is recorded, but EVENT(name)
only counts the strong ones (minimums from the rules, or the pattern's own)."""
import pandas as pd
import pytest

from condition_evaluator import (
    EVENTS_COLUMN, TRADING_DATE_COLUMN, FormulaError, parse_and_evaluate, required_lookback, validate_condition,
)
from db.ops import LibSignalStrength as ops
from signal_strength import STRENGTH_MEASURES


def frame(rows, events):
    df = pd.DataFrame(rows)
    df[TRADING_DATE_COLUMN] = 0
    df[EVENTS_COLUMN] = [frozenset(e) for e in events]
    return df


def candle(o, h, l, c, v=100.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


# two hammers on a ~100 stock: candle 1 is tiny (0.1% range), candle 2 is real (0.5% range)
HAMMERS = frame(
    [candle(100, 100.02, 99.95, 100.01), candle(100, 100.03, 99.92, 100.02), candle(100, 100.05, 99.55, 100.04)],
    [(), {"hammer"}, {"hammer"}],
)
RULES = {"hammer": {"range_pct": 0.2}}


def as_list(s):
    return [bool(x) for x in s.tolist()]


def test_every_formation_is_present_but_only_strong_ones_qualify():
    assert as_list(parse_and_evaluate("EVENT_ANY(hammer)", HAMMERS)) == [False, True, True]
    assert as_list(parse_and_evaluate("EVENT(hammer)", HAMMERS, {"strength_rules": RULES})) == [False, False, True]


def test_without_rules_event_is_presence_only():
    assert as_list(parse_and_evaluate("EVENT(hammer)", HAMMERS)) == [False, True, True]


def test_a_pattern_can_raise_the_minimum_for_itself():
    # 0.5% hammer passes the table's 0.2% but not this pattern's 0.6%
    assert as_list(parse_and_evaluate("EVENT(hammer, range_pct=0.6)", HAMMERS, {"strength_rules": RULES})) == [False, False, False]
    # and can lower it too
    assert as_list(parse_and_evaluate("EVENT(hammer, range_pct=0.05)", HAMMERS, {"strength_rules": RULES})) == [False, True, True]


def test_unknown_measure_and_event_any_minimums_are_rejected():
    with pytest.raises(FormulaError, match="unknown strength measure 'size'"):
        parse_and_evaluate("EVENT(hammer, size=1)", HAMMERS)
    with pytest.raises(FormulaError, match="takes no minimums"):
        parse_and_evaluate("EVENT_ANY(hammer, range_pct=1)", HAMMERS)
    assert "unknown strength measure" in validate_condition("EVENT(doji, wick_ratio=2)", None, None)
    assert validate_condition("EVENT(hammer, range_pct=0.3, volume_ratio=1.5)", None, None) is None


def test_three_black_crows_total_move_and_median_body():
    # three falling candles: strong (1.2% total, bodies 0.4%) vs weak (0.15% total)
    strong = frame([candle(100, 100, 99.5, 99.6), candle(99.6, 99.6, 99.1, 99.2), candle(99.2, 99.2, 98.7, 98.8)],
                   [(), (), {"three_black_crows"}])
    weak = frame([candle(100, 100, 99.9, 99.95), candle(99.95, 99.95, 99.85, 99.9), candle(99.9, 99.9, 99.8, 99.85)],
                 [(), (), {"three_black_crows"}])
    rules = {"strength_rules": {"three_black_crows": {"move_pct": 0.35, "median_body_pct": 0.08}}}
    assert parse_and_evaluate("EVENT(three_black_crows)", strong, rules).iloc[-1]
    assert not parse_and_evaluate("EVENT(three_black_crows)", weak, rules).iloc[-1]


def test_abs_and_median():
    df = frame([candle(10, 11, 9, 10), candle(10, 11, 9, 12), candle(10, 11, 9, 8)], [(), (), ()])
    assert parse_and_evaluate("abs(CLOSE - OPEN)", df).tolist() == [0.0, 2.0, 2.0]
    assert parse_and_evaluate("median(CLOSE, 3)", df).tolist()[-1] == 10.0


def test_every_measure_formula_is_valid():
    for signal, measures in STRENGTH_MEASURES.items():
        for measure, formula in measures.items():
            # a measure is a number; validate it the way EVENT uses it: measure >= minimum
            assert validate_condition(formula, ">=", "0") is None, (signal, measure, formula)


def test_lookback_includes_the_strength_measures():
    assert required_lookback("EVENT(hammer)") == 20  # volume_ratio reads mean(VOLUME, 20)
    assert required_lookback("EVENT_ANY(rsi_cross_above_60)") == 1


# ---- the rules table ----

def test_user_rules_override_system_ones(session_factory):
    ops.set_rule(session_factory, "hammer", "range_pct", 0.2)
    ops.set_rule(session_factory, "hammer", "volume_ratio", 1.0)
    ops.set_rule(session_factory, "hammer", "range_pct", 0.35, owner_id=1)
    with session_factory() as s:
        assert ops.load_rules(s) == {"hammer": {"range_pct": 0.2, "volume_ratio": 1.0}}
        assert ops.load_rules(s, owner_id=1) == {"hammer": {"range_pct": 0.35, "volume_ratio": 1.0}}


def test_seed_is_idempotent_and_keeps_tuned_values(session_factory):
    ops.seed_defaults(session_factory, {"hammer": {"range_pct": 0.2}})
    ops.set_rule(session_factory, "hammer", "range_pct", 0.3)  # tuned
    ops.seed_defaults(session_factory, {"hammer": {"range_pct": 0.2}, "doji": {"range_pct": 0.15}})
    with session_factory() as s:
        assert ops.load_rules(s) == {"hammer": {"range_pct": 0.3}, "doji": {"range_pct": 0.15}}


def test_rule_for_an_unknown_measure_is_rejected(session_factory):
    with pytest.raises(ops.StrengthRuleError, match="has no strength measure"):
        ops.set_rule(session_factory, "doji", "wick_ratio", 2)


def test_an_unmeasurable_strength_minimum_is_not_met():
    # user, 2026-10-16: "None is always true, remove from everywhere" -- a
    # measure that can't be computed (no 20-candle volume average yet) fails
    df = frame([candle(100, 100.05, 99.55, 100.04)], [{"hammer"}])
    rules = {"strength_rules": {"hammer": {"range_pct": 0.2, "volume_ratio": 1.0}}}
    assert not parse_and_evaluate("EVENT(hammer)", df, rules).iloc[0]
    only_range = {"strength_rules": {"hammer": {"range_pct": 0.2}}}
    assert parse_and_evaluate("EVENT(hammer)", df, only_range).iloc[0]  # measurable minimum alone decides
