"""Recommendation-system rule gate: extra, user-authored conditions a live
pattern must satisfy IN ADDITION to matching a guiding scenario.

A rule is a child Strategy (Strategy.parent_id == the RS's parent strategy,
RecommendationSystem.strategy_id) whose `pattern_filter` names the patterns
it covers and whose condition tree is FORMULA leaves only. One rule can
cover many patterns and one pattern can be covered by many rules; how a
pattern's several rules combine is RecommendationSystem.rule_combine_mode
("all" = every evaluable rule must pass, the default; "any" = one is enough).

Three outcomes per rule (decided 2026-09-19): passed, failed, or SKIPPED.
A rule is skipped as a whole -- no partial AND/OR -- when any operand is not
available yet ("insufficient data": start of session, indicator warming up),
when it contains a non-formula leaf, or when it errors. A skipped rule
neither passes nor fails: the pattern is gated by its remaining rules plus
the guiding scenario, and if every rule is skipped it is gated by the
scenario alone. The note is stored on the recommendation so a less-filtered
early-session recommendation is visibly marked as such.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import pandas as pd

from condition_evaluator import (
    FormulaError, evaluate_tree, formula_operands_available, frame_from_rows, required_lookback,
)

logger = logging.getLogger(__name__)

PASSED, FAILED, SKIPPED = "passed", "failed", "skipped"


def _leaves(tree: dict):
    for cond in tree.get("conditions", []):
        yield cond
    for group in tree.get("groups", []):
        yield from _leaves(group)


def rule_lookback(tree: dict) -> int:
    depth = 1
    for cond in _leaves(tree):
        for formula in (cond.get("left_formula"), cond.get("right_formula")):
            if formula:
                depth = max(depth, required_lookback(formula))
    return depth


def summarize_tree(tree: dict) -> str:
    """One-line human summary of a rule's formula tree, e.g.
    "rsi < 40 AND (mean(volume, 5) > 5000 OR close > vwap)"."""
    parts = []
    for cond in tree.get("conditions", []):
        left = cond.get("left_formula") or cond.get("element_code") or "?"
        parts.append(f"{left} {cond['operator']} {cond.get('right_formula')}" if cond.get("operator") else left)
    for group in tree.get("groups", []):
        inner = summarize_tree(group)
        if inner:
            parts.append(f"({inner})")
    return f" {tree.get('operator', 'AND')} ".join(parts)


def evaluate_rule(tree: dict, df: pd.DataFrame, context: Optional[dict] = None) -> Tuple[str, Optional[str]]:
    """(PASSED|FAILED|SKIPPED, reason-if-skipped) on df's LAST row."""
    if df.empty:
        return SKIPPED, "insufficient data"
    try:
        for cond in _leaves(tree):
            if not cond.get("left_formula"):
                return SKIPPED, "unsupported condition type (formula leaves only)"
            for formula in (cond["left_formula"], cond.get("right_formula")):
                if formula and not formula_operands_available(formula, df, context):
                    return SKIPPED, "insufficient data"
        return (PASSED if bool(evaluate_tree(tree, df, context).iloc[-1]) else FAILED), None
    except FormulaError as exc:
        return SKIPPED, f"formula error: {exc}"


def rules_for_pattern(session, parent_strategy_id: int, pattern: str) -> List[Tuple[str, dict]]:
    """[(rule name, tree)] for every ACTIVE child of the parent strategy
    whose pattern_filter lists `pattern`."""
    from db.models import Strategy
    from db.ops import LibStrategies

    children = session.query(Strategy).filter(
        Strategy.parent_id == parent_strategy_id, Strategy.active.is_(True),
    ).all()
    out = []
    for child in children:
        patterns = {p.strip() for p in (child.pattern_filter or "").split(",") if p.strip()}
        if pattern in patterns:
            out.append((child.name, LibStrategies.get_tree(session, child.id)))
    return out


def evaluate_rules(rules: List[Tuple[str, dict]], rows: list, combine_mode: str = "all",
                   context: Optional[dict] = None) -> Tuple[bool, Optional[str]]:
    """(allowed, note). `rows` = chronological in-memory frame rows for the
    firing candle's symbol/timeframe (last row = the firing candle)."""
    if not rules:
        return True, None
    df = frame_from_rows(rows) if rows else pd.DataFrame()
    passed, failed, skipped = [], [], []
    for name, tree in rules:
        outcome, reason = evaluate_rule(tree, df, context)
        {PASSED: passed, FAILED: failed, SKIPPED: skipped}[outcome].append((name, reason))

    notes = []
    if skipped:
        notes.append("rule skipped: " + "; ".join(f"{n} ({r})" for n, r in skipped))
    if combine_mode == "any":
        allowed = bool(passed) or not failed
    else:
        allowed = not failed
    if not allowed:
        notes.insert(0, "rules failed: " + ", ".join(n for n, _ in failed))
    return allowed, "; ".join(notes) or None
