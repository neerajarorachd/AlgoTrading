"""Stores past days' indicator rows and patterns for the chart, for days
that lack them (feed/chart_history_fill.py). The 15:30 EOD pass does this
daily; this runs it by hand.

    .venv/Scripts/python.exe backend/scripts/fill_chart_history.py [SYMBOL ...]

With no arguments, every active stock. Uses DB_CONNECTION_STRING from .env
(or the environment) -- run it outside market hours against the live DB.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.ops.LibSymbols import get_active
from db.session import build_engine, build_session_factory, session_scope
from feed.chart_history_fill import fill_all


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    session_factory = build_session_factory(build_engine())
    requested = set(sys.argv[1:])
    with session_scope(session_factory) as session:
        targets = [(r.symbol, r.exchange_segment) for r in get_active(session)
                   if not requested or r.symbol in requested]
    if not targets:
        print("No matching active symbols found.")
        return
    fill_all(session_factory, targets)


if __name__ == "__main__":
    main()
