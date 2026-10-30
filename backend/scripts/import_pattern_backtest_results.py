"""Load pattern backtest results (a JSON list of PatternBacktestResult rows)
into pattern_backtest_results, and copy each "set" row's numbers onto its
Pattern (win_rate / avg_move_pct / sample_size / last_backtested_at).
Seeds the system patterns first, so a just-added catalog pattern gets its
id. Uses DB_CONNECTION_STRING (.env), like every other script here.

    .venv/Scripts/python.exe backend/scripts/import_pattern_backtest_results.py --file results.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.models import Base
from db.ops import LibPatternBacktests, LibPatterns
from db.session import build_engine, build_session_factory
from patterns_catalog import SYSTEM_PATTERNS


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, help="JSON list of result rows")
    ap.add_argument("--no-pattern-stats", action="store_true", help="don't copy set rows onto the Pattern")
    args = ap.parse_args(argv)
    rows = json.loads(Path(args.file).read_text(encoding="utf-8"))
    engine = build_engine()
    Base.metadata.create_all(engine)  # creates pattern_backtest_results if it is new
    factory = build_session_factory(engine)
    LibPatterns.seed_system_patterns(factory, SYSTEM_PATTERNS)
    saved = LibPatternBacktests.save_run(factory, rows, update_pattern_stats=not args.no_pattern_stats)
    print(f"saved {saved} rows ({sum(r['scope'] == 'set' for r in rows)} set, "
          f"{sum(r['scope'] == 'stock' for r in rows)} per-stock)")


if __name__ == "__main__":
    main()
