"""Runs guiding_scenarios.generate_guiding_scenarios over every active
registered symbol/timeframe, for BOTH lookback windows ("2y" and "3m") —
mines already-analyzed PatternOutcome data (run analyze_pattern_outcomes.py
first if it isn't populated yet) into the small precomputed GuidingScenario
table RS1's live matching reads from.

Not part of the app's normal startup path, and no scheduler is wired up
anywhere (see guiding_scenarios.py's own docstring for why) — this is the
manually-runnable entry point for what a future weekly schedule (an
external cron/Task Scheduler entry, not yet set up) would call. Idempotent:
each run REPLACES the qualifying set for whatever it touches (delete +
bulk-insert per instrument/timeframe/window), so re-running this any number
of times converges on the same result, never accumulates duplicates.

    .venv/Scripts/python.exe backend/scripts/generate_guiding_scenarios.py [SYMBOL ...]

With no arguments, generates for every active registered symbol.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.models import Base, SubscribedSymbol
from db.session import build_engine, build_session_factory
from guiding_scenarios import WINDOW_LOOKBACK_DAYS, generate_guiding_scenarios

TIMEFRAMES = ["1min", "3min", "5min"]


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    Base.metadata.create_all(engine_db)  # idempotent — same convention as every other script here

    requested = set(sys.argv[1:])
    with session_factory() as session:
        rows = session.query(SubscribedSymbol).filter_by(active=True).all()
        targets = [
            (row.id, row.symbol, row.exchange_segment)
            for row in rows
            if not requested or row.symbol in requested
        ]

    if not targets:
        print("No matching active symbols found.")
        return

    total_scenarios = total_indicator_stats = 0
    for instrument_id, symbol, exchange_segment in targets:
        for timeframe in TIMEFRAMES:
            for window_kind in WINDOW_LOOKBACK_DAYS:
                result = generate_guiding_scenarios(session_factory, instrument_id, timeframe, window_kind)
                total_scenarios += result["scenarios_written"]
                total_indicator_stats += result["indicator_stats_written"]
                print(
                    f"{symbol} {timeframe} [{window_kind}]: "
                    f"{result['scenarios_written']} guiding scenario(s) from "
                    f"{result['patterns_scanned']} pattern(s) scanned, "
                    f"{result['indicator_stats_written']} indicator-stat row(s)"
                )

    print(f"\n{total_scenarios} total guiding scenarios, {total_indicator_stats} total indicator-stat rows written")


if __name__ == "__main__":
    main()
