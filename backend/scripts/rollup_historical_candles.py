"""Derives 3min/5min CandleHistorical rows from already-fetched 1min data,
instead of fetching them directly from Dhan — this project's own standing
convention (historical_data_fetch_convention memory), re-confirmed the hard
way 2026-09-17: a direct 90-day '3min' request to Dhan's /charts/intraday
silently ignores the requested range and returns only the CURRENT day's
candles (confirmed live: 90-day request for ASHOKLEY returned 82 candles,
all timestamped today, vs 1min/5min correctly spanning the full range). '5'
happened to work correctly in that same test, but is rolled up too anyway,
for the consistency the convention calls for rather than relying on an
interval value that isn't officially guaranteed.

Bucketing matches feed/candle_aggregator.py's own live rollup exactly
(bucket_start = ts floored to the nearest n-minute boundary), just done in
bulk over already-persisted history instead of one 1-min candle at a time.

Replaces (delete + bulk-insert) each symbol's existing 3min/5min rows
entirely — simpler and safer than diffing against whatever partial/wrong
data a prior direct-fetch attempt may have left behind.

Run manually:
    .venv/Scripts/python.exe backend/scripts/rollup_historical_candles.py [SYMBOL ...]
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.models import CandleHistorical, SubscribedSymbol
from db.ops import LibCandlesHistorical as ops_candles_historical
from db.session import build_engine, build_session_factory, session_scope

ROLLUP_MINUTES = {"3min": 3, "5min": 5}


def _bucket_start(ts, n):
    return ts - timedelta(minutes=ts.minute % n, seconds=ts.second, microseconds=ts.microsecond)


def rollup_one(session_factory, symbol: str, exchange_segment: str, timeframe: str) -> int:
    n = ROLLUP_MINUTES[timeframe]
    with session_scope(session_factory) as session:
        one_min = (
            session.query(CandleHistorical)
            .filter_by(symbol=symbol, exchange_segment=exchange_segment, timeframe="1min")
            .order_by(CandleHistorical.ts.asc())
            .all()
        )
        buckets: dict = {}
        order: list = []
        for c in one_min:
            key = _bucket_start(c.ts, n)
            if key not in buckets:
                buckets[key] = {"open": float(c.open_price), "high": float(c.high_price),
                                 "low": float(c.low_price), "close": float(c.close_price),
                                 "volume": c.volume}
                order.append(key)
            else:
                b = buckets[key]
                b["high"] = max(b["high"], float(c.high_price))
                b["low"] = min(b["low"], float(c.low_price))
                b["close"] = float(c.close_price)
                b["volume"] += c.volume

        session.query(CandleHistorical).filter_by(
            symbol=symbol, exchange_segment=exchange_segment, timeframe=timeframe,
        ).delete()
        session.flush()

        rows = [
            CandleHistorical(
                symbol=symbol, exchange_segment=exchange_segment, timeframe=timeframe, ts=key,
                open_price=buckets[key]["open"], high_price=buckets[key]["high"],
                low_price=buckets[key]["low"], close_price=buckets[key]["close"],
                volume=buckets[key]["volume"],
            )
            for key in order
        ]
        session.add_all(rows)
        return len(rows)


def main() -> None:
    engine = build_engine()
    session_factory = build_session_factory(engine)
    with session_factory() as session:
        requested = set(sys.argv[1:])
        rows = session.query(SubscribedSymbol).filter_by(active=True).order_by(SubscribedSymbol.symbol).all()
        targets = [row for row in rows if not requested or row.symbol in requested]

    for row in targets:
        with session_factory() as session:
            one_min_coverage = ops_candles_historical.get_coverage(session, row.symbol, row.exchange_segment, "1min")

        print(f"{row.symbol}:", end=" ", flush=True)
        for timeframe in ROLLUP_MINUTES:
            count = rollup_one(session_factory, row.symbol, row.exchange_segment, timeframe)
            print(f"{timeframe}={count}", end="  ", flush=True)
            # Rolled-up data spans exactly whatever 1min covers — mirror that
            # coverage so a future ensure_data_available call correctly sees
            # this range as already satisfied instead of re-attempting the
            # broken direct Dhan fetch for 3min/5min.
            if one_min_coverage is not None:
                ops_candles_historical.set_coverage(
                    session_factory, row.symbol, row.exchange_segment, timeframe, *one_min_coverage,
                )
        print()


if __name__ == "__main__":
    main()
