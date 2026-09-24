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
    if lowered in _FIELD_VALUES:
        return lowered
    if lowered in ALIASES:
        return ALIASES[lowered]
    raise FormulaError(f"unknown field {name!r} -- must be one of {sorted(_FIELD_VALUES | set(ALIASES))}")


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
    if column.name not in df.columns:
        raise FormulaError(f"field {column.name!r} has no data in this run's indicator dataframe")
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
        if token in _FIELD_VALUES or token in ALIASES:
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
        # the two whitelisted built-ins -- still the same single evaluator,
        # just recognized function names rather than opening up arbitrary calls.
        func_name = node.func.id.lower() if isinstance(node.func, ast.Name) else None
        if func_name not in ("slope", "mean"):
            raise FormulaError("only the built-in slope(FIELD, N) / mean(FIELD, N) functions "
                                "are allowed in a condition formula")
        if len(node.args) != 2 or node.keywords:
            raise FormulaError(f"{func_name}(FIELD, N) takes exactly 2 positional arguments")
        field_node, n_node = node.args
        if not isinstance(field_node, ast.Name):
            raise FormulaError(f"{func_name}(...)'s first argument must be a bare field name, "
                                f"e.g. {func_name}(rsi, 3)")
        n = _parse_int_literal(n_node, f"{func_name}(...)'s second argument (candle lookback)")
        if n <= 0:
            raise FormulaError(f"{func_name}(...)'s second argument (candle lookback) must be a positive integer")
        field_name = _resolve_field_token(field_node.id)
        if func_name == "mean":
            return _rolling_mean(df, _Column(field_name, 0), n)
        current = _series_for(df, _Column(field_name, 0))
        past = _series_for(df, _Column(field_name, n))
        return (current - past) / n
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
        tree = ast.parse(formula, mode="eval")
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
)
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
    try:
        evaluate_condition(left_formula, operator_symbol, right_formula, pd.DataFrame(columns),
                           {name: 1.0 for name in context_names})
    except FormulaError as exc:
        return str(exc)
    return None


def required_lookback(formula: str) -> int:
    """How many candles (including the current one) a formula reaches back:
    FIELD[-N] needs N+1, slope(F, N) needs N+1, mean(F, N) needs N. Used to
    size the live single-candle frame. Unparseable formulas return 1 (their
    real error surfaces at validation/evaluation, not here)."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError:
        return 1
    needed = 1
    for node in ast.walk(tree):
        try:
            if isinstance(node, ast.Subscript):
                needed = max(needed, abs(_parse_int_literal(node.slice, "offset")) + 1)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 2:
                n = _parse_int_literal(node.args[1], "lookback")
                needed = max(needed, n if node.func.id.lower() == "mean" else n + 1)
        except FormulaError:
            continue
    return needed


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
    tree = ast.parse(formula, mode="eval")
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
        records.append(rec)
    df = pd.DataFrame.from_records(records)
    for f in Field:
        df[f.value] = pd.to_numeric(df[f.value], errors="coerce")
    return df


def build_indicator_dataframe(session, symbol: str, exchange_segment: str, timeframe: str,
                               start: datetime, end: datetime) -> pd.DataFrame:
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
        })
    df = pd.DataFrame.from_records(records)
    return df.sort_values("ts").reset_index(drop=True)
