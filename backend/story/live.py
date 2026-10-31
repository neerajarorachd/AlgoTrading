"""Live market story (plan P2): one StoryTracker per (symbol, exchange
segment, timeframe), fed by ActivityEngine with the same per-candle row the
rest of the engine uses, while the engine setting `story_enabled` is 1.

Holds today's story in memory (closed runs + the open runs = the current
setup); ActivityEngine.flush() saves it through db.ops.LibStory:
  - the first save of a day after a start / restart replaces that day's
    rows for the stock (recovery rebuilds the whole day in memory, so this
    also clears anything a crash left half-written);
  - later saves insert only the newly closed runs and replace the open ones.
Days already moved to the archive (EOD) are never written again.

History warm-up at startup replays ~35 days through the engine; the story
only needs the previous trading day for context (previous-day high/low,
volume average, swing structure), so candles older than `history_days`
calendar days are skipped. The test-stack replay shifts its candles to
today, so a wall-clock cut-off is safe there too.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from story.indicator_rows import ist_date
from story.tracker import StoryRun, StoryTracker

Key = Tuple[str, str, str]  # (symbol, exchange_segment, timeframe)
_IST = timedelta(hours=5, minutes=30)


def _ist_today() -> date:
    return (datetime.now(timezone.utc) + _IST).date()


def _naive_utc(ts) -> datetime:
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _iso(ts) -> str:
    return _naive_utc(ts).isoformat() + "Z"


def run_json(run: StoryRun) -> dict:
    """API shape of one run."""
    return {"subject": run.subject, "state": run.state, "prev_state": run.prev_state,
            "start_ts": _iso(run.start_ts), "end_ts": _iso(run.end_ts), "candles": run.candles,
            "value_start": run.value_start, "value_end": run.value_end, "rank": run.rank,
            "vol_ratio": run.vol_ratio, "details": dict(run.details), "is_open": run.is_open}


class LiveStory:
    def __init__(self, settings: Optional[dict] = None, history_days: int = 5,
                 today_fn: Callable[[], date] = _ist_today):
        self.settings = settings
        self.history_days = history_days
        self._today = today_fn
        self.trackers: Dict[Key, StoryTracker] = {}
        self.day: Dict[Key, date] = {}
        self.closed: Dict[Key, List[StoryRun]] = defaultdict(list)
        self.saved: Dict[Key, int] = defaultdict(int)
        self.rewritten: set = set()  # (key, day) saved in full once
        self.archived_through: Optional[date] = None

    # ------------------------------------------------------------ feed
    def on_row(self, key: Key, row: dict) -> None:
        d = ist_date(row["ts"])
        if d < self._today() - timedelta(days=self.history_days):
            return
        t = self.trackers.get(key)
        if t is None:
            t = self.trackers[key] = StoryTracker(settings=self.settings)
        ended = t.on_row(row)
        if self.day.get(key) != d:
            self.day[key] = d
            self.closed[key] = []
            self.saved[key] = 0
        # runs that ended on a day's first candle belong to the day before
        self.closed[key].extend(r for r in ended if ist_date(r.start_ts) == d)

    # ------------------------------------------------------------ read
    def runs(self, key: Key) -> Tuple[Optional[date], List[StoryRun]]:
        """Today's runs for one stock + timeframe (closed and open), oldest first."""
        d = self.day.get(key)
        t = self.trackers.get(key)
        if d is None or t is None:
            return None, []
        opened = [r for r in t.open_runs().values() if ist_date(r.start_ts) == d]
        return d, sorted(self.closed[key] + opened, key=lambda r: (_naive_utc(r.start_ts), r.subject))

    def setup_now(self, timeframe: str) -> Dict[Key, Dict[str, StoryRun]]:
        """The open runs (current setup) of every stock on one timeframe."""
        return {k: t.open_runs() for k, t in self.trackers.items() if k[2] == timeframe}

    # ------------------------------------------------------------ save
    def collect(self, instrument_id_of: Callable[[Key], Optional[int]]):
        """What the next save writes. Call under the engine lock; pass the
        returned marks to mark_saved() after the write succeeds."""
        full, open_only, rows, marks = [], [], [], []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for key, t in self.trackers.items():
            d = self.day.get(key)
            if d is None or (self.archived_through is not None and d <= self.archived_through):
                continue
            iid = instrument_id_of(key)
            if iid is None:
                continue
            tf = key[2]
            closed = self.closed[key]
            if (key, d) in self.rewritten:
                open_only.append((d, tf, iid))
                new = closed[self.saved[key]:]
            else:
                full.append((d, tf, iid))
                new = closed
            opened = [r for r in t.open_runs().values() if ist_date(r.start_ts) == d]
            rows += [self._row(r, iid, tf, d, now) for r in new + opened]
            marks.append((key, d, len(closed)))
        return full, open_only, rows, marks

    def mark_saved(self, marks) -> None:
        for key, d, n in marks:
            if self.day.get(key) == d:
                self.saved[key] = n
            self.rewritten.add((key, d))
        oldest = self._today() - timedelta(days=self.history_days)
        self.rewritten = {x for x in self.rewritten if x[1] >= oldest}

    @staticmethod
    def _row(r: StoryRun, iid: int, tf: str, d: date, now: datetime) -> dict:
        return {"instrument_id": iid, "timeframe": tf, "trading_day": d, "subject": r.subject,
                "state": r.state, "prev_state": r.prev_state, "start_ts": _naive_utc(r.start_ts),
                "end_ts": _naive_utc(r.end_ts), "candles": r.candles,
                "value_start": None if r.value_start is None else float(r.value_start),
                "value_end": None if r.value_end is None else float(r.value_end),
                "rank": None if r.rank is None else float(r.rank),
                "vol_ratio": r.vol_ratio, "details": json.dumps(r.details, sort_keys=True) if r.details else None,
                "is_open": bool(r.is_open), "updated_at": now}
