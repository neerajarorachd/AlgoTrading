"""Runs pattern_outcome_analysis.analyze_instrument over already-detected
activities, for every 1/3/5-min timeframe. Requires instrument_activity to
already be populated (run replay_activity_engine.py first if it isn't).
Not part of the app's normal startup path; run manually:

    .venv/Scripts/python.exe backend/scripts/analyze_pattern_outcomes.py [SYMBOL ...]

With no arguments, analyzes every active registered symbol.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.models import Base, PatternOutcome, SubscribedSymbol
from db.ops import LibSymbols
from db.session import build_engine, build_session_factory
from pattern_outcome_analysis import analyze_instrument

TIMEFRAMES = ["1min", "3min", "5min"]


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    # create_all is idempotent — matches replay_activity_engine.py's own
    # unconditional call, so this stays usable right after PatternOutcome
    # was added without needing the live app to run first
    Base.metadata.create_all(engine_db)

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

    total_written = 0
    for symbol, exchange_segment in targets:
        for timeframe in TIMEFRAMES:
            written = analyze_instrument(session_factory, symbol, exchange_segment, timeframe)
            total_written += written
            print(f"{symbol} {timeframe}: {written} new outcome row(s)")

    print(f"\n{total_written} total new pattern_outcomes rows written")

    with session_factory() as session:
        instrument_ids = [
            LibSymbols.get_instrument_id(session, symbol, exchange_segment)
            for symbol, exchange_segment in targets
        ]
        rows = (
            session.query(PatternOutcome)
            .filter(PatternOutcome.instrument_id.in_([i for i in instrument_ids if i is not None]))
            .all()
        )

    by_pattern = Counter(row.pattern for row in rows)
    print(f"\n{len(rows)} total outcome rows across the requested symbols, by pattern:")
    for pattern, count in sorted(by_pattern.items(), key=lambda kv: -kv[1]):
        matching = [r for r in rows if r.pattern == pattern and r.pct_change_20 is not None]
        if matching:
            avg_20 = sum(float(r.pct_change_20) for r in matching) / len(matching)
            print(f"  {pattern}: {count} occurrence(s), avg pct_change_20 = {avg_20:+.3%} (n={len(matching)})")
        else:
            print(f"  {pattern}: {count} occurrence(s), no 20-candle-complete window yet")


if __name__ == "__main__":
    main()
