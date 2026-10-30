"""Setup snapshot and the evidence lookup with back-off (plan P0/P3).

A *setup* is every open story row of one stock + timeframe, read together:
each subject's state plus how long it has lasted (bucketed, so similar
moments match). The prediction unit asks: in the past, when the setup
looked like this, how often did price reach the target before the stop?

Exact matches are rare, so it backs off: drop the condition that adds the
least, look again, until at least `min_cases` past cases match. MACD's state
is the anchor (user, 2026-10-30) and is never dropped. Which conditions add
least is learned from the training data (gain ratio on the outcome, per
MACD state), not chosen by hand.

Build the index from the past only; query it with later moments.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ANCHOR = "macd"
AGE_BUCKETS = ((3, "1-3"), (10, "4-10"))


def age_bucket(candles: int) -> str:
    for edge, label in AGE_BUCKETS:
        if candles <= edge:
            return label
    return "10+"


def snapshot(open_runs: Dict[str, object]) -> Dict[Tuple[str, str], str]:
    """{(subject, "state"): state, (subject, "age"): bucket} from a tracker's
    open runs."""
    snap = {}
    for name, run in open_runs.items():
        snap[(name, "state")] = run.state
        snap[(name, "age")] = age_bucket(run.candles)
    return snap


def _entropy(pos: int, n: int) -> float:
    if n == 0 or pos in (0, n):
        return 0.0
    p = pos / n
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


class EvidenceIndex:
    """Counts of outcome 1/0 for every back-off level of every setup.

    fit(snapshots, outcomes): outcomes are 1 (target before stop) or 0.
    predict(snap) -> dict(prob, cases, used, dropped, level) or None when
    even the anchor alone has too few cases.
    """

    def __init__(self, min_cases: int = 30, anchor: str = ANCHOR):
        self.min_cases = min_cases
        self.anchor = (anchor, "state")
        self.order: Dict[str, List[tuple]] = {}      # anchor state -> conditions, most useful first
        self.counts: Dict[str, List[dict]] = {}      # anchor state -> per level {key: [n, wins]}
        self.base: Dict[str, Tuple[int, int]] = {}   # anchor state -> (n, wins)

    def fit(self, snapshots: Sequence[dict], outcomes: Sequence[int]) -> "EvidenceIndex":
        groups: Dict[str, List[int]] = defaultdict(list)
        for k, s in enumerate(snapshots):
            a = s.get(self.anchor)
            if a is not None:
                groups[a].append(k)
        for a, idx in groups.items():
            n = len(idx)
            wins = sum(outcomes[k] for k in idx)
            self.base[a] = (n, wins)
            h = _entropy(wins, n)
            conds = sorted({c for k in idx for c in snapshots[k] if c != self.anchor})
            gain = {}
            for c in conds:
                by: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
                for k in idx:
                    v = snapshots[k].get(c)
                    by[v][0] += 1
                    by[v][1] += outcomes[k]
                # gain ratio (C4.5): information gain / split information, so a
                # condition with many values doesn't look useful just by
                # chopping the cases into small groups
                g = h - sum(m / n * _entropy(w, m) for m, w in by.values())
                split = -sum(m / n * math.log2(m / n) for m, _ in by.values())
                gain[c] = g / split if split > 0 else 0.0
            order = sorted(conds, key=lambda c: (-gain[c], c))
            self.order[a] = order
            levels: List[dict] = [defaultdict(lambda: [0, 0]) for _ in range(len(order) + 1)]
            for k in idx:
                s, y = snapshots[k], outcomes[k]
                key = ()
                levels[0][key][0] += 1
                levels[0][key][1] += y
                for j, c in enumerate(order, start=1):
                    key = key + (s.get(c),)
                    cell = levels[j][key]
                    cell[0] += 1
                    cell[1] += y
            self.counts[a] = [dict(l) for l in levels]
        return self

    def predict(self, snap: dict) -> Optional[dict]:
        a = snap.get(self.anchor)
        if a not in self.order:
            return None
        order = self.order[a]
        key = tuple(snap.get(c) for c in order)
        levels = self.counts[a]
        for j in range(len(order), -1, -1):
            n, w = levels[j].get(key[:j], (0, 0))
            if n >= self.min_cases:
                return {"prob": w / n, "cases": n, "level": j, "of": len(order),
                        "used": [self.anchor] + order[:j], "dropped": order[j:]}
        return None
