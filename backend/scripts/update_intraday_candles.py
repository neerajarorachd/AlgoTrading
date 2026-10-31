"""Intraday candle updater: keeps 1/3/5-min candles in `candles_historical`
current, each timeframe judged by ITS OWN last stored candle per stock.

Per stock in the universe (default backend/data/daily_universe.json):
  * stock already has 1-min data (the sample stocks): fetch 1-min from the
    last stored 1-min day; build 3-min and 5-min by rolling up the stored
    1-min, each starting after that timeframe's own last candle (rule book:
    3/5-min are rolled up from 1-min, never fetched directly);
  * stock has no 1-min data (the rest of the universe): fetch 5-min directly
    from the last stored 5-min day. They get the full set when added to a
    watchlist, after which this script maintains them like the others.

A missed day or several is filled on the next run. Insert-only: stored
candles are never changed. Today's candles are kept only after the close
(>= 15:45 IST). Coverage (HistoricalDataCoverage) is widened so backtests
don't re-fetch what is here. Chart calls are paced by DhanBroker (3 s).

    python scripts/update_intraday_candles.py [--universe FILE] [--symbols A B] [--dry-run]

Exit code 0 if every stock succeeded, 1 if any failed.
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
from db.models import CandleHistorical  # noqa: E402
from db.ops import LibCandlesHistoricalSync as sync  # noqa: E402
from db.session import session_scope  # noqa: E402

IST = sync.IST
DEFAULT_UNIVERSE = BACKEND / "data" / "daily_universe.json"
CHUNK_DAYS = 90  # Dhan intraday range per call
MARKET_DONE_IST = dtime(15, 45)
ROLLUP_MINUTES = {"3min": 3, "5min": 5}
INITIAL_DAYS = 730


def load_universe(path: Path, only: Optional[List[str]] = None) -> List[dict]:
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    return [i for i in items if not only or i["symbol"] in set(only)]


def _row(c) -> dict:
    return {"ts": sync.naive_utc(c.timestamp), "open": c.open, "high": c.high,
            "low": c.low, "close": c.close, "volume": c.volume}


def _complete(ts, now_ist: datetime) -> bool:
    day = sync.ist_date(ts)
    return day < now_ist.date() or (day == now_ist.date() and now_ist.time() >= MARKET_DONE_IST)


def fetch_direct(broker, item: dict, timeframe: str, start: date, now_ist: datetime) -> List[dict]:
    """Candles of `timeframe` from `start` (re-fetching that day) to today, in
    90-day chunks. Dhan's toDate is exclusive: ask one day past."""
    rows: List[dict] = []
    cur, end = start, now_ist.date()
    while cur <= end:
        chunk_end = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        try:
            candles = broker.get_historical_data(
                item["symbol"], str(item["security_id"]), item["exchange_segment"], timeframe,
                datetime.combine(cur, dtime.min, tzinfo=timezone.utc),
                datetime.combine(chunk_end + timedelta(days=1), dtime.min, tzinfo=timezone.utc),
            )
        except BrokerAPIError as e:
            if "DH-907" not in str(e):  # DH-907 = no data in this span (weekend/holiday)
                raise
            candles = []
        rows += [_row(c) for c in candles]
        cur = chunk_end + timedelta(days=1)
    return [r for r in rows if _complete(r["ts"], now_ist)]


def _start_date(last: Optional[datetime], now_ist: datetime) -> date:
    return sync.ist_date(last) if last is not None else now_ist.date() - timedelta(days=INITIAL_DAYS)


def rollup(one_min: List[dict], minutes: int) -> List[dict]:
    """1-min rows -> n-min rows. Same bucketing as feed/candle_aggregator.py and
    scripts/rollup_historical_candles.py (UTC minute floored to n; IST is +5:30,
    a multiple of 3 and 5 minutes, so buckets align in IST too)."""
    buckets: dict = {}
    for r in sorted(one_min, key=lambda x: x["ts"]):
        key = r["ts"] - timedelta(minutes=r["ts"].minute % minutes, seconds=r["ts"].second,
                                  microseconds=r["ts"].microsecond)
        b = buckets.get(key)
        if b is None:
            buckets[key] = {"ts": key, "open": r["open"], "high": r["high"], "low": r["low"],
                            "close": r["close"], "volume": r["volume"]}
        else:
            b["high"], b["low"] = max(b["high"], r["high"]), min(b["low"], r["low"])
            b["close"] = r["close"]
            b["volume"] += r["volume"]
    return [buckets[k] for k in sorted(buckets)]


def _one_min_since(session_factory, symbol: str, segment: str, day: date) -> List[dict]:
    start_utc = datetime.combine(day, dtime.min) - timedelta(hours=5, minutes=30)
    with session_scope(session_factory) as session:
        return [
            {"ts": c.ts, "open": float(c.open_price), "high": float(c.high_price),
             "low": float(c.low_price), "close": float(c.close_price), "volume": int(c.volume)}
            for c in session.query(CandleHistorical).filter(
                CandleHistorical.symbol == symbol, CandleHistorical.exchange_segment == segment,
                CandleHistorical.timeframe == "1min", CandleHistorical.ts >= start_utc,
            ).order_by(CandleHistorical.ts)
        ]


def _store(session_factory, item: dict, timeframe: str, rows: List[dict], dry_run: bool) -> int:
    if not rows or dry_run:
        return 0
    n = sync.insert_missing(session_factory, item["symbol"], item["exchange_segment"], timeframe, rows)
    sync.widen_coverage(session_factory, item["symbol"], item["exchange_segment"], timeframe,
                        min(r["ts"] for r in rows), max(r["ts"] for r in rows))
    return n


def update_symbol(session_factory, broker, item: dict, now_ist: datetime, dry_run: bool = False) -> dict:
    sym, seg = item["symbol"], item["exchange_segment"]
    out = {"symbol": sym, "inserted": {}}
    if sync.has_rows(session_factory, sym, seg, "1min"):
        last1 = sync.last_ts(session_factory, sym, seg, "1min")
        rows = fetch_direct(broker, item, "1min", _start_date(last1, now_ist), now_ist)
        out["inserted"]["1min"] = _store(session_factory, item, "1min", rows, dry_run)
        for tf, minutes in ROLLUP_MINUTES.items():  # each timeframe from its own last candle
            last = sync.last_ts(session_factory, sym, seg, tf)
            from_day = _start_date(last, now_ist)  # no rows yet: roll up all stored 1-min
            built = [r for r in rollup(_one_min_since(session_factory, sym, seg, from_day), minutes)
                     if last is None or r["ts"] > last]
            out["inserted"][tf] = _store(session_factory, item, tf, built, dry_run)
    else:
        last5 = sync.last_ts(session_factory, sym, seg, "5min")
        rows = fetch_direct(broker, item, "5min", _start_date(last5, now_ist), now_ist)
        if last5 is None and not rows:
            raise RuntimeError("no 5-min candles returned for a stock with no stored history")
        out["inserted"]["5min"] = _store(session_factory, item, "5min", rows, dry_run)
    return out


def run(session_factory, broker, universe: List[dict], now_ist: datetime, dry_run: bool = False,
        log: Callable[[str], None] = print) -> dict:
    ok = failed = 0
    for n, item in enumerate(universe, 1):
        try:
            r = update_symbol(session_factory, broker, item, now_ist, dry_run)
            ok += 1
            log(f"[{n}/{len(universe)}] {r['symbol']}: inserted {r['inserted']}")
        except Exception as e:  # one bad stock must not stop the rest
            failed += 1
            log(f"[{n}/{len(universe)}] {item['symbol']}: FAILED {type(e).__name__}: {e}")
    log(f"done: {ok} ok, {failed} failed")
    return {"ok": ok, "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default=str(DEFAULT_UNIVERSE))
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--dry-run", action="store_true", help="fetch but do not write")
    a = ap.parse_args()

    from dotenv import load_dotenv
    for env in (BACKEND.parent / ".env", BACKEND / ".env"):
        load_dotenv(env)
    from brokers.dhan_broker import DhanBroker
    from config import DHAN_TOKEN_TYPE_REST, load_dhan_tokens
    from db.session import build_engine, build_session_factory

    session_factory = build_session_factory(build_engine())
    with session_factory() as session:
        token = load_dhan_tokens(session).get(DHAN_TOKEN_TYPE_REST)
    if not token:
        print("no active FIXED3 Dhan token in BrokerToken", flush=True)
        return 1
    broker = DhanBroker(os.environ["DHAN_CLIENT_ID"], token)
    universe = load_universe(Path(a.universe), a.symbols)
    now_ist = datetime.now(IST)
    print(f"{now_ist:%Y-%m-%d %H:%M} IST: updating {len(universe)} stocks", flush=True)
    result = run(session_factory, broker, universe, now_ist, a.dry_run, log=lambda m: print(m, flush=True))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
