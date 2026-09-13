"""Strategy / StrategyConditionGroup / StrategyCondition reads/writes —
together, one strategy's full, arbitrarily-nested AND/OR condition tree.

This module only persists/reconstructs a strategy's DEFINITION. It does
NOT evaluate one against real data — that's a separate, not-yet-built
engine (backtesting's own job, per the project's build order: pattern-
outcome analysis -> Strategies -> backtesting).

Tree shape, as a plain JSON-serializable dict (what get_tree returns and
create/replace_tree accept):
    {
      "operator": "AND" | "OR",
      "conditions": [ {element_code, operator, compare_type,
                        compared_element_code, static_value,
                        static_value_min, static_value_max,
                        static_value_step}, ... ],
      "groups": [ <same shape, recursively>, ... ],
    }
A strategy's root group is the StrategyConditionGroup row with
parent_group_id IS NULL for that strategy_id — not a separate column on
Strategy (see Strategy's own docstring in db/models.py for why).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from db.models import Strategy, StrategyCondition, StrategyConditionGroup


def get_all(session) -> List[Strategy]:
    return session.query(Strategy).filter_by(active=True).order_by(Strategy.name).all()


def get_by_id(session, strategy_id: int) -> Optional[Strategy]:
    return session.query(Strategy).filter_by(id=strategy_id).one_or_none()


def get_tree(session, strategy_id: int) -> Optional[dict]:
    """Recursively reconstructs a strategy's condition tree. Returns None
    if the strategy has no root group yet (e.g. a newly created strategy
    with an empty tree)."""
    root = (
        session.query(StrategyConditionGroup)
        .filter_by(strategy_id=strategy_id, parent_group_id=None)
        .one_or_none()
    )
    if root is None:
        return None

    all_groups = session.query(StrategyConditionGroup).filter_by(strategy_id=strategy_id).all()
    all_conditions = (
        session.query(StrategyCondition)
        .join(StrategyConditionGroup, StrategyCondition.group_id == StrategyConditionGroup.id)
        .filter(StrategyConditionGroup.strategy_id == strategy_id)
        .all()
    )

    conditions_by_group: Dict[int, List[StrategyCondition]] = defaultdict(list)
    for c in all_conditions:
        conditions_by_group[c.group_id].append(c)
    children_by_parent: Dict[int, List[StrategyConditionGroup]] = defaultdict(list)
    for g in all_groups:
        if g.parent_group_id is not None:
            children_by_parent[g.parent_group_id].append(g)

    def _build(group: StrategyConditionGroup) -> dict:
        return {
            "operator": group.operator,
            "conditions": [_condition_to_dict(c) for c in conditions_by_group.get(group.id, [])],
            "groups": [_build(child) for child in children_by_parent.get(group.id, [])],
        }

    return _build(root)


def _condition_to_dict(c: StrategyCondition) -> dict:
    return {
        "element_code": c.element_code,
        "operator": c.operator,
        "compare_type": c.compare_type,
        "compared_element_code": c.compared_element_code,
        "static_value": float(c.static_value) if c.static_value is not None else None,
        "static_value_min": float(c.static_value_min) if c.static_value_min is not None else None,
        "static_value_max": float(c.static_value_max) if c.static_value_max is not None else None,
        "static_value_step": float(c.static_value_step) if c.static_value_step is not None else None,
    }


def create(session, strategy_fields: dict, tree: Optional[dict]) -> int:
    """Creates a new Strategy row plus its full condition tree (if given).
    Takes an already-open `session` rather than owning its own transaction
    — its primary caller is the Flask API routes, which commit once at
    the end of the request via g.db_session's own teardown hook; a
    session_factory-owning variant here would open a SECOND, concurrent
    transaction on the same tables and deadlock SQLite (found the hard
    way — see this function's own test coverage). A standalone caller
    (a script, not a route) can just wrap this in its own
    `with session_factory() as session: ...; session.commit()`.
    Returns the new strategy's id."""
    strategy = Strategy(**strategy_fields)
    session.add(strategy)
    session.flush()  # assigns strategy.id
    strategy_id = strategy.id
    if tree is not None:
        _insert_group(session, strategy_id, None, tree)
        # Final flush: _insert_group's own intermediate flushes (needed to
        # get each group's id before inserting its children) only push
        # whatever happens to be pending AT THAT POINT — the deepest
        # leaf conditions, added last with no flush after them, would
        # otherwise sit unflushed. With this session's autoflush=False, a
        # fresh query (e.g. get_tree, likely called right after this by
        # the same request) would then silently miss them. Found this the
        # hard way via the API round-trip test, not by inspection.
        session.flush()
    return strategy_id


def replace_tree(session, strategy_id: int, tree: Optional[dict]) -> None:
    """Deletes every existing group/condition for this strategy, then
    inserts the new tree — simplest robust "update": a strategy is edited
    as a whole via the UI, not partially patched, so there's no real tree-
    diffing to do."""
    _delete_tree(session, strategy_id)
    if tree is not None:
        _insert_group(session, strategy_id, None, tree)
        session.flush()  # see create()'s own comment on why this is needed


def update_fields(session, strategy_id: int, fields: dict) -> None:
    if fields:
        session.query(Strategy).filter_by(id=strategy_id).update(fields)


def delete(session, strategy_id: int) -> None:
    _delete_tree(session, strategy_id)
    session.query(Strategy).filter_by(id=strategy_id).delete(synchronize_session=False)


def _insert_group(session, strategy_id: int, parent_group_id: Optional[int], node: dict) -> None:
    group = StrategyConditionGroup(
        strategy_id=strategy_id, parent_group_id=parent_group_id, operator=node["operator"],
    )
    session.add(group)
    session.flush()  # assigns group.id — needed before inserting its children
    for cond in node.get("conditions", []):
        session.add(StrategyCondition(
            group_id=group.id,
            element_code=cond["element_code"],
            operator=cond.get("operator"),
            compare_type=cond.get("compare_type"),
            compared_element_code=cond.get("compared_element_code"),
            static_value=cond.get("static_value"),
            static_value_min=cond.get("static_value_min"),
            static_value_max=cond.get("static_value_max"),
            static_value_step=cond.get("static_value_step"),
        ))
    for child in node.get("groups", []):
        _insert_group(session, strategy_id, group.id, child)


def _delete_tree(session, strategy_id: int) -> None:
    groups = session.query(StrategyConditionGroup).filter_by(strategy_id=strategy_id).all()
    group_ids = [g.id for g in groups]
    if group_ids:
        session.query(StrategyCondition).filter(
            StrategyCondition.group_id.in_(group_ids)
        ).delete(synchronize_session=False)
        session.query(StrategyConditionGroup).filter_by(
            strategy_id=strategy_id
        ).delete(synchronize_session=False)
