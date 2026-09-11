"""Replay today's already-backfilled candles_today rows through ActivityEngine,
in chronological order from market open, as if they were arriving live —
lets the pattern-detection engine be exercised against a full real trading
day without waiting for the market to reopen. Not part of the app's normal
startup path; run manually:

    .venv/Scripts/python.exe backend/scripts/replay_activity_engine.py [SYMBOL ...]

With no arguments, replays every active registered symbol.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine, seed_pattern_definitions
from brokers.models import Candle
from db.models import CandleToday, InstrumentActivity, SubscribedSymbol
from db.session import build_engine, build_session_factory

TIMEFRAMES = ["1min", "3min", "5min"]


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    seed_pattern_definitions(session_factory)

    requested = set(sys.argv[1:])
    with session_factory() as session:
        rows = session.query(SubscribedSymbol).filter_by(active=True).all()
        targets = [
            (row.symbol, row.exchange_segment)
            for row in rows
            if not requested or row.symbol in requested
        ]

    if not targets:
        print("No matching active symbols found.")
        return

    activity_engine = ActivityEngine(session_factory)

    for symbol, exchange_segment in targets:
        for timeframe in TIMEFRAMES:
            with session_factory() as session:
                candle_rows = (
                    session.query(CandleToday)
                    .filter_by(symbol=symbol, exchange_segment=exchange_segment, timeframe=timeframe)
                    .order_by(CandleToday.ts.asc())
                    .all()
                )
                candles = [
                    Candle(
                        symbol=symbol, timeframe=timeframe, timestamp=r.ts,
                        open=float(r.open_price), high=float(r.high_price),
                        low=float(r.low_price), close=float(r.close_price), volume=r.volume,
                    )
                    for r in candle_rows
                ]

            for candle in candles:
                activity_engine.on_candle_closed(symbol, exchange_segment, candle)

            print(f"{symbol} {timeframe}: replayed {len(candles)} candles ({activity_engine.buffered_count()} buffered so far)")

    flushed = activity_engine.flush()  # one bulk write for the whole run, not one per detection
    print(f"\nflushed {flushed} buffered activities to instrument_activity in one write")

    with session_factory() as session:
        rows = (
            session.query(InstrumentActivity)
            .join(SubscribedSymbol, InstrumentActivity.instrument_id == SubscribedSymbol.id)
            .filter(SubscribedSymbol.symbol.in_([s for s, _ in targets]))
            .order_by(InstrumentActivity.ts.asc())
            .all()
        )
        print(f"\n{len(rows)} total activities detected:")
        for row in rows:
            print(f"  {row.ts} [{row.timeframe}] instrument={row.instrument_id} {row.activity_type}/{row.activity}")


if __name__ == "__main__":
    main()
