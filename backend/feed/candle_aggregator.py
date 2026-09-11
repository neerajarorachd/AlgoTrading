from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from brokers.models import Candle

# Ticks carry UTC timestamps end to end (matches the frontend tick contract's `Z`
# suffix). NSE's IST offset (UTC+5:30 = 330 minutes) divides evenly by 1, 3, and 5,
# so flooring a UTC timestamp to a 1/3/5-minute boundary is *always* simultaneously
# aligned to the same IST-session boundary a trader would expect. No IST conversion
# is needed here — don't "fix" this into a timezone conversion, it isn't a bug.
_ROLLUP_MINUTES = {"3min": 3, "5min": 5}

Key = Tuple[str, str, str]  # (symbol, exchange_segment, timeframe)
InstrumentKey = Tuple[str, str]  # (symbol, exchange_segment)


@dataclass
class _Forming:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


class CandleAggregator:
    """Turns per-tick (symbol, ltp, volume-delta) updates into finalized 1/3/5-min candles.

    Boundary-driven, not count-driven: a 3-/5-min candle is emitted the moment a new
    bucket is observed, using whatever 1-min candles actually landed in the previous
    bucket (even a partial set, e.g. after a late subscribe or a reconnect gap) —
    waiting for exactly N buffered candles would silently misalign after any gap.
    """

    def __init__(self, on_candle_closed: Callable[[str, str, Candle], None]):
        self.on_candle_closed = on_candle_closed
        self._forming: Dict[Key, _Forming] = {}
        self._rollup_buffers: Dict[Key, List[Candle]] = defaultdict(list)
        self._rollup_bucket_start: Dict[Key, datetime] = {}
        self._last_closed_1min: Dict[InstrumentKey, Candle] = {}
        self._lock = threading.RLock()
        self.dropped_late_ticks = 0

    def get_last_closed_1min(self, symbol: str, exchange_segment: str) -> Optional[Candle]:
        """Most recent finalized 1-min candle for this instrument, or None before
        any candle (live or backfilled) has closed yet. Used to compute the
        "change since last candle" tick field."""
        with self._lock:
            return self._last_closed_1min.get((symbol, exchange_segment))

    def ingest_historical_1min(
        self, symbol: str, exchange_segment: str, candle: Candle,
        on_candle_closed: Optional[Callable[[str, str, Candle], None]] = None,
    ) -> None:
        """Feeds an already-complete 1-min candle from historical backfill through
        the same finalize/rollup path a live tick-driven candle takes, so 3-/5-min
        rollups get backfilled correctly too. Candles must arrive in chronological
        order (Dhan's historical API already returns them that way).

        `on_candle_closed`, if given, overrides the instance's default callback
        for just this call — lets a bulk backfill collect every closed candle
        (1-min plus any 3-/5-min rollups it triggers) into a list instead of
        persisting/broadcasting one at a time, then persist them all in one DB
        round-trip. Live ticks (on_tick, below) never pass this — unchanged."""
        with self._lock:
            self._finalize_candle(symbol, exchange_segment, candle, on_candle_closed=on_candle_closed)

    def on_tick(self, symbol: str, exchange_segment: str, ltp: float, volume: int, ts: datetime) -> None:
        with self._lock:
            key: Key = (symbol, exchange_segment, "1min")
            bucket_start = ts.replace(second=0, microsecond=0)
            current = self._forming.get(key)

            if current is None:
                self._forming[key] = _Forming(bucket_start, ltp, ltp, ltp, ltp, volume)
                return

            if bucket_start < current.ts:
                self.dropped_late_ticks += 1
                return

            if bucket_start > current.ts:
                self._finalize_1min(symbol, exchange_segment, current)
                self._forming[key] = _Forming(bucket_start, ltp, ltp, ltp, ltp, volume)
                return

            current.high = max(current.high, ltp)
            current.low = min(current.low, ltp)
            current.close = ltp
            current.volume += volume

    def flush_all(self, as_of: datetime) -> None:
        """Force-finalize every currently-forming candle at all three timeframes (EOD close).

        Without this, the last forming 1-min candle of the day never closes (nothing
        ever arrives to start a new bucket) — and the same is true one level up for
        whatever partial 3-/5-min bucket was still accumulating.
        """
        with self._lock:
            for key, forming in list(self._forming.items()):
                symbol, exchange_segment, timeframe = key
                if timeframe == "1min":
                    self._finalize_1min(symbol, exchange_segment, forming)
                    del self._forming[key]

            for key in list(self._rollup_buffers.keys()):
                symbol, exchange_segment, timeframe = key
                self._emit_rollup(symbol, exchange_segment, timeframe, key)
            self._rollup_bucket_start.clear()

    def _finalize_1min(self, symbol: str, exchange_segment: str, forming: _Forming) -> None:
        candle = Candle(
            symbol=symbol, timeframe="1min", timestamp=forming.ts,
            open=forming.open, high=forming.high, low=forming.low,
            close=forming.close, volume=forming.volume,
        )
        self._finalize_candle(symbol, exchange_segment, candle)

    def _finalize_candle(
        self, symbol: str, exchange_segment: str, candle: Candle,
        on_candle_closed: Optional[Callable[[str, str, Candle], None]] = None,
    ) -> None:
        callback = on_candle_closed or self.on_candle_closed
        self._last_closed_1min[(symbol, exchange_segment)] = candle
        callback(symbol, exchange_segment, candle)
        self._roll_up(symbol, exchange_segment, "3min", candle, on_candle_closed=callback)
        self._roll_up(symbol, exchange_segment, "5min", candle, on_candle_closed=callback)

    def _roll_up(
        self, symbol: str, exchange_segment: str, timeframe: str, one_min: Candle,
        on_candle_closed: Optional[Callable[[str, str, Candle], None]] = None,
    ) -> None:
        n = _ROLLUP_MINUTES[timeframe]
        bucket_start = one_min.timestamp - timedelta(minutes=one_min.timestamp.minute % n)
        key: Key = (symbol, exchange_segment, timeframe)
        current_bucket = self._rollup_bucket_start.get(key)

        if current_bucket is not None and bucket_start != current_bucket:
            self._emit_rollup(symbol, exchange_segment, timeframe, key, on_candle_closed=on_candle_closed)

        self._rollup_bucket_start[key] = bucket_start
        self._rollup_buffers[key].append(one_min)

    def _emit_rollup(
        self, symbol: str, exchange_segment: str, timeframe: str, key: Key,
        on_candle_closed: Optional[Callable[[str, str, Candle], None]] = None,
    ) -> None:
        members = self._rollup_buffers.pop(key, [])
        if not members:
            return
        rolled = Candle(
            symbol=symbol, timeframe=timeframe, timestamp=members[0].timestamp,
            open=members[0].open, high=max(c.high for c in members),
            low=min(c.low for c in members), close=members[-1].close,
            volume=sum(c.volume for c in members),
        )
        callback = on_candle_closed or self.on_candle_closed
        callback(symbol, exchange_segment, rolled)
