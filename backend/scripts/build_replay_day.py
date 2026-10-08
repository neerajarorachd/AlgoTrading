"""Builds a replay-day file for replay_feed.py (TEMPORARY dev tool, 2026-10-07:
"because of lack of live data... read 1 min candles from yesterday's data...
should behave like its a live data ticker").

One complete NSE session of 1-min candles for every symbol, plus what the
replay needs to make it look live: each symbol's Dhan security id, the
previous session's close (for change %) and H/L/C (for pivot points).

Sources:
  --from-file PATH   a month_symbol_candles.json-style export
                     ({SYM: {"1min": {"candles": [[unix, o, h, l, c, v], ...]}}})
                     -- no VM/network needed.
  --from-db          the live DB's candles_today + candles_historical for the
                     date (read-only; needs the SSH tunnel / VM up).
  --fill-from-dhan   (with either) refetch the replay day from Dhan where the
                     source is missing minutes -- "complete data for the day".

    .venv/Scripts/python.exe backend/scripts/build_replay_day.py --date 2026-09-15 \
        --from-file month_symbol_candles.json --out <path>/replay_2026-09-15.json
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

IST = timedelta(hours=5, minutes=30)
SECURITY_MASTER = BACKEND / "data" / "instruments" / "dhan_security_master.csv"


def ist_date(unix: int) -> date:
    return (datetime.fromtimestamp(unix, timezone.utc) + IST).date()


def security_ids(symbols) -> dict:
    """NSE equity security ids from the locally cached Dhan scrip master --
    no network, works with the VM down."""
    wanted, found = set(symbols), {}
    with open(SECURITY_MASTER, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row["SEM_EXM_EXCH_ID"] == "NSE" and row["SEM_SEGMENT"] == "E"
                    and row["SEM_INSTRUMENT_NAME"] == "EQUITY" and row["SEM_TRADING_SYMBOL"] in wanted):
                found[row["SEM_TRADING_SYMBOL"]] = row["SEM_SMST_SECURITY_ID"]
    return found


def candles_from_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {sym: tfs["1min"]["candles"] for sym, tfs in data.items()}


def candles_from_db(day: date) -> dict:
    from dotenv import load_dotenv
    load_dotenv(BACKEND.parent / ".env")
    from db.models import CandleHistorical, CandleToday, SubscribedSymbol
    from db.session import build_engine, build_session_factory

    session = build_session_factory(build_engine())()
    # two sessions back, so the previous session's close/HLC is included
    start = datetime.combine(day - timedelta(days=7), time()) - IST
    end = datetime.combine(day + timedelta(days=1), time()) - IST
    out = defaultdict(dict)
    for sym, in session.query(SubscribedSymbol.symbol).filter_by(active=True):
        for model in (CandleHistorical, CandleToday):
            for row in (session.query(model).filter(model.symbol == sym, model.timeframe == "1min",
                                                     model.ts >= start, model.ts < end)):
                ts = row.ts.replace(tzinfo=timezone.utc) if row.ts.tzinfo is None else row.ts
                out[sym][int(ts.timestamp())] = [int(ts.timestamp()), float(row.open_price), float(row.high_price),
                                                 float(row.low_price), float(row.close_price), int(row.volume)]
    return {sym: [by_ts[k] for k in sorted(by_ts)] for sym, by_ts in out.items()}


def fill_from_dhan(all_candles: dict, day: date) -> None:
    """Replaces each symbol's replay-day candles with Dhan's own full-session
    response when Dhan has more minutes -- the DB copy can have holes from
    feed drops (found 2026-10-07: 357-375 candles per symbol for 06-Oct).
    Uses AlgoTrading's REST token (FIXED3), read-only; ~3s per symbol (Dhan's
    chart-endpoint pacing, see DhanBroker)."""
    import os
    from dotenv import load_dotenv
    load_dotenv(BACKEND.parent / ".env")
    from brokers.dhan_broker import DhanBroker
    from config import DHAN_TOKEN_TYPE_REST, load_dhan_tokens
    from db.session import build_engine, build_session_factory

    with build_session_factory(build_engine())() as session:
        token = load_dhan_tokens(session).get(DHAN_TOKEN_TYPE_REST)
    if not token:
        raise SystemExit("no AlgoTrading REST token (FIXED3) in the DB")
    broker = DhanBroker(client_id=os.environ["DHAN_CLIENT_ID"], access_token=token)
    ids = security_ids(all_candles)
    # Dhan takes date-only bounds (strftime %Y-%m-%d) -- pass plain dates, not
    # IST-midnight-as-UTC, which would format as the previous day
    start, end = datetime.combine(day, time()), datetime.combine(day + timedelta(days=1), time())
    for sym in sorted(all_candles):
        if sym not in ids:
            continue
        fetched = broker.get_historical_data(sym, ids[sym], "NSE_EQ", "1min", start, end)
        session_rows = [[int(c.timestamp.timestamp()), c.open, c.high, c.low, c.close, c.volume]
                        for c in fetched if (c.timestamp + IST).date() == day]
        have = [c for c in all_candles[sym] if ist_date(c[0]) == day]
        if len(session_rows) > len(have):
            others = [c for c in all_candles[sym] if ist_date(c[0]) != day]
            all_candles[sym] = sorted(others + session_rows, key=lambda c: c[0])
            print(f"  {sym:11s} filled from Dhan: {len(have)} -> {len(session_rows)} candles")
        else:
            print(f"  {sym:11s} already complete ({len(have)}; Dhan has {len(session_rows)})")


def build(all_candles: dict, day: date) -> dict:
    ids = security_ids(all_candles)
    symbols = {}
    for sym, candles in sorted(all_candles.items()):
        session = [c for c in candles if ist_date(c[0]) == day]
        earlier = [c for c in candles if ist_date(c[0]) < day]
        if not session or not earlier or sym not in ids:
            print(f"  skip {sym}: session={len(session)} earlier={len(earlier)} id={'yes' if sym in ids else 'no'}")
            continue
        prev_day = ist_date(earlier[-1][0])
        prev = [c for c in earlier if ist_date(c[0]) == prev_day]
        symbols[sym] = {
            "security_id": ids[sym], "exchange": "NSE", "segment": "EQUITY", "exchange_segment": "NSE_EQ",
            "previous_session": prev_day.isoformat(),
            "previous_close": prev[-1][4],
            "previous_high": max(c[2] for c in prev),
            "previous_low": min(c[3] for c in prev),
            "candles": session,
        }
        print(f"  {sym:11s} {len(session)} candles, previous session {prev_day} close {prev[-1][4]}")
    return {"date": day.isoformat(), "symbols": symbols}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="the session to replay, YYYY-MM-DD (IST)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-file", type=Path)
    src.add_argument("--from-db", action="store_true")
    p.add_argument("--fill-from-dhan", action="store_true",
                   help="refetch the replay day from Dhan where the source is missing minutes")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()

    day = date.fromisoformat(args.date)
    candles = candles_from_file(args.from_file) if args.from_file else candles_from_db(day)
    if args.fill_from_dhan:
        fill_from_dhan(candles, day)
    result = build(candles, day)
    args.out.write_text(json.dumps(result), encoding="utf-8")
    print(f"wrote {len(result['symbols'])} symbol(s) for {day} -> {args.out}")


if __name__ == "__main__":
    main()
