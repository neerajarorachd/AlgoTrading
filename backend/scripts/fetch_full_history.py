"""Full historical candle backfill for one or more symbols — the same
"clean refetch" HINDCOPPER got on 2026-09-15, generalized into a reusable
script instead of one-off code. Fetches 1min/3min/5min/1day (this project's
own standing convention — see historical_data_fetch_convention memory: the
full timeframe set, not just what the immediate task needs), covering the
last 730 days (matching guiding_scenarios.WINDOW_LOOKBACK_DAYS's "2y" window
— nothing downstream can use more history than this fetches).

Uses historical_data_service.ensure_data_available directly (the same
fetch-if-stale/coverage-tracked path routes_historical_data.py's REST
endpoint calls), against a standalone REST DhanBroker built from the
DB-mirrored FIXED3 token — not the live app's own broker instance, so this
doesn't compete with live trading's REST rate-limit budget while it runs.

Dhan's /charts endpoint is paced at 3s between requests (dhan_broker.py's
own MIN_CHART_REQUEST_INTERVAL_SEC) — 12 symbols x 3 intraday timeframes x
~8 90-day chunks alone is ~290 calls, so this is a genuinely long-running
script (15+ minutes), expected to run via run_in_background, not foreground.

Run manually:
    .venv/Scripts/python.exe backend/scripts/fetch_full_history.py [SYMBOL ...]

With no arguments, fetches every active symbol that doesn't already have
HINDCOPPER-scale 1min coverage (a cheap heuristic, not exact).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from config import DHAN_TOKEN_TYPE_REST, load_dhan_tokens
from db.models import SubscribedSymbol
from db.session import build_engine, build_session_factory
from historical_data_service import ensure_data_available
import os

TIMEFRAMES = ["1min", "3min", "5min", "1day"]
LOOKBACK_DAYS = 730


def main() -> None:
    engine = build_engine()
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        tokens = load_dhan_tokens(session)
        requested = set(sys.argv[1:])
        rows = session.query(SubscribedSymbol).filter_by(active=True).order_by(SubscribedSymbol.symbol).all()
        targets = [row for row in rows if not requested or row.symbol in requested]

    if not targets:
        print("No matching active symbols found.")
        return

    from brokers.dhan_broker import DhanBroker
    rest_broker = DhanBroker(
        client_id=os.environ.get("DHAN_CLIENT_ID", ""),
        access_token=tokens.get(DHAN_TOKEN_TYPE_REST, os.environ.get("DHAN_ACCESS_TOKEN", "")),
    )

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=LOOKBACK_DAYS)

    for row in targets:
        print(f"\n=== {row.symbol} ({row.exchange_segment}) ===", flush=True)
        for timeframe in TIMEFRAMES:
            try:
                summary = ensure_data_available(
                    session_factory, rest_broker, row.symbol, row.security_id,
                    row.exchange_segment, timeframe, start_date, end_date,
                )
                status = "already covered" if summary["already_covered"] else f"fetched {summary['fetched']} candles"
                print(f"  {timeframe:<6} {status}", flush=True)
            except Exception as e:
                print(f"  {timeframe:<6} FAILED: {e}", flush=True)

    print("\nDone.")


if __name__ == "__main__":
    main()
