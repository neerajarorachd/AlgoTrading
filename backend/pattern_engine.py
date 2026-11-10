"""Live pattern evaluation (ALGO-32 / 5.3). On every candle close the activity
engine hands this the instrument's recent rows (OHLCV + indicators + the
candle's raw events); every enabled pattern is evaluated on them:

- match % = conditions met / conditions (each condition a formula in the
  shared language; EVENT() checks signal strength for this timeframe);
- FORMED when match % >= the formed threshold (setting, default 80%);
- a HIT is recorded once, on the candle where a pattern first forms
  (edge-triggered), and not again within its cooldown;
- the latest match % per pattern is kept per instrument/timeframe for the
  colours (5.4).

Everything is day-bounded like the condition language: state resets at each
new IST trading day. Hits become ordinary activity rows (activity_type
"pattern", activity = the pattern's key, intensity = weight x match %), so
the today-store, the 15-minute save, recovery, the chart markers and the
event lists all carry them with no extra plumbing.

Batch mode (2026-11-10): restart recovery, the gap-fill day re-run and paper
backtests hand a whole run of candles to evaluate_batch, which builds ONE
frame and evaluates every formula once over all rows, then applies the same
formed / cooldown / day-reset loop. Hits, match %, state and explanations are
identical to calling on_candle per candle: rows where a condition could see
something the live frame could not (an event written onto an earlier candle
later, e.g. swings; or a reach past the live window) are recomputed on the
exact per-candle frame. Setting pattern_batch_eval: 0 = off (per candle),
1 = on, 2 = on and also check every row against per-candle (logs mismatches,
uses the per-candle value)."""
from __future__ import annotations

import ast
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from condition_evaluator import (EVENTS_COLUMN, FormulaError, _eval_node, _normalize, frame_from_rows, required_lookback,
                                 timeframe_minutes)

logger = logging.getLogger(__name__)

IST = timedelta(hours=5, minutes=30)
RELOAD_SECONDS = 60  # pattern edits take effect at the next candle close after this


@dataclass
class _Compiled:
    id: int
    key: str
    pattern_type: str
    weight: float
    lookback: int
    cooldown: int
    trees: List[ast.AST]
    formulas: List[str]
    name: str = ""
    depths: List[int] = field(default_factory=list)  # candles each condition reaches back (incl. current)
    events: List[frozenset] = field(default_factory=list)  # event names each condition reads


def _event_names(tree: ast.AST) -> frozenset:
    """The event names an EVENT(...) / EVENT_ANY(...) inside a condition reads."""
    names = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id.lower() in ("event", "event_any")
                and n.args and isinstance(n.args[0], ast.Name)):
            names.add(n.args[0].id.lower())
    return frozenset(names)


LATE_EVENTS = "_late"  # row key: {event name: ts of the candle that wrote it} for events written onto an earlier row
MAX_FRAME_ROWS = 120  # the engine keeps this many rows per instrument/timeframe (ActivityEngine._frame_rows)
DAILY_FRAME_ROWS = 400  # daily mode: 52-week (250-day) lookbacks fit
DAILY_KEY = "all"  # daily mode: one continuous "session" -- state never resets by day


class PatternEvaluator:
    def __init__(self, session_factory, owner_id: Optional[int] = None, only_keys=None, batch_mode: Optional[int] = None,
                 explain: bool = True, daily: bool = False):
        self.session_factory = session_factory
        self.owner_id = owner_id
        # backtests evaluate only the patterns they trade; each pattern's state is
        # its own, so the others' absence changes nothing for these
        self.only_keys = frozenset(only_keys) if only_keys else None
        # keep each hit's "why it formed" (/api/review); backtests switch it off --
        # nothing there reads it, and it is half their run time (2026-11-10)
        self.explain = explain
        # daily mode (2026-11-10): one candle = one day; no reset per day, the
        # window is the last N candles (no "today + previous session" rule)
        self.daily = daily
        self.max_rows = DAILY_FRAME_ROWS if daily else MAX_FRAME_ROWS
        self.batch_mode = 1  # setting pattern_batch_eval, unless forced here (comparisons)
        self._forced_batch_mode = batch_mode
        self.patterns: List[_Compiled] = []
        self.threshold = 0.8
        self.strength: Dict[str, dict] = {}  # timeframe -> strength rules
        self.window = 1  # rows needed for every pattern's conditions
        self._loaded_at = 0.0
        self._state: Dict[tuple, dict] = {}  # (instrument key, pattern id) -> {formed, last_hit, day}
        self._index: Dict[tuple, int] = {}  # instrument key -> candles seen today
        self._day: Dict[tuple, object] = {}
        self.scores: Dict[tuple, Dict[str, float]] = {}  # instrument key -> {pattern key: match %}
        # instrument key -> today's hits, each with every condition's numbers
        # (hit_explain; read by /api/review, 2026-10-16)
        self.explanations: Dict[tuple, List[dict]] = {}
        self.reload()

    # ---- definitions ----

    def reload(self) -> None:
        from db.ops import LibPatterns, LibSignalStrength, LibSystemSettings
        from db.session import session_scope
        with session_scope(self.session_factory) as session:
            rows = LibPatterns.list_patterns(session, owner_id=self.owner_id, enabled_only=True)
            settings = LibSystemSettings.load_by_keys(session, ["pattern_formed_threshold", "pattern_batch_eval"])
            self.strength = {tf: LibSignalStrength.rules_for_timeframe(session, tf, self.owner_id) for tf in ("1min", "3min", "5min")}
        compiled = []
        for p in rows:
            if self.only_keys is not None and p["key"] not in self.only_keys:
                continue
            formulas = [c["formula"] for c in p["conditions"]]
            try:
                trees = [ast.parse(_normalize(f), mode="eval") for f in formulas]
            except SyntaxError:
                logger.warning("Pattern %s has a condition that does not parse; skipped", p["key"])
                continue
            compiled.append(_Compiled(p["id"], p["key"], p["pattern_type"], p["weight"], p["lookback"], p["cooldown"], trees, formulas, p["name"],
                                      depths=[required_lookback(f, p["lookback"]) for f in formulas],
                                      events=[_event_names(t) for t in trees]))
        self.patterns = compiled
        self.threshold = float(settings.get("pattern_formed_threshold", 0.8))
        self.batch_mode = int(settings.get("pattern_batch_eval", 1)) if self._forced_batch_mode is None else int(self._forced_batch_mode)
        self.window = max([1] + [required_lookback(f, p.lookback) for p in compiled for f in p.formulas]) + 1
        self._loaded_at = time.monotonic()

    def timeframes(self, candidates=("1min", "3min", "5min")) -> set:
        """The timeframes at least one loaded pattern can form on. A condition
        is ruled out on a timeframe only when it is (an AND containing) a
        comparison of TF_MINUTES with a number that is false there; a pattern
        is left out only when too few conditions remain to reach the formed
        threshold. Anything else counts as possible (2026-11-10)."""
        out = set()
        for tf in candidates:
            minutes = float(timeframe_minutes(tf))
            for p in self.patterns:
                if not p.trees:
                    continue
                possible = sum(0 if _false_on_timeframe(t, minutes) else 1 for t in p.trees)
                if possible / len(p.trees) >= self.threshold:
                    out.add(tf)
                    break
        return out

    def meta(self, key: str):
        """(name, pattern_type) for a pattern key, or None -- labels live hits."""
        for p in self.patterns:
            if p.key == key:
                return p.name or p.key, p.pattern_type
        return None

    def _explain(self, key, latest, p, df, timeframe, match) -> None:
        """Keep why this hit formed: each condition's required and actual value."""
        from hit_explain import explain_pattern
        try:
            context = {"strength_rules": self.strength.get(timeframe, {}), "lookback": p.lookback}
            conditions = explain_pattern(p.formulas, df, context)
        except Exception:
            logger.exception("Explaining pattern %s failed", p.key)
            return
        self.explanations.setdefault(key, []).append({
            "ts": latest["ts"], "timeframe": timeframe, "pattern": p.key, "name": p.name or p.key,
            "type": p.pattern_type, "weight": p.weight, "match": round(match, 4), "score": round(p.weight * match, 4),
            "met": sum(1 for c in conditions if c["met"]), "of": len(conditions),
            "close": latest.get("close"), "conditions": conditions,
        })

    def _maybe_reload(self) -> None:
        if time.monotonic() - self._loaded_at > RELOAD_SECONDS:
            try:
                self.reload()
            except Exception:
                logger.exception("Pattern definitions reload failed; keeping the previous set")
                self._loaded_at = time.monotonic()

    # ---- evaluation ----

    def match_percentages(self, df: pd.DataFrame, timeframe: str) -> Dict[str, Tuple[_Compiled, float]]:
        """{pattern key: (pattern, match %)} on the frame's LAST row."""
        out = {}
        context_base = {"strength_rules": self.strength.get(timeframe, {})}
        for p in self.patterns:
            context = {**context_base, "lookback": p.lookback}
            met = 0
            for tree in p.trees:
                try:
                    value = _eval_node(tree, df, context)
                    last = value.iloc[-1] if isinstance(value, pd.Series) else value
                    met += bool(last) if not pd.isna(last) else 0
                except FormulaError:
                    pass  # a field this frame lacks counts as not met
            out[p.key] = (p, met / len(p.trees) if p.trees else 0.0)
        return out

    def on_candle(self, key: tuple, rows, timeframe: str, record: bool = True) -> List[dict]:
        """Evaluate every pattern on the latest candle of `rows` (chronological
        engine rows, each with _events). Returns the hits to record:
        [{pattern, key, pattern_type, match, score}]. With record=False the
        state still advances (warm-up), but nothing is returned."""
        self._maybe_reload()
        if not self.patterns or not rows:
            return []
        df = self._frame(self._live_window(rows), timeframe)
        results = self.match_percentages(df, timeframe)
        return self._advance(key, rows[-1], results, timeframe, record, df)

    def _frame(self, window: list, timeframe: str) -> pd.DataFrame:
        df = frame_from_rows(window)
        if EVENTS_COLUMN not in df.columns:
            df[EVENTS_COLUMN] = [frozenset()] * len(df)
        df["tf_minutes"] = float(timeframe_minutes(timeframe))  # TF_MINUTES in conditions
        return df

    def _live_window(self, rows) -> list:
        """The rows a candle's frame uses: the latest `window` rows from today
        AND the previous session only -- running data spans sessions
        (2026-10-16); dots are still marked per day (_advance)."""
        recent = list(rows)[-self.window:]
        if self.daily:
            return recent
        days = sorted({(r["ts"] + IST).date() for r in recent})[-2:]
        return [r for r in recent if (r["ts"] + IST).date() in days]

    def _advance(self, key: tuple, latest: dict, results, timeframe: str, record: bool, frame) -> List[dict]:
        """One candle's state step, shared by on_candle and evaluate_batch: day
        reset, candle index, scores, formed edge + cooldown, explanations.
        `frame`: the candle's frame, or a callable building it (only needed
        when something forms)."""
        day = DAILY_KEY if self.daily else (latest["ts"] + IST).date()
        if self._day.get(key) != day:  # a new session: patterns never span days
            self._day[key] = day
            self._index[key] = 0
            for p in self.patterns:
                self._state.pop((key, p.id), None)
            self.explanations[key] = []
        self._index[key] = self._index.get(key, 0) + 1
        index = self._index[key]
        self.scores[key] = {k: m for k, (_, m) in results.items()}
        hits = []
        for k, (p, match) in results.items():
            st = self._state.setdefault((key, p.id), {"formed": False, "last_hit": None})
            formed = match >= self.threshold
            if formed and not st["formed"]:
                cooled = st["last_hit"] is None or index - st["last_hit"] > p.cooldown
                if cooled:
                    st["last_hit"] = index
                    if self.explain:
                        if callable(frame):
                            frame = frame()
                        self._explain(key, latest, p, frame, timeframe, match)
                    if record:
                        hits.append({"pattern": p, "key": k, "pattern_type": p.pattern_type,
                                     "match": round(match, 4), "score": round(p.weight * match, 4)})
            st["formed"] = formed
        return hits

    # ---- batch mode (2026-11-10) ----

    def evaluate_batch(self, key: tuple, rows: list, evals: List[Tuple[int, bool]], timeframe: str) -> Dict[int, List[dict]]:
        """Same result as on_candle(key, <the engine's rows as of candle i>,
        timeframe, record) for each (i, record) in `evals`, in order -- but
        one frame and one evaluation per formula for all of them. `rows`: the
        engine rows in order with no gaps, starting with what the engine held
        before the first candle in `evals` (up to MAX_FRAME_ROWS). Returns
        {i: hits} for the candles that have hits."""
        self._maybe_reload()
        if not self.patterns or not evals:
            return {}
        width = min(self.window, self.max_rows)
        days = [DAILY_KEY if self.daily else (r["ts"] + IST).date() for r in rows]
        starts = {}
        for i, _ in evals:  # first row of candle i's own frame (_live_window)
            lo = max(0, i - width + 1)
            keep = sorted(set(days[lo:i + 1]))[-2:]
            while days[lo] not in keep:
                lo += 1
            starts[i] = lo
        try:
            lo = min(starts.values())
            df = self._frame(rows[lo:max(i for i, _ in evals) + 1], timeframe)
            cut = frame_slicer(df, rows, lo)
            matches = self._batch_matches(key, rows, evals, starts, timeframe, df, lo, cut)
        except Exception:
            logger.exception("Batch pattern evaluation failed for %s; evaluating candle by candle", key)
            matches = None
        out = {}
        for i, record in evals:
            if matches is None:
                hits = self.on_candle(key, as_of(rows, max(0, i - self.max_rows + 1), i), timeframe, record)
            else:
                hits = self._advance(key, rows[i], matches[i], timeframe, record, lambda i=i: cut(starts[i], i))
            if hits:
                out[i] = hits
        return out

    def _batch_matches(self, key, rows, evals, starts, timeframe, df, lo, cut) -> Dict[int, dict]:
        """{i: {pattern key: (pattern, match %)}} for every candle in evals."""
        index = [i for i, _ in evals]
        pos = np.array(index) - lo
        context_base = {"strength_rules": self.strength.get(timeframe, {})}
        # events written onto row j by a later candle: (j, name, that candle's ts)
        late = [(j, name, seen) for j in range(lo, max(index) + 1)
                for name, seen in (rows[j].get(LATE_EVENTS) or {}).items()]
        frames: Dict[int, pd.DataFrame] = {}

        def live_frame(i):
            if i not in frames:
                frames[i] = cut(starts[i], i)
            return frames[i]

        verify = self.batch_mode >= 2
        met: Dict[int, np.ndarray] = {}
        for p in self.patterns:
            context = {**context_base, "lookback": p.lookback}
            total = np.zeros(len(index), dtype=np.int64)
            for c, tree in enumerate(p.trees):
                try:
                    cond = _met(_eval_node(tree, df, context), pos)
                except FormulaError:
                    cond = np.zeros(len(index), dtype=np.int64)  # a field this frame lacks counts as not met
                depth, names = p.depths[c], p.events[c]
                # candles whose own frame is shorter than this condition's reach
                redo = {n for n, i in enumerate(index) if depth > i - starts[i] + 1}
                if names:  # candles that read row j before its event was written
                    for j, name, seen in late:
                        if name in names:
                            redo.update(n for n, i in enumerate(index)
                                        if j <= i and rows[i]["ts"] < seen and j >= max(starts[i], i - depth + 1))
                for n in (range(len(index)) if verify else sorted(redo)):
                    i = index[n]
                    frame = live_frame(i)
                    try:
                        exact = _met(_eval_node(tree, frame, context), np.array([len(frame) - 1]))[0]
                    except FormulaError:
                        exact = 0
                    if verify and n not in redo and exact != cond[n]:
                        logger.warning("Batch/per-candle mismatch: %s %s condition %d at %s: batch %s, per-candle %s",
                                       key, p.key, c, rows[i]["ts"], cond[n], exact)
                    cond[n] = exact
                total += cond
            met[p.id] = total
        return {i: {p.key: (p, int(met[p.id][n]) / len(p.trees) if p.trees else 0.0) for p in self.patterns}
                for n, i in enumerate(index)}


def _met(value, at) -> "np.ndarray":
    """1 where a condition's value is true at positions `at`, else 0 (NaN = not met)."""
    if isinstance(value, pd.Series):
        values = value.to_numpy(dtype=object)[at]
    else:
        values = [value] * len(at)
    return np.array([0 if pd.isna(v) else int(bool(v)) for v in values], dtype=np.int64)


def _visible(row: dict, ts) -> Optional[frozenset]:
    """The row's events as they stood when the candle at `ts` closed, or None
    when nothing on it was written later than that."""
    late = row.get(LATE_EVENTS)
    if not late or not any(seen > ts for seen in late.values()):
        return None
    return frozenset(e for e in row.get(EVENTS_COLUMN, ()) if not (e in late and late[e] > ts))


def as_of(rows: list, lo: int, i: int) -> list:
    """rows[lo:i+1] as the engine held them when candle i closed: events
    written onto a row later (swings land ~5 candles back) left out."""
    ts_i = rows[i]["ts"]
    out = []
    for r in rows[lo:i + 1]:
        events = _visible(r, ts_i)
        out.append(r if events is None else {**r, EVENTS_COLUMN: set(events)})
    return out


def frame_slicer(df: pd.DataFrame, rows: list, lo: int):
    """cut(a, i): the frame of rows[a..i] as candle i saw it, sliced out of
    `df` (the frame of rows[lo..]) instead of being built again -- same
    values, same length, events written later left out."""
    def cut(a: int, i: int) -> pd.DataFrame:
        part = df.iloc[a - lo:i - lo + 1].reset_index(drop=True).copy()
        ts_i = rows[i]["ts"]
        for k in range(a, i + 1):
            events = _visible(rows[k], ts_i)
            if events is not None:
                part.at[k - a, EVENTS_COLUMN] = events
        return part
    return cut


_COMPARE = {ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b, ast.Lt: lambda a, b: a < b,
            ast.LtE: lambda a, b: a <= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def _false_on_timeframe(node: ast.AST, minutes: float) -> bool:
    """True only when the condition is certainly false at this timeframe:
    TF_MINUTES <op> number (either side) that fails, alone or inside an AND."""
    if isinstance(node, ast.Expression):
        return _false_on_timeframe(node.body, minutes)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return any(_false_on_timeframe(v, minutes) for v in node.values)
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _COMPARE:
        left, right = node.left, node.comparators[0]

        def is_tf(n):
            return isinstance(n, ast.Name) and n.id.lower() == "tf_minutes"

        def number(n):
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
                return float(n.value)
            return None
        op = _COMPARE[type(node.ops[0])]
        if is_tf(left) and number(right) is not None:
            return not op(minutes, number(right))
        if is_tf(right) and number(left) is not None:
            return not op(number(left), minutes)
    return False
