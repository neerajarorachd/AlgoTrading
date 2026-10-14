"""Generic arithmetic condition evaluator for Strategy conditions —
explicit instruction, 2026-09-18: "we will not write a function for each
equation. make it smart. single arithmatic function." One formula string
(e.g. "VWAP - 1", "CLOSE[-1] - HIGH[-1] < VWAP", "RSI >= 45") is parsed
ONCE via Python's own `ast` module (restricted to a safe arithmetic/
comparison subset — no calls, no attribute access, no imports) and
evaluated against a per-candle indicator DataFrame, instead of writing a
bespoke Python function per condition shape the way
backend/scripts/order_backtest.py's SLTargetConfig/FORMATION_LEVEL_FUNCS
does for pattern geometry.

`Field` is the SAME enum used on both ends, per explicit instruction
("Enum should represent at both end, in designing strategy and while
executing the equation, both"): the Strategy-builder UI lists `Field`
members as the pickable keywords, and this module's evaluator reads the
identical enum values as DataFrame column names — no separate string
catalog to keep in sync.

Candle offset: `FIELD` alone means the current (most recent closed) candle,
`FIELD[-1]` means one candle back ("last-1"), `FIELD[-2]` "last-2", etc. —
implemented as `df[field].shift(-offset)` at read time (shift by the
POSITIVE offset since more-negative-in-formula means further back in the
chronologically-ascending DataFrame), directly matching the "get_df[VWAP-1]"
framing from the request. Offsets never leak across a trading-day boundary
(explicit instruction, 2026-09-18: "this is an intraday strategy... VWAP is
applicable only with intraday") -- build_indicator_dataframe() always adds
TRADING_DATE_COLUMN (the candle's own IST calendar date), and _series_for
group-shifts by it, so a market-open candle's FIELD[-1] is NaN rather than
reaching into the prior session's last candle.

`slope(FIELD, N)` — `(FIELD - FIELD[-N]) / N` — explicit instruction,
2026-09-18: "add slopes also." `mean(FIELD, N)` — the rolling average of
FIELD over the last N candles (including the current one), day-boundary-
safe like every other offset in this module — added the same day for
volume-based liquidity conditions ("mean volume[last 5 candles] > order
size"). Any other function call is rejected; this stays a closed, safe
grammar, not general Python.

`context`: an optional dict of named numeric constants (e.g.
{"order_size": 5000}) a formula can reference like any other bare name —
resolved AFTER Field/ALIASES, so a context key can never shadow a real
field. Lets a formula compare against a value that isn't itself a candle
series, e.g. a Strategy's own max_vol_per_call ("mean(volume, 5) >
order_size") — the caller decides what "order_size" resolves to; this
module only plumbs the name through.

This is the concrete module `backend/Phase1_LLD.md` already named
(`condition_evaluator.py`/`operand_resolvers.py`) but never built until now
— see [[condition_dsl_event_hooks_plan]].
"""
from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import pandas as pd

IST_OFFSET = timedelta(hours=5, minutes=30)


class Field(str, Enum):
    """Every keyword usable in a condition formula — the single source of
    truth for both the Strategy-builder UI's field picker and this
    module's own evaluator. Values are the exact DataFrame column names
    build_indicator_dataframe() produces, so `Field.RSI.value` is always
    a valid `chart[...]` lookup with no separate mapping step.

    A few short trader-shorthand aliases (ALIASES below) resolve to the
    same canonical member at parse time — explicit instruction: "all
    keywords should be available like sk, macd line (enum macd_line)"."""

    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"
    RSI = "rsi"
    MACD_LINE = "macd_line"
    MACD_SIGNAL = "macd_signal"
    STOCH_K = "stoch_k"
    STOCH_D = "stoch_d"
    VWAP = "vwap"
    MA21 = "ma21"
    MA50 = "ma50"
    ATR = "atr"
    BB_UPPER = "bb_upper"
    BB_MIDDLE = "bb_middle"
    BB_LOWER = "bb_lower"
    # EMA overlays (stored per candle in candle_indicators; added ALGO-30)
    EMA5 = "ema5"
    EMA14 = "ema14"
    EMA21 = "ema21"
    EMA50 = "ema50"


# Derived fields (ALGO-30): computed from the stored ones on demand, so they
# need no column of their own -- usable anywhere a Field is. Not Field
# members because nothing stores them; frame builders never fill them.
DERIVED_FIELDS = {
    "macd_hist": lambda df: df["macd_line"] - df["macd_signal"],
    # band width as a % of the middle band
    "bb_width": lambda df: (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"] * 100,
    "body": lambda df: (df["close"] - df["open"]).abs(),
    "upper_wick": lambda df: df["high"] - df[["open", "close"]].max(axis=1),
    "lower_wick": lambda df: df[["open", "close"]].min(axis=1) - df["low"],
    "range": lambda df: df["high"] - df["low"],
}
DERIVED_DESCRIPTIONS = {
    "macd_hist": "MACD line minus signal",
    "bb_width": "(BB_UPPER - BB_LOWER) / BB_MIDDLE x 100, in %",
    "body": "|CLOSE - OPEN|",
    "upper_wick": "HIGH - max(OPEN, CLOSE)",
    "lower_wick": "min(OPEN, CLOSE) - LOW",
    "range": "HIGH - LOW",
}

# Raw events fired on a candle (hammer, macd_bullish_cross, bb_squeeze...),
# as a set per row -- read by EVENT(name). Frame builders fill it when asked.
EVENTS_COLUMN = "_events"


# short trader shorthand -> canonical Field value, resolved before parsing.
# "ltp" ("last traded price") isn't its own Field member -- at candle
# granularity it's the same series as CLOSE, so it's an alias here rather
# than a second enum member with a duplicate value (which Python's Enum
# would just silently treat as another name for CLOSE anyway).
ALIASES = {
    "sk": Field.STOCH_K.value,
    "sd": Field.STOCH_D.value,
    "o": Field.OPEN.value,
    "h": Field.HIGH.value,
    "l": Field.LOW.value,
    "c": Field.CLOSE.value,
    "ltp": Field.CLOSE.value,
}

_FIELD_VALUES = {f.value for f in Field}

_ALLOWED_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
}
_ALLOWED_COMPARES = {
    ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
}
_ALLOWED_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}


class FormulaError(ValueError):
    """A formula string isn't a valid restricted arithmetic/comparison
    expression, or references a field outside Field/ALIASES."""


def _resolve_field_token(name: str) -> str:
    lowered = name.lower()
    if lowered in _FIELD_VALUES or lowered in DERIVED_FIELDS:
        return lowered
    if lowered in ALIASES:
        return ALIASES[lowered]
    raise FormulaError(f"unknown field {name!r} -- must be one of {sorted(_FIELD_VALUES | set(DERIVED_FIELDS) | set(ALIASES))}")


@dataclass(frozen=True)
class _Column:
    """A resolved FIELD or FIELD[-N] reference -- name is always a
    canonical Field value, offset is 0 (current candle) or a positive int
    (candles back)."""
    name: str
    offset: int


TRADING_DATE_COLUMN = "_trading_date"


def _series_for(df: pd.DataFrame, column: _Column) -> pd.Series:
    """FIELD[-N] shifts WITHIN the same trading day only when the frame
    carries TRADING_DATE_COLUMN (build_indicator_dataframe always adds
    it) -- explicit instruction, 2026-09-18: "this is an intraday
    strategy... VWAP is applicable only with intraday", i.e. an offset
    must never read yesterday's close into today's first candle's
    condition. Plain (non-grouped) shift is kept as the fallback for
    dataframes that don't carry that column (e.g. this module's own
    tests, which use small synthetic single-day frames where it makes no
    difference) rather than making it a hard requirement everywhere."""
    if column.name in DERIVED_FIELDS and column.name not in df.columns:
        # computed fresh every time, never cached on the caller's frame: a
        # cached column goes stale when the frame is copied and changed
        # (found by the MACD-departure test, 2026-10-14)
        try:
            series = DERIVED_FIELDS[column.name](df)
        except KeyError as exc:
            raise FormulaError(f"field {column.name!r} needs {exc} in this run's indicator dataframe") from exc
    elif column.name not in df.columns:
        raise FormulaError(f"field {column.name!r} has no data in this run's indicator dataframe")
    else:
        series = df[column.name]
    if not column.offset:
        return series
    if TRADING_DATE_COLUMN in df.columns:
        return series.groupby(df[TRADING_DATE_COLUMN]).shift(column.offset)
    return series.shift(column.offset)


def _parse_int_literal(node: ast.AST, context: str) -> int:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return -int(node.operand.value)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return int(node.value)
    raise FormulaError(f"{context} must be a plain integer, e.g. -1 or 3")


def _rolling_mean(df: pd.DataFrame, column: _Column, n: int) -> pd.Series:
    """mean(FIELD, N): the rolling average of FIELD over the last N candles
    (including the current one), day-boundary-safe the same way offsets
    are -- grouped by TRADING_DATE_COLUMN when present so a new session's
    first few candles never average in the prior day's closing values."""
    series = _series_for(df, _Column(column.name, 0))
    if TRADING_DATE_COLUMN in df.columns:
        return series.groupby(df[TRADING_DATE_COLUMN]).transform(lambda s: s.rolling(n, min_periods=n).mean())
    return series.rolling(n, min_periods=n).mean()


# ---- ALGO-30: time words, crossovers, events (all day-boundary-safe) ----

DEFAULT_RECENTLY_CANDLES = 10  # RECENTLY(cond)'s n when the caller's context gives no "lookback"


def _day_key(df: pd.DataFrame):
    return df[TRADING_DATE_COLUMN] if TRADING_DATE_COLUMN in df.columns else None


def _as_series(value, df: pd.DataFrame) -> pd.Series:
    return value if isinstance(value, pd.Series) else pd.Series(value, index=df.index)


def _as_bool(value, df: pd.DataFrame, where: str) -> pd.Series:
    """A condition's truth per candle; unknown (NaN) counts as not met."""
    s = _as_series(value, df)
    if s.dtype == bool:
        return s
    if s.dtype == object:
        return s.fillna(False).astype(bool)
    raise FormulaError(f"{where} needs a condition (a comparison), not a number")


def _shift(series: pd.Series, df: pd.DataFrame, n: int) -> pd.Series:
    key = _day_key(df)
    return series.groupby(key).shift(n) if key is not None else series.shift(n)


def _rolling(series: pd.Series, df: pd.DataFrame, n: int, how: str, min_periods: int) -> pd.Series:
    def roll(s):
        return getattr(s.astype(float).rolling(n, min_periods=min_periods), how)()
    key = _day_key(df)
    return series.groupby(key).transform(roll) if key is not None else roll(series)


def _bars_since(cond: pd.Series, df: pd.DataFrame) -> pd.Series:
    """Candles since `cond` was last true (0 = now); NaN if not yet today."""
    def since(s):
        pos = pd.Series(range(len(s)), index=s.index, dtype=float)
        return pos - pos.where(s.astype(bool)).ffill()
    key = _day_key(df)
    return cond.groupby(key).transform(since) if key is not None else since(cond)


def _arity(node: ast.Call, name: str, allowed) -> list:
    if node.keywords or len(node.args) not in allowed:
        counts = " or ".join(str(n) for n in sorted(allowed))
        raise FormulaError(f"{name.upper()}(...) takes {counts} argument(s)")
    return node.args


def _positive_int(node: ast.AST, what: str) -> int:
    n = _parse_int_literal(node, what)
    if n <= 0:
        raise FormulaError(f"{what} must be a positive integer")
    return n


def _eval_call(node: ast.Call, name: str, df: pd.DataFrame, context: Optional[dict]):
    """The whitelisted functions -- still one evaluator, just known names."""
    ev = lambda n: _eval_node(n, df, context)  # noqa: E731
    if name in ("slope", "mean"):
        field_node, n_node = _arity(node, name, {2})
        if not isinstance(field_node, ast.Name):
            raise FormulaError(f"{name}(...)'s first argument must be a bare field name, e.g. {name}(rsi, 3)")
        n = _positive_int(n_node, f"{name}(...)'s second argument (candle lookback)")
        field_name = _resolve_field_token(field_node.id)
        if name == "mean":
            return _rolling_mean(df, _Column(field_name, 0), n)
        return (_series_for(df, _Column(field_name, 0)) - _series_for(df, _Column(field_name, n))) / n
    if name in ("highest", "lowest"):
        expr, n_node = _arity(node, name, {2})
        n = _positive_int(n_node, f"{name.upper()}(...)'s candle count")
        return _rolling(_as_series(ev(expr), df), df, n, "max" if name == "highest" else "min", n)
    if name in ("crossed_up", "crossed_down"):
        a_node, b_node = _arity(node, name, {2})
        a, b = _as_series(ev(a_node), df), _as_series(ev(b_node), df)
        a1, b1 = _shift(a, df, 1), _shift(b, df, 1)
        return (a > b) & (a1 <= b1) if name == "crossed_up" else (a < b) & (a1 >= b1)
    if name == "event":
        (arg,) = _arity(node, name, {1})
        if not isinstance(arg, ast.Name):
            raise FormulaError("EVENT(...) takes an event name, e.g. EVENT(hammer)")
        if EVENTS_COLUMN not in df.columns:
            raise FormulaError("EVENT(...) needs the candles' events, which this run did not load")
        event = arg.id.lower()
        return df[EVENTS_COLUMN].apply(lambda s: bool(s) and event in s).astype(bool)
    if name == "recently":
        args = _arity(node, name, {1, 2})
        default = (context or {}).get("lookback", DEFAULT_RECENTLY_CANDLES)
        n = _positive_int(args[1], "RECENTLY(...)'s candle count") if len(args) == 2 else int(default)
        cond = _as_bool(ev(args[0]), df, "RECENTLY(...)")
        return _rolling(cond, df, n, "max", 1) > 0
    if name == "for":
        cond_node, n_node = _arity(node, name, {2})
        n = _positive_int(n_node, "FOR(...)'s candle count")
        return _rolling(_as_bool(ev(cond_node), df, "FOR(...)"), df, n, "min", n) > 0
    if name == "count":
        cond_node, n_node = _arity(node, name, {2})
        n = _positive_int(n_node, "COUNT(...)'s candle count")
        return _rolling(_as_bool(ev(cond_node), df, "COUNT(...)"), df, n, "sum", 1)
    if name == "bars_since":
        (cond_node,) = _arity(node, name, {1})
        return _bars_since(_as_bool(ev(cond_node), df, "BARS_SINCE(...)"), df)
    if name == "then":
        a_node, b_node, n_node = _arity(node, name, {3})
        n = _positive_int(n_node, "THEN(...)'s candle count")
        a = _as_bool(ev(a_node), df, "THEN(...)'s first condition")
        b = _as_bool(ev(b_node), df, "THEN(...)'s second condition")
        a_before = _rolling(_shift(a.astype(float), df, 1), df, n, "max", 1) > 0  # a in the n candles before now
        return b & a_before
    raise FormulaError(f"unknown function {name!r} -- allowed: {', '.join(f['name'] for f in FUNCTIONS)}")


_KEYWORD_FIXES = (
    # trader-friendly spellings -> what the restricted grammar parses
    (re.compile(r"\bAND\b", re.IGNORECASE), "and"),
    (re.compile(r"\bOR\b", re.IGNORECASE), "or"),
    (re.compile(r"\bNOT\b", re.IGNORECASE), "not"),
    (re.compile(r"\bfor\s*\(", re.IGNORECASE), "FOR("),  # `for` is a Python keyword
)


def _normalize(formula: str) -> str:
    for pattern, replacement in _KEYWORD_FIXES:
        formula = pattern.sub(replacement, formula)
    return formula


def _eval_node(node: ast.AST, df: pd.DataFrame, context: Optional[dict] = None):
    """Returns either a pandas Series (a field reference or an arithmetic
    combination of them) or a plain float (a numeric literal) -- the
    caller combines/compares whichever mix shows up, pandas broadcasts a
    scalar against a Series automatically either way.

    context: optional {name: number} of caller-provided constants, checked
    AFTER Field/ALIASES so a context key can never shadow a real field."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, df, context)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise FormulaError(f"only numeric literals are allowed, got {node.value!r}")
        return float(node.value)
    if isinstance(node, ast.Name):
        token = node.id.lower()
        if token in _FIELD_VALUES or token in ALIASES or token in DERIVED_FIELDS:
            return _series_for(df, _Column(_resolve_field_token(node.id), 0))
        if context is not None and token in context:
            return float(context[token])
        known = sorted(_FIELD_VALUES | set(ALIASES) | set(context or {}))
        raise FormulaError(f"unknown field {node.id!r} -- must be one of {known}")
    if isinstance(node, ast.Subscript):
        if not isinstance(node.value, ast.Name):
            raise FormulaError("only FIELD[-N] subscripting is allowed")
        offset = _parse_int_literal(node.slice, "FIELD[...]'s offset")
        if offset > 0:
            raise FormulaError("candle offsets must be 0 or negative (0/absent = current candle, -1 = last-1, ...)")
        return _series_for(df, _Column(_resolve_field_token(node.value.id), -offset))
    if isinstance(node, ast.Call):
        func_name = node.func.id.lower() if isinstance(node.func, ast.Name) else None
        if func_name not in _FUNCTION_NAMES:
            raise FormulaError(f"unknown function {func_name!r} -- allowed: {', '.join(sorted(_FUNCTION_NAMES))}")
        return _eval_call(node, func_name, df, context)
    if isinstance(node, ast.BoolOp):
        # `and` / `or` inside one formula (ALGO-30), e.g. inside RECENTLY(...)
        parts = [_as_bool(_eval_node(v, df, context), df, "and/or") for v in node.values]
        result = parts[0]
        for p in parts[1:]:
            result = (result & p) if isinstance(node.op, ast.And) else (result | p)
        return result
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return ~_as_bool(_eval_node(node.operand, df, context), df, "not")
    if isinstance(node, ast.BinOp):
        op = _ALLOWED_BINOPS.get(type(node.op))
        if op is None:
            raise FormulaError(f"operator {type(node.op).__name__} is not allowed")
        return op(_eval_node(node.left, df, context), _eval_node(node.right, df, context))
    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_UNARY.get(type(node.op))
        if op is None:
            raise FormulaError(f"unary operator {type(node.op).__name__} is not allowed")
        return op(_eval_node(node.operand, df, context))
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise FormulaError("chained comparisons (a < b < c) are not supported -- write two conditions instead")
        op = _ALLOWED_COMPARES.get(type(node.ops[0]))
        if op is None:
            raise FormulaError(f"comparison {type(node.ops[0]).__name__} is not allowed")
        return op(_eval_node(node.left, df, context), _eval_node(node.comparators[0], df, context))
    raise FormulaError(f"{type(node).__name__} is not allowed in a condition formula")


def parse_and_evaluate(formula: str, df: pd.DataFrame, context: Optional[dict] = None) -> pd.Series:
    """The one arithmetic function every formula runs through -- no
    per-shape functions. `formula` is a restricted Python expression
    (FIELD/FIELD[-N] names, +-*/ , one comparison, numeric literals only
    -- no calls, no attribute access, no boolean/chained comparisons).
    Returns a pandas Series aligned to `df`'s own index: a float series
    for a pure arithmetic formula (e.g. "VWAP - CLOSE[-1]"), a bool
    series for one with a comparison (e.g. "RSI >= 45").

    context: optional {name: number} of caller-provided constants a
    formula can reference by name (e.g. {"order_size": 5000}) -- see this
    module's own docstring."""
    try:
        tree = ast.parse(_normalize(formula), mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"could not parse formula {formula!r}: {exc}") from exc
    result = _eval_node(tree, df, context)
    if isinstance(result, (int, float)):
        # a formula that's just a bare literal -- broadcast to the frame's shape
        return pd.Series(result, index=df.index)
    return result


def evaluate_tree(tree: dict, df: pd.DataFrame, context: Optional[dict] = None) -> pd.Series:
    """Recursively evaluates a full Strategy condition tree (the same
    dict shape LibStrategies.get_tree/create/replace_tree already use --
    {"operator": "AND"|"OR", "conditions": [...], "groups": [...]})
    against `df`, returning one bool Series aligned to df's index: True
    on every candle where the WHOLE tree is satisfied.

    This is the first real evaluator for that tree -- LibStrategies.py's
    own docstring has always said "this module only persists/reconstructs
    a strategy's DEFINITION... NOT evaluate one against real data". Only
    the FORMULA leaf shape (left_formula set) is handled here; an EVENT
    leaf (a bare pattern name like "double_top", no left_formula) needs
    real pattern-activity data this function doesn't have access to and
    raises clearly rather than silently evaluating it wrong."""
    if not tree.get("conditions") and not tree.get("groups"):
        raise FormulaError("an empty condition group has nothing to evaluate")

    parts = []
    for cond in tree.get("conditions", []):
        if not cond.get("left_formula"):
            raise FormulaError(
                f"condition {cond!r} has no left_formula -- event-pattern leaves "
                "(element_code naming a fired pattern, e.g. 'double_top') aren't "
                "supported by this evaluator yet, only formula-based numeric leaves are"
            )
        parts.append(evaluate_condition(
            cond["left_formula"], cond.get("operator"), cond.get("right_formula"), df, context))
    for group in tree.get("groups", []):
        parts.append(evaluate_tree(group, df, context))

    operator_name = tree["operator"]
    if operator_name == "AND":
        result = parts[0]
        for p in parts[1:]:
            result = result & p
        return result
    if operator_name == "OR":
        result = parts[0]
        for p in parts[1:]:
            result = result | p
        return result
    raise FormulaError(f"unknown group operator {operator_name!r} -- must be 'AND' or 'OR'")


def evaluate_condition(left_formula: str, operator_symbol: Optional[str], right_formula: Optional[str],
                        df: pd.DataFrame, context: Optional[dict] = None) -> pd.Series:
    """Full condition evaluation: `left_formula` alone (e.g. a formula
    that's already a comparison, "RSI >= 45") OR `left_formula` combined
    with `operator_symbol`/`right_formula` (e.g. left="VWAP - CLOSE[-1]",
    operator=">", right="0.5") -- matches StrategyCondition's existing
    operator/compare_type shape, just with a formula on either side
    instead of a single element_code. `right_formula` may itself be a
    number as a string (a static comparison) or another field formula."""
    left = parse_and_evaluate(left_formula, df, context)
    if operator_symbol is None:
        if left.dtype != bool:
            raise FormulaError("a condition with no operator must itself be a boolean formula (contain a comparison)")
        return left
    ops = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne}
    if operator_symbol not in ops:
        raise FormulaError(f"unknown operator {operator_symbol!r} -- must be one of {sorted(ops)}")
    right = parse_and_evaluate(right_formula, df, context)
    return ops[operator_symbol](left, right)


FUNCTIONS = (
    {"name": "slope", "signature": "slope(FIELD, N)", "description": "(FIELD - FIELD[-N]) / N"},
    {"name": "mean", "signature": "mean(FIELD, N)", "description": "average of FIELD over the last N candles"},
    # ALGO-30 -- see docs/condition_ready_reckoner.md
    {"name": "highest", "signature": "HIGHEST(X, N)", "description": "highest X over the last N candles"},
    {"name": "lowest", "signature": "LOWEST(X, N)", "description": "lowest X over the last N candles"},
    {"name": "crossed_up", "signature": "CROSSED_UP(A, B)", "description": "A crossed above B on this candle"},
    {"name": "crossed_down", "signature": "CROSSED_DOWN(A, B)", "description": "A crossed below B on this candle"},
    {"name": "event", "signature": "EVENT(name)", "description": "a raw event fired on this candle, e.g. EVENT(hammer)"},
    {"name": "recently", "signature": "RECENTLY(cond[, N])", "description": "cond true at least once in the last N candles (N = the pattern's lookback, default 10)"},
    {"name": "for", "signature": "FOR(cond, N)", "description": "cond true on every one of the last N candles"},
    {"name": "count", "signature": "COUNT(cond, N)", "description": "how many of the last N candles cond was true on"},
    {"name": "bars_since", "signature": "BARS_SINCE(cond)", "description": "candles since cond was last true (0 = now)"},
    {"name": "then", "signature": "THEN(A, B, N)", "description": "B true now, and A was true within the N candles before"},
)
_FUNCTION_NAMES = {f["name"] for f in FUNCTIONS}
# Named constants a caller may pass as `context`; the Strategy designer only
# knows these names, so validate_condition accepts exactly them.
KNOWN_CONTEXT_NAMES = ("order_size",)


def describe_grammar() -> dict:
    """The formula grammar as data -- served to the Strategy designer UI so
    its field/function pickers can never drift from what the evaluator
    actually accepts (both read Field/ALIASES/FUNCTIONS below)."""
    aliases_by_field: dict = {}
    for alias, canonical in ALIASES.items():
        aliases_by_field.setdefault(canonical, []).append(alias)
    return {
        "fields": [{"value": f.value, "aliases": sorted(aliases_by_field.get(f.value, []))} for f in Field],
        "derived_fields": [{"value": k, "description": DERIVED_DESCRIPTIONS[k]} for k in DERIVED_FIELDS],
        "functions": list(FUNCTIONS),
        "constants": list(KNOWN_CONTEXT_NAMES),
    }


def validate_condition(left_formula: str, operator_symbol: Optional[str], right_formula: Optional[str],
                        context_names=KNOWN_CONTEXT_NAMES) -> Optional[str]:
    """Returns an error message, or None if the condition is well-formed.
    Dry-runs the real evaluator against a tiny synthetic frame, so the
    accepted grammar is by construction exactly what evaluation accepts."""
    if not left_formula:
        return "left_formula is required"
    if operator_symbol is not None and not right_formula:
        return "right_formula is required when an operator is given"
    columns = {f.value: [1.0, 2.0, 3.0, 4.0] for f in Field}
    columns[TRADING_DATE_COLUMN] = [0, 0, 0, 0]
    columns[EVENTS_COLUMN] = [frozenset()] * 4
    try:
        evaluate_condition(left_formula, operator_symbol, right_formula, pd.DataFrame(columns),
                           {name: 1.0 for name in context_names})
    except FormulaError as exc:
        return str(exc)
    return None


def required_lookback(formula: str, default_recently: int = DEFAULT_RECENTLY_CANDLES) -> int:
    """How many candles (including the current one) a formula reaches back:
    FIELD[-N] needs N+1, slope(F, N) needs N+1, mean(F, N) needs N, and the
    ALGO-30 wrappers add their window to what they wrap (RECENTLY(CROSSED_UP
    (a, b), 10) needs 11). BARS_SINCE looks back without limit -- it asks for
    a full session. Used to size the live frame. Unparseable formulas return
    1 (their real error surfaces at validation/evaluation, not here)."""
    try:
        tree = ast.parse(_normalize(formula), mode="eval")
    except SyntaxError:
        return 1

    def depth(node) -> int:
        try:
            if isinstance(node, ast.Subscript):
                return abs(_parse_int_literal(node.slice, "offset")) + 1
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                name, args = node.func.id.lower(), node.args
                inner = max((depth(a) for a in args), default=1)
                if name in ("slope", "mean") and len(args) == 2:
                    n = _parse_int_literal(args[1], "lookback")
                    return n if name == "mean" else n + 1
                if name in ("highest", "lowest", "for", "count") and len(args) == 2:
                    return _parse_int_literal(args[1], "lookback") + depth(args[0]) - 1
                if name == "recently":
                    n = _parse_int_literal(args[1], "lookback") if len(args) == 2 else default_recently
                    return n + depth(args[0]) - 1
                if name == "then" and len(args) == 3:
                    return max(depth(args[1]), _parse_int_literal(args[2], "lookback") + depth(args[0]))
                if name in ("crossed_up", "crossed_down"):
                    return inner + 1
                if name == "bars_since":
                    return SESSION_CANDLES
                return inner
        except FormulaError:
            return 1
        return max((depth(c) for c in ast.iter_child_nodes(node)), default=1)

    return depth(tree)


SESSION_CANDLES = 375  # 09:15-15:30 at 1-min -- BARS_SINCE's "whole session"


def _operand_nodes(node: ast.AST):
    if isinstance(node, (ast.Name, ast.Subscript, ast.Call)):
        yield node
        return
    for child in ast.iter_child_nodes(node):
        yield from _operand_nodes(child)


def formula_operands_available(formula: str, df: pd.DataFrame, context: Optional[dict] = None) -> bool:
    """False when any field/offset/function operand the formula reads is NaN
    on the frame's LAST row -- i.e. today's data isn't there yet (start of
    session, or an indicator still warming up). A comparison against NaN
    evaluates to plain False in pandas, so this is the only way to tell
    "false" from "unknown". Constants and literals are always available."""
    tree = ast.parse(_normalize(formula), mode="eval")
    for node in _operand_nodes(tree):
        value = _eval_node(node, df, context)
        if isinstance(value, pd.Series) and pd.isna(value.iloc[-1]):
            return False
    return True


def frame_from_rows(rows: list) -> pd.DataFrame:
    """Builds the same frame shape build_indicator_dataframe produces, from
    in-memory rows (dicts with ts + the Field values) -- the live path,
    where today's candles/indicators exist only in the engine's memory until
    the scheduled flush. Rows must be chronological."""
    records = []
    for r in rows:
        ts = r["ts"] if r["ts"].tzinfo is not None else r["ts"].replace(tzinfo=timezone.utc)
        rec = {"ts": ts, TRADING_DATE_COLUMN: (ts + IST_OFFSET).date()}
        for f in Field:
            rec[f.value] = r.get(f.value)
        if EVENTS_COLUMN in r:  # the candle's raw events, when the caller has them (pattern evaluation)
            rec[EVENTS_COLUMN] = frozenset(r[EVENTS_COLUMN])
        records.append(rec)
    df = pd.DataFrame.from_records(records)
    for f in Field:
        df[f.value] = pd.to_numeric(df[f.value], errors="coerce")
    return df


def build_indicator_dataframe(session, symbol: str, exchange_segment: str, timeframe: str,
                               start: datetime, end: datetime, include_events: bool = False) -> pd.DataFrame:
    """Loads real OHLC (candles_historical) + real indicator values
    (candle_indicators) for one symbol/timeframe/date range, joined into
    ONE chronologically-ascending pandas DataFrame whose columns are
    exactly the Field enum's own values -- the concrete "chart[...]" this
    module's docstring describes, built from already-validated data
    (the same CandleHistorical/CandleIndicators tables every other real-
    money script in this project's HINDCOPPER investigation reads from),
    not recomputed indicators of its own.

    Missing indicator rows (e.g. the ATR/MACD/MA50 warm-up period) come
    through as NaN, same "None until ready" convention CandleIndicators
    itself already uses -- a formula referencing one just evaluates to
    NaN/False on those early candles rather than raising."""
    from db.models import CandleIndicators, SubscribedSymbol
    from db.ops import LibCandlesHistorical

    row = session.query(SubscribedSymbol).filter_by(symbol=symbol, exchange_segment=exchange_segment).one()
    candles = LibCandlesHistorical.get_range(session, symbol, exchange_segment, timeframe, start, end)
    indicator_rows = session.query(CandleIndicators).filter_by(instrument_id=row.id, timeframe=timeframe).all()

    def _as_utc(ts):
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)

    indicators_by_ts = {_as_utc(r.ts): r for r in indicator_rows}

    records = []
    for c in candles:
        ts = _as_utc(c.ts)
        ind = indicators_by_ts.get(ts)
        records.append({
            "ts": ts,
            # IST trading date -- FIELD[-N] offsets group-shift within
            # this, never reaching into a prior session (see
            # _series_for's own docstring).
            TRADING_DATE_COLUMN: (ts + IST_OFFSET).date(),
            Field.OPEN.value: float(c.open_price), Field.HIGH.value: float(c.high_price),
            Field.LOW.value: float(c.low_price), Field.CLOSE.value: float(c.close_price),
            Field.VOLUME.value: c.volume,
            Field.RSI.value: float(ind.rsi) if ind and ind.rsi is not None else None,
            Field.MACD_LINE.value: float(ind.macd_line) if ind and ind.macd_line is not None else None,
            Field.MACD_SIGNAL.value: float(ind.macd_signal) if ind and ind.macd_signal is not None else None,
            Field.STOCH_K.value: float(ind.stoch_k) if ind and ind.stoch_k is not None else None,
            Field.STOCH_D.value: float(ind.stoch_d) if ind and ind.stoch_d is not None else None,
            Field.VWAP.value: float(ind.vwap) if ind and ind.vwap is not None else None,
            Field.MA21.value: float(ind.ma21) if ind and ind.ma21 is not None else None,
            Field.MA50.value: float(ind.ma50) if ind and ind.ma50 is not None else None,
            Field.ATR.value: float(ind.atr) if ind and ind.atr is not None else None,
            Field.BB_UPPER.value: float(ind.bb_upper) if ind and ind.bb_upper is not None else None,
            Field.BB_MIDDLE.value: float(ind.bb_middle) if ind and ind.bb_middle is not None else None,
            Field.BB_LOWER.value: float(ind.bb_lower) if ind and ind.bb_lower is not None else None,
            **{f.value: (float(getattr(ind, f.value)) if ind and getattr(ind, f.value, None) is not None else None)
               for f in (Field.EMA5, Field.EMA14, Field.EMA21, Field.EMA50)},
        })
    df = pd.DataFrame.from_records(records)
    if include_events and not df.empty:
        # EVENT(name) reads the candle's raw events (instrument_activity)
        from db.models import InstrumentActivity
        events: dict = {}
        for ts, activity in session.query(InstrumentActivity.ts, InstrumentActivity.activity).filter(
                InstrumentActivity.instrument_id == row.id, InstrumentActivity.timeframe == timeframe,
                InstrumentActivity.ts >= start, InstrumentActivity.ts <= end):
            events.setdefault(_as_utc(ts), set()).add(activity)
        df[EVENTS_COLUMN] = df["ts"].map(lambda t: frozenset(events.get(t, ())))
    return df.sort_values("ts").reset_index(drop=True)
