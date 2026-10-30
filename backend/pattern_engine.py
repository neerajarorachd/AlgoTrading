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
event lists all carry them with no extra plumbing."""
from __future__ import annotations

import ast
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

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


class PatternEvaluator:
    def __init__(self, session_factory, owner_id: Optional[int] = None):
        self.session_factory = session_factory
        self.owner_id = owner_id
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
            settings = LibSystemSettings.load_by_keys(session, ["pattern_formed_threshold"])
            self.strength = {tf: LibSignalStrength.rules_for_timeframe(session, tf, self.owner_id) for tf in ("1min", "3min", "5min")}
        compiled = []
        for p in rows:
            formulas = [c["formula"] for c in p["conditions"]]
            try:
                trees = [ast.parse(_normalize(f), mode="eval") for f in formulas]
            except SyntaxError:
                logger.warning("Pattern %s has a condition that does not parse; skipped", p["key"])
                continue
            compiled.append(_Compiled(p["id"], p["key"], p["pattern_type"], p["weight"], p["lookback"], p["cooldown"], trees, formulas, p["name"]))
        self.patterns = compiled
        self.threshold = float(settings.get("pattern_formed_threshold", 0.8))
        self.window = max([1] + [required_lookback(f, p.lookback) for p in compiled for f in p.formulas]) + 1
        self._loaded_at = time.monotonic()

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
        latest = rows[-1]
        day = (latest["ts"] + IST).date()
        if self._day.get(key) != day:  # a new session: patterns never span days
            self._day[key] = day
            self._index[key] = 0
            for p in self.patterns:
                self._state.pop((key, p.id), None)
            self.explanations[key] = []
        self._index[key] = self._index.get(key, 0) + 1
        index = self._index[key]
        # the latest `window` rows from today AND the previous session: running
        # data spans sessions (2026-10-16); dots are still marked per day (above)
        recent = list(rows)[-self.window:]
        days = sorted({(r["ts"] + IST).date() for r in recent})[-2:]
        window = [r for r in recent if (r["ts"] + IST).date() in days]
        df = frame_from_rows(window)
        if EVENTS_COLUMN not in df.columns:
            df[EVENTS_COLUMN] = [frozenset()] * len(df)
        df["tf_minutes"] = float(timeframe_minutes(timeframe))  # TF_MINUTES in conditions
        results = self.match_percentages(df, timeframe)
        self.scores[key] = {k: m for k, (_, m) in results.items()}
        hits = []
        for k, (p, match) in results.items():
            st = self._state.setdefault((key, p.id), {"formed": False, "last_hit": None})
            formed = match >= self.threshold
            if formed and not st["formed"]:
                cooled = st["last_hit"] is None or index - st["last_hit"] > p.cooldown
                if cooled:
                    st["last_hit"] = index
                    self._explain(key, latest, p, df, timeframe, match)
                    if record:
                        hits.append({"pattern": p, "key": k, "pattern_type": p.pattern_type,
                                     "match": round(match, 4), "score": round(p.weight * match, 4)})
            st["formed"] = formed
        return hits
