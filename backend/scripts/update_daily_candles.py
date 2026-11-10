"""Daily-candle updater: keeps `candles_daily` current for the daily-update
universe (default: backend/data/daily_universe.json, ~150 stocks).

Each run, per stock (2026-11-10): look up the last stored trade date and
fetch only the newer days, in ONE call (Dhan returns a stock's whole daily
history in one request -- 6,180 RELIANCE candles from 2002 in one call).
Start and end are moved off weekends/NSE holidays (market_calendar), step by
step until a trading day is found:
  - newest day wanted = the last COMPLETED trading day: today once the market
    has closed (>= 15:45 IST), else the trading day before (stepping back);
  - toDate (exclusive at Dhan) = the next trading day after it (stepping on);
  - a stock already up to date makes no call at all.
The call also re-asks for the last stored day: if Dhan's close for it changed,
Dhan has back-adjusted the history (split/bonus), so the stock's whole stored
history is re-fetched in one call and replaced -- otherwise old rows would sit
on the old price scale. A stock with no rows yet gets the last `--years`
years. Missed days are filled on the next run; re-running is harmless.

Token: AlgoTrading's FIXED3 (REST) token from the BrokerToken table (same
source as scripts/fetch_full_history.py). Never printed.
Chart calls are paced by DhanBroker itself (3 s apart).

    python scripts/update_daily_candles.py [--universe FILE] [--years 10] [--symbols A B] [--dry-run]

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
import db.models  # noqa: E402,F401  (first: models.py imports models_daily back)
from db.models_daily import CandleDaily  # noqa: E402
from db.ops import LibCandlesDaily as daily_ops  # noqa: E402
from db.session import session_scope  # noqa: E402
import market_calendar  # noqa: E402

IST = daily_ops.IST
DEFAULT_UNIVERSE = BACKEND / "data" / "daily_universe.json"
MARKET_DONE_IST = dtime(15, 45)


def load_universe(path: Path, only: Optional[List[str]] = None) -> List[dict]:
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    if only:
        items = [i for i in items if i["symbol"] in set(only)]
    return items


def next_trading_day(d: date) -> date:
    """d itself if the market is open that day, else the next day that is."""
    return d if market_calendar.is_trading_day(d) else next_trading_day(d + timedelta(days=1))


def previous_trading_day(d: date) -> date:
    """d itself if the market is open that day, else the last day before it that was."""
    return d if market_calendar.is_trading_day(d) else previous_trading_day(d - timedelta(days=1))


def last_complete_day(now_ist: datetime) -> date:
    """The newest trading day whose daily candle is final."""
    today = now_ist.date()
    if market_calendar.is_trading_day(today) and now_ist.time() >= MARKET_DONE_IST:
        return today
    return previous_trading_day(today - timedelta(days=1))


def plan_range(last_date: Optional[date], now_ist: datetime, years: int) -> Optional[tuple]:
    """(fromDate, newest day wanted, toDate exclusive), or None when the stock
    is already up to date. fromDate is the last stored day (re-asked, to spot
    a back-adjustment) or the first trading day `years` back."""
    newest = last_complete_day(now_ist)
    if last_date is None:
        start = next_trading_day(now_ist.date() - timedelta(days=365 * years))
    else:
        if next_trading_day(last_date + timedelta(days=1)) > newest:
            return None
        start = last_date
    return start, newest, next_trading_day(newest + timedelta(days=1))


def _fetch(broker, item: dict, start: date, to: date, newest: date) -> list:
    try:
        candles = broker.get_historical_data(
            item["symbol"], str(item["security_id"]), item["exchange_segment"], "1day",
            datetime.combine(start, dtime.min, tzinfo=timezone.utc),
            datetime.combine(to, dtime.min, tzinfo=timezone.utc),
        )
    except BrokerAPIError as e:
        # DH-907 = "no data in this range" (e.g. no trading in it for this stock).
        if "DH-907" not in str(e):
            raise
        candles = []
    return [c for c in candles if daily_ops.candle_trade_date(c) <= newest]


def update_symbol(session_factory, broker, item: dict, now_ist: datetime, years: int,
                  dry_run: bool = False) -> dict:
    symbol, segment = item["symbol"], item["exchange_segment"]
    with session_scope(session_factory) as session:
        last = daily_ops.get_last_date(session, symbol, segment)
        stored_close = daily_ops.get_close(session, symbol, segment, last) if last else None
    out = {"symbol": symbol, "last_before": last, "fetched": 0, "changed": 0, "adjusted": False}
    plan = plan_range(last, now_ist, years)
    if plan is None:
        return out  # up to date: no call
    start, newest, to = plan
    candles = _fetch(broker, item, start, to, newest)
    if last is not None:
        again = next((c for c in candles if daily_ops.candle_trade_date(c) == last), None)
        if again is not None and stored_close is not None and round(again.close, 2) != round(stored_close, 2):
            # back-adjusted by Dhan (split/bonus): replace the stock's whole history
            with session_scope(session_factory) as session:
                first = daily_ops.get_first_date(session, symbol, segment)
            candles = _fetch(broker, item, first, to, newest)
            out["adjusted"] = True
        else:
            candles = [c for c in candles if daily_ops.candle_trade_date(c) > last]
    out["fetched"] = len(candles)
    if candles and not dry_run:
        out["changed"] = daily_ops.upsert_candles(session_factory, symbol, segment, candles)
    if last is None and not candles:
        raise RuntimeError("no candles returned for a stock with no stored history")
    return out


def run(session_factory, broker, universe: List[dict], now_ist: datetime, years: int = 10,
        dry_run: bool = False, log: Callable[[str], None] = print) -> dict:
    ok = failed = rows_changed = 0
    for n, item in enumerate(universe, 1):
        try:
            r = update_symbol(session_factory, broker, item, now_ist, years, dry_run)
            ok += 1
            rows_changed += r["changed"]
            note = " -- prices back-adjusted by Dhan, whole history replaced" if r["adjusted"] else ""
            log(f"[{n}/{len(universe)}] {r['symbol']}: last was {r['last_before']}, "
                f"fetched {r['fetched']}, changed {r['changed']}{note}")
        except Exception as e:  # one bad stock must not stop the rest
            failed += 1
            log(f"[{n}/{len(universe)}] {item['symbol']}: FAILED {type(e).__name__}: {e}")
    log(f"done: {ok} ok, {failed} failed, {rows_changed} rows written")
    return {"ok": ok, "failed": failed, "rows_changed": rows_changed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    ap.add_argument("--years", type=int, default=10, help="history for a stock with no rows yet")
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
