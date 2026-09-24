"""Populates instrument_activity from candles_historical for one or more
symbols, across 1min/3min/5min — the prerequisite step analyze_pattern_
outcomes.py's own docstring calls out ("Requires instrument_activity to
already be populated"). replay_activity_engine.py doesn't do this (it reads
candles_today, today-only); this reads the persistent multi-day
CandleHistorical archive instead, same as backtest_historical.py's own
replay().

Deliberately does NOT reuse replay() as-is, for two real reasons found live
2026-09-17:
  1. A full 2-year 1-min replay buffers ~340,000 activity rows (every
     detector firing across every candle) before its one single flush() at
     the very end — flushing every FLUSH_EVERY candles instead keeps each
     bulk insert a manageable size and gives real incremental progress.
  2. replay() also drives PredictionTracker, which this script's own goal
     (populate instrument_activity for pattern_outcome_analysis.py) never
     needs — analyze_instrument only ever reads InstrumentActivity, never
     PatternPrediction. Worse, PredictionTracker's own ~60s wall-clock
     auto-flush (_maybe_flush) hit the EXACT already-documented "ORM/pyodbc
     bulk-batch slowdown" order_backtest.py's own replay() comment warns
     about (tens of thousands of rows via bulk_update_mappings/insert_bulk
     taking minutes even with fast_executemany=True) — confirmed live: with
     PredictionTracker running, 20,000 candles took 157s (should be ~4s);
     removing it entirely restored the clean, linear ~0.2ms/candle rate a
     direct ActivityEngine-only benchmark had already shown. Not a VM/disk
     problem — a known SQLAlchemy/pyodbc large-batch cost this script now
     simply never triggers, since it never creates a PatternPrediction row.

Run manually:
    .venv/Scripts/python.exe backend/scripts/build_historical_activities.py [SYMBOL ...]

With no arguments, replays every active registered symbol. Covers the last
730 days (matching guiding_scenarios's own "2y" window — nothing downstream
needs more).
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine, seed_pattern_definitions
from brokers.models import Candle
from db.models import Base, SubscribedSymbol
from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory

TIMEFRAMES = ["1min", "3min", "5min"]
LOOKBACK_DAYS = 730
# Row-count based (buffered activities), not candle-count — activities fire
# multiple times per candle (~1.8x observed), so a candle-count interval
# doesn't actually bound the bulk INSERT size the way it looks like it
# does. Smaller, more frequent flushes each wait less time for a scheduler
# slot on SQL Server Express's small, shared worker pool (explicit
# instruction, 2026-09-17, after identifying flush() itself — not the
# per-candle loop — as the actual contention point).
FLUSH_EVERY_ROWS = 2_000


def replay_with_periodic_flush(
    session_factory, symbol: str, exchange_segment: str, timeframe: str,
    start: datetime, end: datetime,
) -> None:
    with session_factory() as session:
        rows = LibCandlesHistorical.get_range(session, symbol, exchange_segment, timeframe, start, end)
    candles = [
        Candle(symbol=symbol, timeframe=timeframe, timestamp=r.ts,
               open=float(r.open_price), high=float(r.high_price),
               low=float(r.low_price), close=float(r.close_price), volume=r.volume)
        for r in rows
    ]
    print(f"  {timeframe}: {len(candles)} candles", flush=True)
    if not candles:
        return

    engine = ActivityEngine(session_factory)
    t0 = time.monotonic()
    total_flushed = 0

    for i, candle in enumerate(candles):
        engine.on_candle_closed(symbol, exchange_segment, candle)
        if engine.buffered_count() >= FLUSH_EVERY_ROWS:
            total_flushed += engine.flush()
            if (i + 1) % 20_000 == 0:  # progress print stays candle-paced, not flush-paced
                print(f"    {i+1}/{len(candles)} ({time.monotonic()-t0:.0f}s elapsed, "
                      f"{total_flushed} activities flushed so far)", flush=True)

    total_flushed += engine.flush()
    print(f"    done: {time.monotonic()-t0:.0f}s total, {total_flushed} total activities", flush=True)


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    Base.metadata.create_all(engine_db)
    seed_pattern_definitions(session_factory)

    requested = set(sys.argv[1:])
    with session_factory() as session:
        rows = session.query(SubscribedSymbol).filter_by(active=True).order_by(SubscribedSymbol.symbol).all()
        targets = [row for row in rows if not requested or row.symbol in requested]

    if not targets:
        print("No matching active symbols found.")
        return

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    for row in targets:
        print(f"\n=== {row.symbol} ===", flush=True)
        for timeframe in TIMEFRAMES:
            replay_with_periodic_flush(session_factory, row.symbol, row.exchange_segment, timeframe, start, end)

    print("\nDone.")


if __name__ == "__main__":
    main()
