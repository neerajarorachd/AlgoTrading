"""StoryTracker: turns per-candle subject states into story rows (runs).

One StoryTracker per instrument + timeframe. Feed it every closed candle's
row (`on_row`); it returns the runs that ended on that candle. `open_runs()`
is the current setup.

Anti-flicker: a new state must hold `confirm_candles` candles in a row
before it counts. The new run's start is then backdated to its first
candle; candles where a candidate state did not hold stay in the current
run. Every run closes at the end of the trading day (all states close at
15:30); the next day's first run keeps yesterday's last state as its
previous state.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from story.indicator_rows import ist_date
from story.subjects import DEFAULTS, Ctx, Subject, default_subjects
from story.swings import SwingTracker


@dataclass
class StoryRun:
    subject: str
    state: str
    prev_state: Optional[str]
    start_ts: object
    end_ts: object
    start_index: int
    end_index: int
    candles: int
    value_start: float
    value_end: float
    rank: float
    vol_ratio_sum: float = 0.0
    vol_ratio_n: int = 0
    details: dict = field(default_factory=dict)
    is_open: bool = True

    @property
    def vol_ratio(self) -> Optional[float]:
        return self.vol_ratio_sum / self.vol_ratio_n if self.vol_ratio_n else None

    def to_dict(self) -> dict:
        return {
            "subject": self.subject, "state": self.state, "prev_state": self.prev_state,
            "start_ts": self.start_ts, "end_ts": self.end_ts, "candles": self.candles,
            "value_start": self.value_start, "value_end": self.value_end, "rank": self.rank,
            "vol_ratio": self.vol_ratio, "details": dict(self.details), "is_open": self.is_open,
        }


class _SubjectState:
    __slots__ = ("run", "cand", "last_state")

    def __init__(self):
        self.run: Optional[StoryRun] = None
        self.cand: list = []          # [(index, row, obs, vol_ratio)] of an unconfirmed new state
        self.last_state: Optional[str] = None


class StoryTracker:
    def __init__(self, subjects: Optional[List[Subject]] = None, settings: Optional[dict] = None,
                 swing_tracker: Optional[SwingTracker] = None):
        self.subjects = subjects if subjects is not None else default_subjects()
        self.settings = {**DEFAULTS, **(settings or {})}
        for s in self.subjects:
            s.settings = self.settings
        self.confirm = max(1, int(self.settings["confirm_candles"]))
        self.swings = swing_tracker if swing_tracker is not None else SwingTracker()
        self.ctx = Ctx(self.settings)
        self._st: Dict[str, _SubjectState] = {s.name: _SubjectState() for s in self.subjects}
        self._vols: deque = deque(maxlen=int(self.settings["volume_lookback"]))
        self._day = None
        self._i = -1
        self.new_swings: list = []    # swings confirmed on the latest candle

    # ------------------------------------------------------------ public
    def on_row(self, row: dict) -> List[StoryRun]:
        """One closed candle. Returns the runs that ended (closed) on it."""
        self._i += 1
        closed: List[StoryRun] = []
        d = ist_date(row["ts"])
        if d != self._day:
            if self._day is not None:
                closed += self.close_all()
                self.ctx.prev_day_high, self.ctx.prev_day_low = self.ctx.day_high, self.ctx.day_low
            self._day = d
            self.ctx.day_high = self.ctx.day_low = None
        ctx = self.ctx
        ctx.rows.append(row)
        if len(ctx.rows) > 60:
            del ctx.rows[:-60]
        ctx.day_high = row["high"] if ctx.day_high is None else max(ctx.day_high, row["high"])
        ctx.day_low = row["low"] if ctx.day_low is None else min(ctx.day_low, row["low"])
        vol = row.get("volume") or 0
        ctx.vol_ratio = (vol / (sum(self._vols) / len(self._vols))
                         if len(self._vols) == self._vols.maxlen and sum(self._vols) > 0 else None)
        self._vols.append(vol)
        self.new_swings = self.swings.update(row)
        if self.new_swings:
            ctx.last_leg_pct = self.new_swings[-1].leg_pct
        ctx.structure = self.swings.structure()

        for subj in self.subjects:
            obs = subj.observe(ctx)
            if obs is None:
                continue
            r = self._step(subj, self._st[subj.name], self._i, row, obs, ctx.vol_ratio)
            if r is not None:
                closed.append(r)
        return closed

    def open_runs(self) -> Dict[str, StoryRun]:
        return {name: st.run for name, st in self._st.items() if st.run is not None}

    def close_all(self) -> List[StoryRun]:
        """End of day: every open run closes (pending candidates are folded
        into it, they never confirmed)."""
        out = []
        for subj in self.subjects:
            st = self._st[subj.name]
            if st.run is None:
                continue
            for i, r, o, vr in st.cand:
                self._extend(subj, st.run, i, r, o, vr)
            st.cand = []
            st.run.is_open = False
            st.last_state = st.run.state
            out.append(st.run)
            st.run = None
        return out

    # ------------------------------------------------------------ internals
    def _new_run(self, subj, st, items) -> StoryRun:
        i0, r0, o0, _ = items[0]
        run = StoryRun(subject=subj.name, state=o0[0], prev_state=st.last_state,
                       start_ts=r0["ts"], end_ts=r0["ts"], start_index=i0, end_index=i0,
                       candles=0, value_start=o0[1], value_end=o0[1], rank=o0[2])
        for i, r, o, vr in items:
            self._extend(subj, run, i, r, o, vr)
        return run

    def _extend(self, subj, run, i, row, obs, vol_ratio):
        run.end_ts, run.end_index = row["ts"], i
        run.candles += 1
        run.value_end, run.rank = obs[1], obs[2]
        if vol_ratio is not None:
            run.vol_ratio_sum += vol_ratio
            run.vol_ratio_n += 1
        subj.accumulate(run.details, row, run.state)

    def _step(self, subj, st, i, row, obs, vol_ratio) -> Optional[StoryRun]:
        item = (i, row, obs, vol_ratio)
        if st.run is None:
            st.run = self._new_run(subj, st, [item])
            return None
        if obs[0] == st.run.state:
            for c in st.cand:  # a candidate that did not hold stays in this run
                self._extend(subj, st.run, *c)
            st.cand = []
            self._extend(subj, st.run, *item)
            return None
        if st.cand and st.cand[0][2][0] != obs[0]:
            for c in st.cand:
                self._extend(subj, st.run, *c)
            st.cand = []
        st.cand.append(item)
        if len(st.cand) < self.confirm:
            return None
        ended = st.run
        ended.is_open = False
        st.last_state = ended.state
        st.run = self._new_run(subj, st, st.cand)
        st.cand = []
        return ended
