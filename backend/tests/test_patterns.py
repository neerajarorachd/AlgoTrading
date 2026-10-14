"""ALGO-31: pattern table -- seeding, validation, read-only system patterns,
copies, and the weight resolver (per-stock only when the setting is on, the
pattern's own weight always the fallback)."""
import pytest

from db.models import Pattern, PatternCondition
from db.ops import LibPatterns as ops
from patterns_catalog import SYSTEM_PATTERNS


def _good(**over):
    data = {"key": "my_setup", "name": "My setup", "pattern_type": "bullish",
            "conditions": ["RSI > 45", "RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL))"]}
    data.update(over)
    return data


def test_seed_is_idempotent_and_keeps_tuned_values(session_factory):
    ops.seed_system_patterns(session_factory, SYSTEM_PATTERNS)
    with session_factory() as s:
        count = s.query(Pattern).filter_by(origin="system").count()
        p = s.query(Pattern).filter_by(key="bullish_momentum").one()
        p.manual_weight, p.enabled = 2, False
        s.commit()
    ops.seed_system_patterns(session_factory, SYSTEM_PATTERNS)
    with session_factory() as s:
        assert s.query(Pattern).filter_by(origin="system").count() == count == len(SYSTEM_PATTERNS)
        p = s.query(Pattern).filter_by(key="bullish_momentum").one()
        assert float(p.manual_weight) == 2 and p.enabled is False  # not reset
        assert s.query(PatternCondition).filter_by(pattern_id=p.id).count() == 6


def test_every_system_pattern_type_is_represented(session_factory):
    types = {p[2] for p in SYSTEM_PATTERNS}
    assert types == {"bullish", "bearish", "caution", "avoid"}


def test_create_and_read_back(session_factory):
    pid = ops.create_pattern(session_factory, _good(manual_weight=4, lookback=8), owner_id=1)
    with session_factory() as s:
        p = ops.get_pattern(s, pid)
    assert p["origin"] == "user" and p["owner_id"] == 1 and p["weight"] == 4 and p["lookback"] == 8
    assert [c["formula"] for c in p["conditions"]] == ["RSI > 45", "RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL))"]


@pytest.mark.parametrize("bad, message", [
    ({"conditions": ["RSI >> 45"]}, "condition 1"),
    ({"conditions": ["WHENEVER(RSI > 45)"]}, "unknown function"),
    ({"conditions": []}, "at least one condition"),
    ({"pattern_type": "sideways"}, "pattern_type"),
    ({"key": "2bad key"}, "key must start"),
    ({"manual_weight": 7}, "between 0 and 5"),
    ({"lookback": 0}, "lookback"),
])
def test_invalid_patterns_are_rejected_with_a_clear_reason(session_factory, bad, message):
    with pytest.raises(ops.PatternError, match=message):
        ops.create_pattern(session_factory, _good(**bad), owner_id=1)


def test_duplicate_key_for_the_same_owner_is_rejected(session_factory):
    ops.create_pattern(session_factory, _good(), owner_id=1)
    with pytest.raises(ops.PatternError, match="already exists"):
        ops.create_pattern(session_factory, _good(), owner_id=1)


def test_system_patterns_are_read_only_except_enabled(session_factory):
    ops.seed_system_patterns(session_factory, SYSTEM_PATTERNS)
    with session_factory() as s:
        pid = s.query(Pattern.id).filter_by(key="strong_hammer").scalar()
    with pytest.raises(ops.PatternError, match="read-only"):
        ops.update_pattern(session_factory, pid, {"manual_weight": 5})
    ops.update_pattern(session_factory, pid, {"enabled": False})
    with session_factory() as s:
        assert s.get(Pattern, pid).enabled is False


def test_copy_makes_an_editable_user_version(session_factory):
    ops.seed_system_patterns(session_factory, SYSTEM_PATTERNS)
    with session_factory() as s:
        src = s.query(Pattern).filter_by(key="strong_hammer").one()
        src_id, src_conditions = src.id, s.query(PatternCondition).filter_by(pattern_id=src.id).count()
    copy_id = ops.copy_pattern(session_factory, src_id, owner_id=1)
    second = ops.copy_pattern(session_factory, src_id, owner_id=1)
    with session_factory() as s:
        c = ops.get_pattern(s, copy_id)
        assert ops.get_pattern(s, second)["key"] == "strong_hammer_copy_2"
    assert c["origin"] == "user" and c["parent_id"] == src_id and c["key"] == "strong_hammer_copy"
    assert len(c["conditions"]) == src_conditions
    ops.update_pattern(session_factory, copy_id, {"manual_weight": 5, "conditions": ["EVENT(hammer)"]})
    with session_factory() as s:
        assert ops.get_pattern(s, copy_id)["weight"] == 5


def test_list_shows_system_plus_own_only(session_factory):
    ops.seed_system_patterns(session_factory, SYSTEM_PATTERNS)
    ops.create_pattern(session_factory, _good(key="mine"), owner_id=1)
    ops.create_pattern(session_factory, _good(key="theirs"), owner_id=2)
    with session_factory() as s:
        keys = {p["key"] for p in ops.list_patterns(s, owner_id=1)}
        bullish = ops.list_patterns(s, owner_id=1, pattern_type="bullish")
    assert "mine" in keys and "theirs" not in keys and "strong_hammer" in keys
    assert all(p["pattern_type"] == "bullish" for p in bullish)


def test_default_weight_manual_or_backtest():
    p = Pattern(manual_weight=3, backtest_weight=4.5, use_manual_weight=True)
    assert ops.default_weight(p) == 3
    p.use_manual_weight = False
    assert ops.default_weight(p) == 4.5
    p.backtest_weight = None  # no backtest yet -> manual
    assert ops.default_weight(p) == 3


def test_effective_weight_uses_per_stock_only_when_on_and_present(session_factory):
    pid = ops.create_pattern(session_factory, _good(manual_weight=3), owner_id=None, origin="system")
    ops.set_stock_weight(session_factory, pid, instrument_id=7, weight=4.5)
    with session_factory() as s:
        p, weights = s.get(Pattern, pid), ops.load_stock_weights(s)
    assert ops.effective_weight(p, 7, weights, use_per_stock=False) == 3     # setting off: default
    assert ops.effective_weight(p, 7, weights, use_per_stock=True) == 4.5    # on, value exists
    assert ops.effective_weight(p, 8, weights, use_per_stock=True) == 3      # on, no value: fallback


def test_stock_weight_upserts(session_factory):
    pid = ops.create_pattern(session_factory, _good(), owner_id=None, origin="system")
    ops.set_stock_weight(session_factory, pid, 7, 2)
    ops.set_stock_weight(session_factory, pid, 7, 4, source="manual")
    with session_factory() as s:
        assert ops.load_stock_weights(s) == {(pid, 7): 4.0}
