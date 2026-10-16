"""Why a pattern or raw signal fired (2026-10-16): for a pattern hit, every
condition with what it requires and the actual value on that candle; for a
raw signal, every strength measure with its minimum and actual value.

Used live (PatternEvaluator / ActivityEngine record one entry per hit, read
by /api/review) and by scripts/explain_patterns_day.py (one-off replays).
`met` is authoritative -- it is the evaluator's own answer; `required` and
`actual` are a best-effort reading of the formula for people.
"""
from __future__ import annotations

import ast
from typing import Optional

import pandas as pd

from condition_evaluator import EVENTS_COLUMN, FormulaError, _eval_node, _normalize

OPS = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=", ast.Eq: "==", ast.NotEq: "!="}


def _at(value, i: int = -1):
    """One row's value as plain JSON: number, bool or None (unknown)."""
    v = value.iloc[i] if isinstance(value, pd.Series) else value
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, bool) or type(v).__name__ == "bool_":
        return bool(v)
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return str(v)


def _src(node, formula):
    return ast.get_source_segment(formula, node) or ast.unparse(node)


def explain_condition(formula: str, df: pd.DataFrame, context: dict) -> dict:
    """{formula, required, actual, met} on the frame's last row. An unknown
    value is reported as None and counts as not met."""
    norm = _normalize(formula)
    tree = ast.parse(norm, mode="eval").body
    out = {"formula": formula, "required": None, "actual": None, "met": False}
    try:
        out["met"] = bool(_at(_eval_node(tree, df, context)) or False)
    except FormulaError as exc:
        out["actual"] = f"n/a ({exc})"
        return out
    try:
        if isinstance(tree, ast.Compare) and len(tree.ops) == 1:
            right_node = tree.comparators[0]
            right = _at(_eval_node(right_node, df, context))
            out["actual"] = _at(_eval_node(tree.left, df, context))
            label = "" if isinstance(right_node, ast.Constant) else f" ({_src(right_node, norm)})"
            out["required"] = f"{OPS[type(tree.ops[0])]} {'unknown' if right is None else right}{label}"
        elif isinstance(tree, ast.Call) and getattr(tree.func, "id", "").lower() in ("recently", "for"):
            name = tree.func.id.lower()
            n = int(tree.args[1].value) if len(tree.args) > 1 else int(context.get("lookback", 10))
            series = _eval_node(tree.args[0], df, context)
            series = series if isinstance(series, pd.Series) else pd.Series([series] * len(df), index=df.index)
            flags = [bool(x) if not pd.isna(x) else False for x in series.tolist()]
            if name == "recently":
                ago = next((i for i, f in enumerate(reversed(flags)) if f), None)
                out["required"] = f"within last {n} candles"
                out["actual"] = f"none in the last {len(flags)} candles" if ago is None else ("this candle" if ago == 0 else f"{ago} candles ago")
            else:
                run = 0
                for f in reversed(flags):
                    if not f:
                        break
                    run += 1
                out["required"] = f"{n} candles in a row"
                out["actual"] = f"{run} in a row"
        elif isinstance(tree, ast.Call) and getattr(tree.func, "id", "").lower() == "event":
            events = df[EVENTS_COLUMN].iloc[-1] if EVENTS_COLUMN in df.columns else frozenset()
            code = _src(tree.args[0], norm)
            out["required"] = "on this candle, strong enough"
            out["actual"] = ("fired, strong enough" if out["met"] else "fired, too weak") if code in events else "did not fire"
        else:
            out["actual"] = out["met"]
    except Exception as exc:  # the reading is best-effort; `met` stays the evaluator's answer
        out["actual"] = f"? ({exc.__class__.__name__})"
    return out


def explain_pattern(formulas, df: pd.DataFrame, context: dict) -> list:
    return [explain_condition(f, df, context) for f in formulas]


def explain_signal(signal: str, df: pd.DataFrame, minimums: dict, measures: dict, index: int = -1) -> list:
    """Each strength measure of a raw signal on row `index`: its formula,
    minimum (None = no minimum set), actual value and whether it is met."""
    out = []
    for measure, formula in measures.items():
        try:
            value = _at(_eval_node(ast.parse(_normalize(formula), mode="eval").body, df, {}), index)
        except FormulaError:
            value = None
        minimum = minimums.get(measure)
        met = None if minimum is None else (value is not None and value >= minimum)
        out.append({"measure": measure, "formula": formula,
                    "required": None if minimum is None else f">= {round(float(minimum), 4)}",
                    "actual": value, "met": met})
    return out


def strength_ok(explained: list) -> Optional[bool]:
    """All set minimums met (None when the signal has no minimums at all)."""
    checked = [m["met"] for m in explained if m["met"] is not None]
    return None if not checked else all(checked)
