"""Swing points with labels, leg % and swing ratio (plan P0/P1).

Two ways to confirm a swing, compared in P0:

- "fixed": the engine's rule today. A swing high is a candle whose high is
  the highest of `lookback` candles on each side, so it is known `lookback`
  candles late. Optional `min_leg_pct` (ignore swings that moved less than
  this from the previous opposite swing) and `alternate` (high, low, high...;
  a second high in a row replaces the first only if it is higher).
- "zigzag": a swing high is confirmed as soon as price falls `reversal_pct`
  below the highest high since the last swing low (and the mirror for lows).
  Strong moves confirm quickly; moves smaller than `reversal_pct` never do.

Every confirmed swing gets:
- label: HH / LH / EH for highs, HL / LL / EL for lows, against the previous
  swing of the same kind (EH/EL = within `equal_pct`);
- leg_pct: % move from the previous opposite swing (signed);
- ratio: this leg's size / the previous leg's size, e.g. a 0.8% pullback
  after a 1% rise = 0.8;
- lag: candles between the swing candle and its confirmation.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Swing:
    kind: str            # "high" | "low"
    price: float
    ts: object           # the swing candle's own time
    index: int           # the swing candle's row index
    confirm_index: int   # row index where it became known
    label: Optional[str] = None
    leg_pct: Optional[float] = None
    ratio: Optional[float] = None
    replaced: bool = False  # alternate mode: this swing replaced the previous same-kind one

    @property
    def lag(self) -> int:
        return self.confirm_index - self.index


HIGH_LABELS = ("HH", "LH", "EH")
LOW_LABELS = ("HL", "LL", "EL")
EQUAL_PCT = 0.05


def label_swing(kind: str, price: float, prev_same: Optional[float], prev_other: Optional[float],
                prev_other_leg: Optional[float], equal_pct: float = EQUAL_PCT):
    """(label, leg_pct, ratio) for a new swing. Shared by SwingTracker and the
    live ActivityEngine so both label swings the same way.
    label: vs the previous swing of the same kind (EH/EL within equal_pct %);
    leg_pct: signed % move from the previous opposite swing;
    ratio: |this leg| / |the previous opposite swing's own leg|."""
    label = leg = ratio = None
    if prev_same:
        diff = (price - prev_same) / prev_same * 100
        if abs(diff) <= equal_pct:
            label = "EH" if kind == "high" else "EL"
        elif kind == "high":
            label = "HH" if diff > 0 else "LH"
        else:
            label = "HL" if diff > 0 else "LL"
    if prev_other:
        leg = (price - prev_other) / prev_other * 100
        if prev_other_leg:
            ratio = abs(leg) / abs(prev_other_leg)
    return label, leg, ratio


@dataclass
class SwingTracker:
    mode: str = "fixed"
    lookback: int = 5
    min_leg_pct: float = 0.0
    alternate: bool = False
    reversal_pct: float = 0.5
    equal_pct: float = 0.05
    swings: List[Swing] = field(default_factory=list)

    def __post_init__(self):
        if self.mode not in ("fixed", "zigzag"):
            raise ValueError(f"unknown swing mode {self.mode!r}")
        self._window: deque = deque(maxlen=2 * self.lookback + 1)
        self._i = -1
        # zigzag state
        self._dir = 0  # +1 looking for a high, -1 looking for a low, 0 not started
        self._hi = self._lo = None  # (price, ts, index)

    # ------------------------------------------------------------ public
    def update(self, row: dict) -> List[Swing]:
        """Feed one candle row (needs ts/high/low). Returns swings confirmed
        on this candle (usually none)."""
        self._i += 1
        if self.mode == "fixed":
            return self._update_fixed(row)
        return self._update_zigzag(row)

    def last(self, kind: str) -> Optional[Swing]:
        for s in reversed(self.swings):
            if s.kind == kind:
                return s
        return None

    # ------------------------------------------------------------ fixed
    def _update_fixed(self, row: dict) -> List[Swing]:
        self._window.append((self._i, row["ts"], row["high"], row["low"]))
        if len(self._window) < self._window.maxlen:
            return []
        mid_i, mid_ts, mid_h, mid_l = self._window[self.lookback]
        out = []
        if mid_h == max(w[2] for w in self._window):
            s = self._accept("high", mid_h, mid_ts, mid_i)
            if s:
                out.append(s)
        if mid_l == min(w[3] for w in self._window):
            s = self._accept("low", mid_l, mid_ts, mid_i)
            if s:
                out.append(s)
        return out

    # ------------------------------------------------------------ zigzag
    def _update_zigzag(self, row: dict) -> List[Swing]:
        h, l, ts, i = row["high"], row["low"], row["ts"], self._i
        rev = self.reversal_pct / 100
        out = []
        if self._dir == 0:
            if self._hi is None or h > self._hi[0]:
                self._hi = (h, ts, i)
            if self._lo is None or l < self._lo[0]:
                self._lo = (l, ts, i)
            if l <= self._hi[0] * (1 - rev) and self._hi[2] < i:
                s = self._accept("high", *self._hi)
                out += [s] if s else []
                self._dir, self._lo = -1, (l, ts, i)
            elif h >= self._lo[0] * (1 + rev) and self._lo[2] < i:
                s = self._accept("low", *self._lo)
                out += [s] if s else []
                self._dir, self._hi = 1, (h, ts, i)
            return out
        if self._dir == 1:  # rising: track the high, confirm it on a big enough drop
            if h > self._hi[0]:
                self._hi = (h, ts, i)
            elif l <= self._hi[0] * (1 - rev):
                s = self._accept("high", *self._hi)
                out += [s] if s else []
                self._dir, self._lo = -1, (l, ts, i)
        else:
            if l < self._lo[0]:
                self._lo = (l, ts, i)
            elif h >= self._lo[0] * (1 + rev):
                s = self._accept("low", *self._lo)
                out += [s] if s else []
                self._dir, self._hi = 1, (h, ts, i)
        return out

    # ------------------------------------------------------------ shared
    def _accept(self, kind: str, price: float, ts, index: int) -> Optional[Swing]:
        other = "low" if kind == "high" else "high"
        prev_same = self.last(kind)
        prev_other = self.last(other)
        replaced = False
        if self.alternate and self.swings and self.swings[-1].kind == kind:
            last = self.swings[-1]
            more_extreme = price > last.price if kind == "high" else price < last.price
            if not more_extreme:
                return None
            self.swings.pop()  # the new, more extreme point takes its place
            replaced = True
            prev_same = self.last(kind)
            prev_other = self.last(other)
        if self.min_leg_pct and prev_other is not None:
            leg = abs(price - prev_other.price) / prev_other.price * 100
            if leg < self.min_leg_pct:
                return None
        s = Swing(kind=kind, price=price, ts=ts, index=index, confirm_index=self._i, replaced=replaced)
        s.label, s.leg_pct, s.ratio = label_swing(
            kind, price,
            prev_same.price if prev_same else None,
            prev_other.price if prev_other else None,
            prev_other.leg_pct if prev_other else None,
            self.equal_pct,
        )
        self.swings.append(s)
        if len(self.swings) > 50:
            del self.swings[:-50]
        return s

    def structure(self) -> Optional[str]:
        """uptrend (last high HH and last low HL), downtrend (LH and LL),
        else range; None until both kinds have a label."""
        h, l = self.last("high"), self.last("low")
        if not h or not l or not h.label or not l.label:
            return None
        if h.label == "HH" and l.label == "HL":
            return "uptrend"
        if h.label == "LH" and l.label == "LL":
            return "downtrend"
        return "range"
