"""In-memory copy of each chart's past sessions (2026-11-09).

Opening a stock's chart loads 20 past sessions of candles plus their
indicators and markers: about 4 MB through the SSH tunnel, 7-8 s a click.
Past sessions don't change during the day, so this keeps them in memory per
(symbol, segment, timeframe) and the chart routes read only TODAY's rows from
the DB. Today's data is never cached.

- Filled in the background, one key at a time: every active symbol x
  1/3/5-min at startup, any key a request missed, all keys again on a new
  IST day.
- A write that touches a day before today (gap fill, chart history fill,
  engine recovery...) drops that symbol's entries at once -- the Lib write
  functions call the notify_* hooks below -- and queues a rebuild
  REBUILD_DELAY_SECONDS later. Until it's back, the routes read the DB
  exactly as before, so a stale copy is never served.
- Writes made by other processes (the VM's update scripts) aren't seen;
  they only write the day that just closed, which the next day's rebuild
  picks up.
"""
from __future__ import annotations

import logging
import threading
import time
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import zlib
from array import array
from itertools import accumulate
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)
TIMEFRAMES = ("1min", "3min", "5min")
CACHED_SESSIONS = 20  # what the chart opens on; its scroll-back reads the DB
REBUILD_DELAY_SECONDS = 30  # quiet time after a past-day write before rebuilding (writes come in bursts)
RETRY_DELAY_SECONDS = 300
MAX_ENTRIES = 300  # least recently used beyond this is dropped: 100 stocks x 3 timeframes, ~50 MB

_INDICATOR_FIELDS = (
    "rsi", "macd_line", "macd_signal", "stoch_k", "stoch_d", "vwap", "ma21", "ma50",
    "ema5", "ema14", "ema21", "ema50", "atr", "bb_upper", "bb_middle", "bb_lower",
    "adx", "plus_di", "minus_di",
)

Key = Tuple[str, str, str]  # (symbol, exchange_segment, timeframe)

_installed: Optional["PastChartCache"] = None


def install(cache: Optional["PastChartCache"]) -> None:
    """Make `cache` the one the Lib write hooks notify (None to remove)."""
    global _installed
    _installed = cache


def ist_today() -> date:
    return (datetime.now(timezone.utc) + IST_OFFSET).date()


def day_start_utc(day: date) -> datetime:
    """00:00 IST of `day`, as naive UTC (how candle ts are stored)."""
    return datetime.combine(day, datetime.min.time()) - IST_OFFSET


def _naive(ts: datetime) -> datetime:
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def _is_past(ts: Optional[datetime]) -> bool:
    return ts is not None and _naive(ts) < day_start_utc(ist_today())


# --- hooks called by the Lib write functions -------------------------------

def notify_candles_written(candles: Iterable[Tuple[str, str, object]]) -> None:
    """(symbol, exchange_segment, Candle) tuples just written to a candle table."""
    if _installed is None:
        return
    for symbol, exchange_segment in {(s, seg) for s, seg, c in candles if _is_past(c.timestamp)}:
        _installed.invalidate_symbol(symbol, exchange_segment)


def notify_instrument_rows_written(rows: Iterable[dict]) -> None:
    """Indicator/activity row dicts (instrument_id, ts, ...) just written."""
    if _installed is None:
        return
    for instrument_id in {r["instrument_id"] for r in rows if _is_past(r.get("ts"))}:
        _installed.invalidate_instrument(instrument_id)


def notify_instrument_deleted_since(instrument_id: int, since: datetime) -> None:
    if _installed is not None and _is_past(since):
        _installed.invalidate_instrument(instrument_id)


# --- the cache --------------------------------------------------------------
# Each column is packed and compressed: timestamps and numbers as deltas of
# integers (numbers in units of 0.0001 -- prices have at most 2 decimals, so
# they stay exact; indicators round to 4 decimals), then zlib. About 0.5 MB a
# stock for 20 sessions of 1/3/5-min with all indicators and markers (one
# Python object per row was ~21 MB). A read unpacks one table (a few ms) and
# rebuilds rows only for the slice asked for, with the attribute names the
# routes' serializers read.

_EPOCH = datetime(1970, 1, 1)
_US = timedelta(microseconds=1)
_SCALE = 10_000  # 4 decimals
_NONE = -(2 ** 62)  # a missing value


def _us(ts: datetime) -> int:
    return (_naive(ts) - _EPOCH) // _US


def _fixed(value) -> int:
    return _NONE if value is None else round(float(value) * _SCALE)


def _pack(values: List[int]) -> bytes:
    deltas = array("q", values)
    for i in range(len(deltas) - 1, 0, -1):
        deltas[i] -= deltas[i - 1]
    return zlib.compress(deltas.tobytes(), 6)


def _unpack(blob: bytes) -> List[int]:
    deltas = array("q")
    deltas.frombytes(zlib.decompress(blob))
    return list(accumulate(deltas))


class _Table:
    """Rows sorted by ts, stored as compressed columns: ts (int microseconds),
    numbers (fixed point, `_NONE` = None), whole numbers and short text codes."""

    def __init__(self, rows, numbers: Tuple[str, ...] = (), ints: Tuple[str, ...] = (), codes: Tuple[str, ...] = ()):
        self.numbers, self.int_fields, self.code_fields = numbers, ints, codes
        self.codes: List[Optional[str]] = []
        lookup: Dict[Optional[str], int] = {}
        ts, cols = [], {name: [] for name in numbers + ints + codes}
        for row in rows:
            ts.append(_us(row.ts))
            for name in numbers:
                cols[name].append(_fixed(getattr(row, name, None)))
            for name in ints:
                value = getattr(row, name, None)
                cols[name].append(_NONE if value is None else int(value))
            for name in codes:
                value = getattr(row, name, None)
                if value not in lookup:
                    lookup[value] = len(self.codes)
                    self.codes.append(value)
                cols[name].append(lookup[value])
        self.length = len(ts)
        self.first = ts[0] if ts else None
        self.blobs = {"ts": _pack(ts), **{name: _pack(values) for name, values in cols.items()}}

    def __len__(self) -> int:
        return self.length

    def first_ts(self) -> datetime:
        return _EPOCH + self.first * _US

    def nbytes(self) -> int:
        return sum(len(b) for b in self.blobs.values())

    def view(self) -> "_View":
        return _View(self)


class _View:
    """One table unpacked for one request."""

    def __init__(self, table: _Table):
        self.table = table
        self.ts = _unpack(table.blobs["ts"])
        self._cols: Dict[str, List[int]] = {}

    def index(self, ts: datetime, right: bool = False) -> int:
        return (bisect_right if right else bisect_left)(self.ts, _us(ts))

    def _col(self, name: str) -> List[int]:
        if name not in self._cols:
            self._cols[name] = _unpack(self.table.blobs[name])
        return self._cols[name]

    def rows(self, start: int, end: int) -> list:
        t = self.table
        numbers = [(n, self._col(n)) for n in t.numbers]
        ints = [(n, self._col(n)) for n in t.int_fields]
        codes = [(n, self._col(n)) for n in t.code_fields]
        out = []
        for i in range(start, end):
            row = SimpleNamespace(ts=_EPOCH + self.ts[i] * _US)
            for name, col in numbers:
                v = col[i]
                setattr(row, name, None if v == _NONE else v / _SCALE)
            for name, col in ints:
                v = col[i]
                setattr(row, name, None if v == _NONE else v)
            for name, col in codes:
                setattr(row, name, t.codes[col[i]])
            out.append(row)
        return out


_IST_US = IST_OFFSET // _US
_DAY_US = timedelta(days=1) // _US


def _ist_day_us(ts_us: int) -> int:
    return (ts_us + _IST_US) // _DAY_US


@dataclass
class _Entry:
    day: date
    instrument_id: Optional[int]
    candles: _Table
    day_first_idx: List[int]  # index of each session's first candle, ascending
    has_more: bool  # candles exist before the first cached one
    indicators: _Table
    markers: _Table
    last_used: float = field(default_factory=time.monotonic)

    def nbytes(self) -> int:
        return self.candles.nbytes() + self.indicators.nbytes() + self.markers.nbytes()


_CANDLE_FLOATS = ("open_price", "high_price", "low_price", "close_price")
_MARKER_FLOATS = ("intensity", "neckline_price")
_MARKER_CODES = ("activity_type", "activity")


class PastChartCache:
    def __init__(self, session_factory, sessions: int = CACHED_SESSIONS, rebuild_delay: float = REBUILD_DELAY_SECONDS):
        self._session_factory = session_factory
        self._sessions = sessions
        self._rebuild_delay = rebuild_delay
        self._lock = threading.Lock()
        self._entries: Dict[Key, _Entry] = {}
        self._by_instrument: Dict[Tuple[int, str], Key] = {}
        self._instrument_symbol: Dict[int, Tuple[str, str]] = {}
        self._gen: Dict[Tuple[str, str], int] = {}
        self._known: set = set()  # keys rebuilt on a new day
        self._due: Dict[Key, float] = {}  # queued builds -> monotonic time due
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._day = ist_today()

    # ---- reads (None = not cached, caller reads the DB) ----

    def sessions_before(self, symbol: str, exchange_segment: str, timeframe: str,
                        before: datetime, days: int) -> Optional[Tuple[list, bool]]:
        """Same result as routes_candles._sessions_before: candles of the last
        `days` sessions strictly before `before`, and whether older exist."""
        if days < 1:
            return None
        key = (symbol, exchange_segment, timeframe)
        entry = self._entry(key)
        if entry is None:
            return None
        if _naive(before) > day_start_utc(entry.day):
            return None
        candles = entry.candles.view()
        end = candles.index(before)
        sessions = bisect_left(entry.day_first_idx, end) if end else 0  # sessions with a candle before `before`
        if sessions >= days:
            start = entry.day_first_idx[sessions - days]
            return candles.rows(start, end), start > 0 or entry.has_more
        if entry.has_more:
            return None  # needs older sessions than cached
        return candles.rows(0, end), False

    def indicator_rows(self, instrument_id: int, timeframe: str, ts_from, ts_to) -> Optional[list]:
        entry = self._ranged_entry(instrument_id, timeframe, ts_from, ts_to)
        if entry is None:
            return None
        table = entry.indicators.view()
        return table.rows(table.index(ts_from), table.index(ts_to, right=True))

    def marker_rows(self, instrument_id: int, timeframe: str, ts_from, ts_to) -> Optional[list]:
        entry = self._ranged_entry(instrument_id, timeframe, ts_from, ts_to)
        if entry is None:
            return None
        table = entry.markers.view()
        return table.rows(table.index(ts_from), table.index(ts_to, right=True))

    def _ranged_entry(self, instrument_id, timeframe, ts_from, ts_to) -> Optional[_Entry]:
        # only a range wholly inside the cached past days
        if ts_from is None or ts_to is None:
            return None
        with self._lock:
            key = self._by_instrument.get((instrument_id, timeframe))
        if key is None:
            return None
        entry = self._entry(key)
        if entry is None or entry.instrument_id != instrument_id or not len(entry.candles):
            return None
        if _naive(ts_from) < entry.candles.first_ts() or _naive(ts_to) >= day_start_utc(entry.day):
            return None
        return entry

    def _entry(self, key: Key) -> Optional[_Entry]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.day == ist_today():
                entry.last_used = time.monotonic()
                return entry
            # missing or from a previous day: read the DB this time, build it for next
            self._known.add(key)
            self._due.setdefault(key, time.monotonic())
        self._wake.set()
        return None

    # ---- invalidation ----

    def invalidate_symbol(self, symbol: str, exchange_segment: str) -> None:
        with self._lock:
            self._gen[(symbol, exchange_segment)] = self._gen.get((symbol, exchange_segment), 0) + 1
            due = time.monotonic() + self._rebuild_delay
            for tf in TIMEFRAMES:
                key = (symbol, exchange_segment, tf)
                if self._entries.pop(key, None) is not None or key in self._known:
                    self._known.add(key)
                    self._due[key] = due
        self._wake.set()

    def invalidate_instrument(self, instrument_id: int) -> None:
        with self._lock:
            pair = self._instrument_symbol.get(instrument_id)
        if pair is not None:
            self.invalidate_symbol(*pair)

    # ---- building ----

    def enqueue(self, keys: Iterable[Key]) -> None:
        now = time.monotonic()
        with self._lock:
            for key in keys:
                self._known.add(key)
                self._due.setdefault(key, now)
        self._wake.set()

    def build(self, symbol: str, exchange_segment: str, timeframe: str) -> bool:
        """Reads one key's past sessions from the DB and stores them. False if
        a past-day write for that symbol landed meanwhile (result dropped)."""
        from api.routes_candles import _sessions_before
        from db.ops import LibActivities, LibCandleIndicators, LibSymbols

        key = (symbol, exchange_segment, timeframe)
        day = ist_today()
        end = day_start_utc(day)
        with self._lock:
            gen = self._gen.get((symbol, exchange_segment), 0)
        started = time.monotonic()
        with self._session_factory() as session:
            rows, has_more = _sessions_before(symbol, exchange_segment, timeframe, end, self._sessions, session=session)
            candles = _Table(rows, _CANDLE_FLOATS, ints=("volume",))
            candle_ts = [_us(r.ts) for r in rows]
            instrument_id = LibSymbols.get_instrument_id(session, symbol, exchange_segment)
            indicator_rows, marker_rows = [], []
            if rows and instrument_id is not None:
                first, last = candles.first_ts(), end - timedelta(microseconds=1)
                indicator_rows = LibCandleIndicators.get_for_instrument_range(
                    session, instrument_id, timeframe, ts_from=first, ts_to=last)
                marker_rows = LibActivities.get_for_instrument_range(
                    session, instrument_id, timeframe, ts_from=first, ts_to=last)
            indicators = _Table(indicator_rows, _INDICATOR_FIELDS)
            markers = _Table(marker_rows, _MARKER_FLOATS, codes=_MARKER_CODES)
            del rows, indicator_rows, marker_rows
            session.expunge_all()  # let the ORM rows go now
        day_first_idx = [i for i in range(len(candle_ts)) if i == 0 or
                         _ist_day_us(candle_ts[i]) != _ist_day_us(candle_ts[i - 1])]
        entry = _Entry(day=day, instrument_id=instrument_id, candles=candles, day_first_idx=day_first_idx,
                       has_more=has_more, indicators=indicators, markers=markers)
        with self._lock:
            if self._gen.get((symbol, exchange_segment), 0) != gen or ist_today() != day:
                return False
            self._entries[key] = entry
            self._known.add(key)
            if instrument_id is not None:
                self._by_instrument[(instrument_id, timeframe)] = key
                self._instrument_symbol[instrument_id] = (symbol, exchange_segment)
            while len(self._entries) > MAX_ENTRIES:
                oldest = min(self._entries, key=lambda k: self._entries[k].last_used)
                del self._entries[oldest]
                self._known.discard(oldest)
        logger.info("chart_cache: %s %s %s cached in %.1fs (%d sessions, %d candles, %d indicator rows, %d markers, %.1f MB)",
                    symbol, exchange_segment, timeframe, time.monotonic() - started, len(day_first_idx),
                    len(candles), len(indicators), len(markers), entry.nbytes() / 1e6)
        return True

    def start(self, keys: Iterable[Key] = ()) -> None:
        """Background worker: builds queued keys one at a time."""
        self.enqueue(keys)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="chart-cache")
            self._thread.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                if ist_today() != self._day:  # new day: yesterday became a past session
                    self._day = ist_today()
                    self._entries.clear()
                    now = time.monotonic()
                    for key in self._known:
                        self._due[key] = now
                now = time.monotonic()
                ready = [k for k, t in self._due.items() if t <= now]
                key = min(ready, key=lambda k: self._due[k]) if ready else None
                if key is not None:
                    del self._due[key]
                wait = 0 if key else min([60.0] + [t - now for t in self._due.values()])
            if key is None:
                self._wake.wait(timeout=max(wait, 0.5))
                self._wake.clear()
                continue
            try:
                self.build(*key)
            except Exception:
                logger.exception("chart_cache: building %s failed, retrying in %ds", key, RETRY_DELAY_SECONDS)
                with self._lock:
                    self._due.setdefault(key, time.monotonic() + RETRY_DELAY_SECONDS)
