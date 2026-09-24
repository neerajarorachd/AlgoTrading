"""Replays today's already-stored candles_today rows through ActivityEngine and
recommendation_engine.on_activities, in chronological order from market open,
so recommendations that would have fired live since 09:15 today get generated
retroactively — lets the Recommendations queue reflect the full trading day
instead of only signals detected from whenever this process last (re)started.

Mirrors replay_activity_engine.py's shape, but calls recommendation_engine.
on_activities (the current, guiding-scenario-based system) instead of the
older PredictionTracker — see recommendation_queue_rs1_design for why RS1/
guiding-scenarios superseded PredictionTracker as the live decision path.

Not part of the app's normal startup path; run manually:

    .venv/Scripts/python.exe backend/scripts/replay_recommendations.py [SYMBOL ...]

With no arguments, replays every active registered symbol across all three
timeframes (1min/3min/5min). Safe to re-run: activity_engine's own duplicate-
detection (by symbol/timeframe/pattern/ts) prevents double-counting activities,
and match_guiding_scenario's lookup is a pure read — the only write this
script performs beyond activities is queuing new Recommendation rows, which
only happens for activities that haven't already been matched.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine, seed_pattern_definitions
from brokers.models import Candle
from db.models import Base, CandleToday, SubscribedSymbol
from db.session import build_engine, build_session_factory
from recommendation_engine import on_activities as recommendation_on_activities

TIMEFRAMES = ["1min", "3min", "5min"]


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    Base.metadata.create_all(engine_db)
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
    total_queued = 0

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

            queued_here = 0
            for candle in candles:
                new_activities = activity_engine.on_candle_closed(symbol, exchange_segment, candle)
                queued = recommendation_on_activities(session_factory, symbol, exchange_segment, new_activities)
                queued_here += len(queued)

            total_queued += queued_here
            print(f"{symbol} {timeframe}: replayed {len(candles)} candles, {queued_here} recommendation(s) queued")

    flushed = activity_engine.flush()  # one bulk write for the whole run, not one per detection
    print(f"\nflushed {flushed} buffered activities to instrument_activity in one write")
    print(f"total recommendations queued: {total_queued}")


if __name__ == "__main__":
    main()
