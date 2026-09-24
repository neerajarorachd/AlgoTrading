import pytest

from db.models import Strategy
from db.ops import LibStrategies, LibStrategyElements

EVENT_CATALOG = [
    ("doji", "single_candle", "A candle with almost no body"),
    ("double_top", "graph_formation", "Two comparable swing highs"),
]


def _condition(element_code, **overrides):
    """A full-key condition dict, matching exactly what LibStrategies'
    own _condition_to_dict always produces (every optional field present,
    None where unset) — so tree equality comparisons in these tests are
    apples-to-apples rather than failing on missing-vs-explicit-None keys."""
    base = {
        "element_code": element_code, "operator": None, "compare_type": None,
        "compared_element_code": None, "static_value": None,
        "static_value_min": None, "static_value_max": None, "static_value_step": None,
        "left_formula": None, "right_formula": None,
    }
    base.update(overrides)
    return base


# (RSI < 40 AND doji fired) OR (MA21 > VWAP)
NESTED_TREE = {
    "operator": "OR",
    "conditions": [
        _condition("ma21", operator=">", compare_type="element", compared_element_code="vwap"),
    ],
    "groups": [
        {
            "operator": "AND",
            "conditions": [
                _condition("rsi", operator="<", compare_type="static", static_value=40.0),
                _condition("doji"),
            ],
            "groups": [],
        },
    ],
}


def _create_strategy(session_factory, tree=NESTED_TREE, **overrides):
    """create() takes an already-open session (its primary caller is the
    Flask routes, via g.db_session — see LibStrategies.create's own
    docstring for why it doesn't own a session_factory-based transaction
    itself), so tests open one and commit it explicitly, same as any
    other caller outside a request would."""
    fields = dict(name="Test Strategy", strategy_type="entry", description=None, family=None, parent_id=None)
    fields.update(overrides)
    with session_factory() as session:
        strategy_id = LibStrategies.create(session, fields, tree)
        session.commit()
    return strategy_id


# --------------------------------------------------------------------- LibStrategyElements

def test_seed_strategy_elements_creates_event_and_numeric_rows(session_factory):
    LibStrategyElements.seed_strategy_elements(session_factory, EVENT_CATALOG)
    with session_factory() as session:
        elements = {e.code: e for e in LibStrategyElements.get_all(session)}
    assert elements["doji"].element_type == "event"
    assert elements["doji"].source == "instrument_activity"
    assert elements["rsi"].element_type == "numeric"
    assert elements["rsi"].source == "candle_indicators"
    assert elements["close"].source == "candle"


def test_seed_strategy_elements_is_idempotent(session_factory):
    LibStrategyElements.seed_strategy_elements(session_factory, EVENT_CATALOG)
    LibStrategyElements.seed_strategy_elements(session_factory, EVENT_CATALOG)
    with session_factory() as session:
        count = len(LibStrategyElements.get_all(session))
    assert count == len(EVENT_CATALOG) + len(LibStrategyElements.NUMERIC_ELEMENT_CATALOG)


# --------------------------------------------------------------------- LibStrategies tree round-trip

def test_create_and_get_tree_round_trips_a_nested_structure(session_factory):
    strategy_id = _create_strategy(session_factory)
    with session_factory() as session:
        tree = LibStrategies.get_tree(session, strategy_id)
    assert tree == NESTED_TREE


def test_get_tree_returns_none_for_a_strategy_with_no_conditions(session_factory):
    strategy_id = _create_strategy(session_factory, tree=None, name="Empty")
    with session_factory() as session:
        assert LibStrategies.get_tree(session, strategy_id) is None


def test_get_all_and_get_by_id(session_factory):
    strategy_id = _create_strategy(session_factory, name="Alpha")
    with session_factory() as session:
        all_strategies = LibStrategies.get_all(session)
        by_id = LibStrategies.get_by_id(session, strategy_id)
    assert [s.name for s in all_strategies] == ["Alpha"]
    assert by_id.id == strategy_id
    assert by_id.strategy_type == "entry"


def test_replace_tree_swaps_out_the_old_tree_entirely(session_factory):
    strategy_id = _create_strategy(session_factory)
    new_tree = {"operator": "AND", "conditions": [_condition("double_top")], "groups": []}

    with session_factory() as session:
        LibStrategies.replace_tree(session, strategy_id, new_tree)
        session.commit()

    with session_factory() as session:
        tree = LibStrategies.get_tree(session, strategy_id)
    assert tree == new_tree


def test_update_fields_changes_metadata_without_touching_the_tree(session_factory):
    strategy_id = _create_strategy(session_factory, name="Original")

    with session_factory() as session:
        LibStrategies.update_fields(session, strategy_id, {"name": "Renamed", "description": "updated"})
        session.commit()

    with session_factory() as session:
        row = LibStrategies.get_by_id(session, strategy_id)
        tree = LibStrategies.get_tree(session, strategy_id)
    assert row.name == "Renamed"
    assert row.description == "updated"
    assert tree == NESTED_TREE


def test_delete_cascades_groups_and_conditions(session_factory):
    strategy_id = _create_strategy(session_factory)

    with session_factory() as session:
        LibStrategies.delete(session, strategy_id)
        session.commit()

    with session_factory() as session:
        assert LibStrategies.get_by_id(session, strategy_id) is None
        assert LibStrategies.get_tree(session, strategy_id) is None
        assert session.query(Strategy).count() == 0


def test_strategy_parent_id_supports_duplication(session_factory):
    original_id = _create_strategy(session_factory, name="Original")
    duplicate_id = _create_strategy(session_factory, name="Original (copy)", parent_id=original_id)

    with session_factory() as session:
        duplicate = LibStrategies.get_by_id(session, duplicate_id)
    assert duplicate.parent_id == original_id
