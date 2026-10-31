"""Load the research universe's 5-min candles (pickle files written by
research/universe_*/fetch_universe.py) into `candles_historical`.

Rows: (epoch_utc, open, high, low, close, volume), one pickle per symbol.
Insert-only: a candle already stored (same symbol, segment, timeframe, ts) is
left untouched, so existing data for symbols already in the table is never
changed. The tracked coverage (HistoricalDataCoverage) is set for new symbols
and widened for symbols whose coverage overlaps the loaded range, so a later
backtest does not re-fetch what is already here.

One commit per symbol keeps the SQL Server log small. Run it ON THE VM (no
tunnel round trips):

    python scripts/load_5min_from_pkl.py --pkl-dir DIR --universe FILE [--symbols A B]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from db.models import CandleHistorical  # noqa: E402
from db.ops import LibCandlesHistorical as ops  # noqa: E402
from db.session import session_scope  # noqa: E402

TIMEFRAME = "5min"
STEP = timedelta(minutes=5)
BATCH = 10_000


def _naive_utc(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)


def load_symbol(session_factory, symbol: str, segment: str, rows: list) -> dict:
    if not rows:
        return {"symbol": symbol, "read": 0, "inserted": 0}
    with session_scope(session_factory) as session:
        have = {
            r[0] for r in session.query(CandleHistorical.ts).filter_by(
                symbol=symbol, exchange_segment=segment, timeframe=TIMEFRAME)
        }
    new = [
        {"symbol": symbol, "exchange_segment": segment, "timeframe": TIMEFRAME,
         "ts": _naive_utc(r[0]), "open_price": r[1], "high_price": r[2],
         "low_price": r[3], "close_price": r[4], "volume": int(r[5])}
        for r in rows if _naive_utc(r[0]) not in have
    ]
    with session_scope(session_factory) as session:
        for i in range(0, len(new), BATCH):
            session.execute(CandleHistorical.__table__.insert(), new[i:i + BATCH])

    first = _naive_utc(min(r[0] for r in rows)).replace(tzinfo=timezone.utc)
    last = _naive_utc(max(r[0] for r in rows)).replace(tzinfo=timezone.utc)
    with session_scope(session_factory) as session:
        cov = ops.get_coverage(session, symbol, segment, TIMEFRAME)
    if cov is None:
        ops.set_coverage(session_factory, symbol, segment, TIMEFRAME, first, last)
    else:
        c_from, c_to = (c.replace(tzinfo=c.tzinfo or timezone.utc) for c in cov)
        if first <= c_to + STEP and last >= c_from - STEP:  # overlaps or touches: widen
            ops.set_coverage(session_factory, symbol, segment, TIMEFRAME, min(first, c_from), max(last, c_to))
    return {"symbol": symbol, "read": len(rows), "inserted": len(new)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkl-dir", required=True)
    ap.add_argument("--universe", required=True)
    ap.add_argument("--symbols", nargs="*")
    a = ap.parse_args()

    from dotenv import load_dotenv
    for env in (BACKEND.parent / ".env", BACKEND / ".env"):
        load_dotenv(env)
    from db.session import build_engine, build_session_factory

    session_factory = build_session_factory(build_engine())
    universe = json.loads(Path(a.universe).read_text(encoding="utf-8"))
    if a.symbols:
        universe = [u for u in universe if u["symbol"] in set(a.symbols)]
    total = 0
    for n, item in enumerate(universe, 1):
        p = Path(a.pkl_dir) / f"{item['symbol']}.pkl"
        if not p.exists():
            print(f"[{n}/{len(universe)}] {item['symbol']}: no pickle, skipped", flush=True)
            continue
        r = load_symbol(session_factory, item["symbol"], item["exchange_segment"], pickle.load(open(p, "rb")))
        total += r["inserted"]
        print(f"[{n}/{len(universe)}] {r['symbol']}: read {r['read']}, inserted {r['inserted']}", flush=True)
    print(f"done: {total} rows inserted", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
