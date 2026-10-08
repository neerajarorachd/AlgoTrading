"""TEMPORARY replay "live" feed (2026-10-07): with no usable live data, plays
one recorded NSE session back through the real pipeline as if it were live
-- user's words: "read 1 min candles from yesterday's data... should behave
like its a live data ticker. fetch 15 minutes data (to speedup) every
minute, generate 1,3,5 min candles. generate on go the list of generated
patterns and indicators".

Each 1-min candle goes through CandleAggregator.ingest_historical_1min with
the app's own on_candle_closed callback -- the exact path a live 1-min
candle takes: candle persisted, 3-/5-min rollups, ActivityEngine (patterns +
indicator snapshots), recommendation pipeline, `candle_closed` and the new
`activity` WebSocket events. A tick per symbol per minute (built by a real
MarketFeed, so LTP/Chg/Gap/Day/Candle are computed exactly as live) updates
Market Watch. Timestamps are shifted to TODAY so every "today" endpoint and
screen treats the replay as the current session.

Enabled only by the REPLAY_FILE env var (see app.py), built by
scripts/build_replay_day.py. Refuses to run against anything but SQLite --
the replay writes candles/activities into the database it runs on, and must
never touch the live SQL Server (memory: test_env_separation_rule).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from brokers.models import Candle
from db.models import CandleHistorical, CandleIndicators, CandleToday, InstrumentActivity, SubscribedSymbol
from market_feed import MarketFeed

import api.ws_live as ws_live

logger = logging.getLogger(__name__)

IST = timedelta(hours=5, minutes=30)
DEFAULT_BATCH_MINUTES = 15
DEFAULT_INTERVAL_SEC = 60.0


class _NoBroker:
    """MarketFeed wants a broker to subscribe through -- the replay has none."""
    def connect(self): pass
    def disconnect(self): pass
    def subscribe_feed(self, instruments, on_tick): pass
    def unsubscribe_feed(self, instruments): pass


def _ist_today() -> date:
    return (datetime.now(timezone.utc) + IST).date()


class ReplayFeed:
    def __init__(self, day_file, db_engine, session_factory, aggregator, activity_engine,
                 batch_minutes: Optional[int] = None, interval_sec: Optional[float] = None,
                 today: Optional[date] = None, sleep: Callable[[float], None] = time.sleep,
                 broadcast_tick: Callable[[dict], None] = ws_live.broadcast_tick):
        if db_engine.dialect.name != "sqlite":
            raise RuntimeError(
                f"replay refuses to run against a {db_engine.dialect.name} database -- it writes replayed "
                "candles/activities, and must only ever run on a local SQLite test DB, never the live one")
        self.session_factory = session_factory
        self.aggregator = aggregator
        self.activity_engine = activity_engine
        self.batch_minutes = batch_minutes or int(os.environ.get("REPLAY_BATCH_MINUTES", DEFAULT_BATCH_MINUTES))
        self.interval_sec = interval_sec if interval_sec is not None else float(
            os.environ.get("REPLAY_INTERVAL_SEC", DEFAULT_INTERVAL_SEC))
        self._sleep = sleep

        data = json.loads(Path(day_file).read_text(encoding="utf-8"))
        self.replay_date = date.fromisoformat(data["date"])
        self.shown_as = today or _ist_today()
        self._shift = timedelta(days=(self.shown_as - self.replay_date).days)
        self.symbols: Dict[str, dict] = data["symbols"]

        # every distinct minute of the session, each with the candles that close in it
        by_minute: Dict[datetime, List[Tuple[str, list]]] = {}
        for sym, info in self.symbols.items():
            for c in info["candles"]:
                ts = datetime.fromtimestamp(c[0], timezone.utc) + self._shift
                by_minute.setdefault(ts, []).append((sym, c))
        self._minutes = sorted(by_minute.items())
        self._next = 0
        self._state = "ready"
        self._thread: Optional[threading.Thread] = None

        self._market_feed = MarketFeed(_NoBroker(), on_tick=broadcast_tick,
                                       candle_lookup=aggregator.get_last_closed_1min)

    # ------------------------------------------------------------ setup

    def prepare(self) -> None:
        """Registers the replay's symbols, seeds the previous session's daily
        candle (pivot points), and clears anything left in today's tables by
        an earlier replay run, so each run starts from a clean session."""
        day_start = datetime.combine(self.shown_as, datetime.min.time()) - IST
        with self.session_factory() as session:
            for sym, info in self.symbols.items():
                row = session.query(SubscribedSymbol).filter_by(symbol=sym, exchange=info["exchange"],
                                                                 segment=info["segment"]).one_or_none()
                if row is None:
                    row = SubscribedSymbol(symbol=sym, exchange=info["exchange"], segment=info["segment"],
                                           exchange_segment=info["exchange_segment"], security_id=info["security_id"])
                    session.add(row)
                row.active, row.removed_at = True, None
                row.previous_close = info["previous_close"]
                session.flush()

                seg = info["exchange_segment"]
                session.query(CandleToday).filter(CandleToday.symbol == sym, CandleToday.ts >= day_start).delete()
                session.query(CandleIndicators).filter(CandleIndicators.instrument_id == row.id,
                                                       CandleIndicators.ts >= day_start).delete()
                session.query(InstrumentActivity).filter(InstrumentActivity.instrument_id == row.id,
                                                         InstrumentActivity.ts >= day_start).delete()

                prev_ts = datetime.combine(date.fromisoformat(info["previous_session"]), datetime.min.time()).replace(
                    hour=3, minute=45, tzinfo=timezone.utc) + self._shift
                session.query(CandleHistorical).filter_by(symbol=sym, exchange_segment=seg, timeframe="1day").filter(
                    CandleHistorical.ts >= prev_ts - timedelta(days=1)).delete()
                session.add(CandleHistorical(
                    symbol=sym, exchange_segment=seg, timeframe="1day", ts=prev_ts,
                    open_price=info["previous_close"], high_price=info["previous_high"],
                    low_price=info["previous_low"], close_price=info["previous_close"], volume=0,
                ))
            session.commit()

        first_open = {}
        for _, rows in self._minutes:
            for sym, c in rows:
                first_open.setdefault(sym, c[1])
        for sym, info in self.symbols.items():
            self._market_feed.subscribe({
                "symbol": sym, "exchange": info["exchange"], "segment": info["segment"],
                "exchange_segment": info["exchange_segment"], "security_id": info["security_id"],
                "previous_close": info["previous_close"], "open": first_open.get(sym),
            })

    # ------------------------------------------------------------ playback

    def emit_next_batch(self) -> int:
        """Plays the next `batch_minutes` minutes. Returns how many were played
        (0 once the session is over). Flushes the engine after each batch --
        replay only -- so every DB-reading screen sees the replayed patterns
        immediately instead of at a 15:30 flush."""
        batch = self._minutes[self._next:self._next + self.batch_minutes]
        for ts, rows in batch:
            for sym, c in rows:
                info = self.symbols[sym]
                candle = Candle(symbol=sym, timeframe="1min", timestamp=ts,
                                open=c[1], high=c[2], low=c[3], close=c[4], volume=int(c[5]))
                self.aggregator.ingest_historical_1min(sym, info["exchange_segment"], candle)
                self._market_feed._on_broker_tick({
                    "SecurityId": info["security_id"], "ExchangeSegment": info["exchange_segment"],
                    "LTP": c[4], "PreviousClose": info["previous_close"],
                    "Timestamp": ts + timedelta(minutes=1),
                })
        self._next += len(batch)
        if batch:
            self.activity_engine.flush()
        return len(batch)

    def start(self) -> None:
        if self._thread is not None:
            return

        def _run():
            try:
                self.prepare()
                self._state = "running"
                logger.info("Replay: %s shown as %s, %d minutes at %d per %.0fs",
                            self.replay_date, self.shown_as, len(self._minutes), self.batch_minutes, self.interval_sec)
                while self.emit_next_batch():
                    if self._next < len(self._minutes):
                        self._sleep(self.interval_sec)
                self._state = "finished"
                logger.info("Replay: finished %s", self.replay_date)
            except Exception:
                self._state = "failed"
                logger.exception("Replay: failed")

        self._thread = threading.Thread(target=_run, daemon=True, name="replay-feed")
        self._thread.start()

    def status(self) -> dict:
        played = self._minutes[self._next - 1][0] if self._next else None
        return {
            "available": True,
            "mode": "replay",
            "state": self._state,
            "connected": self._state == "running",
            "replay_date": self.replay_date.isoformat(),
            "shown_as_date": self.shown_as.isoformat(),
            "sim_time": (played + timedelta(minutes=1) + IST).strftime("%H:%M") if played else None,
            "minutes_played": self._next,
            "minutes_total": len(self._minutes),
            "batch_minutes": self.batch_minutes,
            "interval_sec": self.interval_sec,
            "subscribed": len(self.symbols),
        }
