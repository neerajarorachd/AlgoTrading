"""Daily-candle updater: keeps `candles_daily` current for the daily-update
universe (default: backend/data/daily_universe.json, ~150 stocks).

Each run, per stock: look up the last stored trade date, fetch from that date
(re-fetching it replaces a partial candle) up to today, and upsert. A stock
with no rows yet gets the last `--years` years. A missed day or several is
therefore filled automatically on the next run, and re-running is harmless.

Today's candle is only kept once the market has closed (>= 15:45 IST), so a
mid-session manual run never stores a half-finished day.

Token: AlgoTrading's FIXED3 (REST) token from the BrokerToken table (same
source as scripts/fetch_full_history.py). Never printed.
Chart calls are paced by DhanBroker itself (3 s apart).

    python scripts/update_daily_candles.py [--universe FILE] [--years 2] [--symbols A B] [--dry-run]

Exit code 0 if every stock succeeded, 1 if any failed (systemd shows failure).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from brokers.models import BrokerAPIError  # noqa: E402
from db.models_daily import CandleDaily  # noqa: E402
from db.ops import LibCandlesDaily as daily_ops  # noqa: E402
from db.session import session_scope  # noqa: E402

IST = daily_ops.IST
DEFAULT_UNIVERSE = BACKEND / "data" / "daily_universe.json"
CHUNK_DAYS = 365  # Dhan's daily endpoint takes about a year per call
MARKET_DONE_IST = dtime(15, 45)


def load_universe(path: Path, only: Optional[List[str]] = None) -> List[dict]:
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    if only:
        items = [i for i in items if i["symbol"] in set(only)]
    return items


def plan_range(last_date: Optional[date], today: date, years: int) -> tuple:
    """(from, to) dates to request. `to` is today; `from` is the last stored
    day (re-fetched) or `years` back for a stock with no data."""
    start = last_date if last_date is not None else today - timedelta(days=365 * years)
    return start, today


def update_symbol(session_factory, broker, item: dict, now_ist: datetime, years: int,
                  dry_run: bool = False) -> dict:
    symbol, segment = item["symbol"], item["exchange_segment"]
    with session_scope(session_factory) as session:
        last = daily_ops.get_last_date(session, symbol, segment)
    start, end = plan_range(last, now_ist.date(), years)
    keep_today = now_ist.time() >= MARKET_DONE_IST

    fetched = changed = 0
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        # Dhan's toDate is exclusive for daily data: ask one day past.
        try:
            candles = broker.get_historical_data(
                symbol, str(item["security_id"]), segment, "1day",
                datetime.combine(cur, dtime.min, tzinfo=timezone.utc),
                datetime.combine(chunk_end + timedelta(days=1), dtime.min, tzinfo=timezone.utc),
            )
        except BrokerAPIError as e:
            # DH-907 = "no data in this range" (e.g. a weekend/holiday-only span).
            if "DH-907" not in str(e):
                raise
            candles = []
        candles = [
            c for c in candles
            if daily_ops.candle_trade_date(c) <= now_ist.date()
            and (keep_today or daily_ops.candle_trade_date(c) < now_ist.date())
        ]
        fetched += len(candles)
        if candles and not dry_run:
            changed += daily_ops.upsert_candles(session_factory, symbol, segment, candles)
        cur = chunk_end + timedelta(days=1)
    if last is None and fetched == 0:
        raise RuntimeError("no candles returned for a stock with no stored history")
    return {"symbol": symbol, "last_before": last, "fetched": fetched, "changed": changed}


def run(session_factory, broker, universe: List[dict], now_ist: datetime, years: int = 2,
        dry_run: bool = False, log: Callable[[str], None] = print) -> dict:
    ok = failed = rows_changed = 0
    for n, item in enumerate(universe, 1):
        try:
            r = update_symbol(session_factory, broker, item, now_ist, years, dry_run)
            ok += 1
            rows_changed += r["changed"]
            log(f"[{n}/{len(universe)}] {r['symbol']}: last was {r['last_before']}, "
                f"fetched {r['fetched']}, changed {r['changed']}")
        except Exception as e:  # one bad stock must not stop the rest
            failed += 1
            log(f"[{n}/{len(universe)}] {item['symbol']}: FAILED {type(e).__name__}: {e}")
    log(f"done: {ok} ok, {failed} failed, {rows_changed} rows written")
    return {"ok": ok, "failed": failed, "rows_changed": rows_changed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    ap.add_argument("--years", type=int, default=2)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--dry-run", action="store_true", help="fetch but do not write")
    a = ap.parse_args()

    from dotenv import load_dotenv
    for env in (BACKEND.parent / ".env", BACKEND / ".env"):
        load_dotenv(env)

    from brokers.dhan_broker import DhanBroker
    from config import DHAN_TOKEN_TYPE_REST, load_dhan_tokens
    from db.models import Base
    from db.session import build_engine, build_session_factory

    engine = build_engine()
    Base.metadata.create_all(engine, tables=[CandleDaily.__table__])  # idempotent
    session_factory = build_session_factory(engine)
    with session_factory() as session:
        token = load_dhan_tokens(session).get(DHAN_TOKEN_TYPE_REST)
    if not token:
        print("no active FIXED3 Dhan token in BrokerToken", flush=True)
        return 1

    broker = DhanBroker(os.environ["DHAN_CLIENT_ID"], token)
    universe = load_universe(Path(a.universe), a.symbols)
    now_ist = datetime.now(IST)
    print(f"{now_ist:%Y-%m-%d %H:%M} IST: updating {len(universe)} stocks", flush=True)
    result = run(session_factory, broker, universe, now_ist, a.years, a.dry_run,
                 log=lambda m: print(m, flush=True))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
