"""Traverse one stock's already-backfilled 1-min candles for today and list
every single-candle pattern found, with a timestamp and an intensity score —
a preview step before deciding whether/how to persist intensity, run
manually:

    .venv/Scripts/python.exe backend/scripts/list_single_candle_patterns.py SYMBOL [EXCHANGE_SEGMENT] [TIMEFRAME]

Defaults: EXCHANGE_SEGMENT=NSE_EQ, TIMEFRAME=1min. Prints nothing to the DB —
read-only, just detection + intensity, listed for review.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import SINGLE_CANDLE_INTENSITY, SINGLE_CANDLE_PATTERNS
from brokers.models import Candle
from db.models import CandleToday
from db.session import build_engine, build_session_factory


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    symbol = sys.argv[1]
    exchange_segment = sys.argv[2] if len(sys.argv) > 2 else "NSE_EQ"
    timeframe = sys.argv[3] if len(sys.argv) > 3 else "1min"

    session_factory = build_session_factory(build_engine())
    with session_factory() as session:
        rows = (
            session.query(CandleToday)
            .filter_by(symbol=symbol, exchange_segment=exchange_segment, timeframe=timeframe)
            .order_by(CandleToday.ts.asc())
            .all()
        )
        candles = [
            Candle(
                symbol=symbol, timeframe=timeframe, timestamp=r.ts,
                open=float(r.open_price), high=float(r.high_price),
                low=float(r.low_price), close=float(r.close_price), volume=r.volume,
            )
            for r in rows
        ]

    print(f"{symbol} {exchange_segment} {timeframe}: {len(candles)} candles")
    print(f"{'timestamp':<20} {'pattern':<16} {'intensity':>10}   O/H/L/C")
    found = 0
    for c in candles:
        for name, detector in SINGLE_CANDLE_PATTERNS.items():
            if not detector(c):
                continue
            found += 1
            intensity = SINGLE_CANDLE_INTENSITY[name](c)
            intensity_str = "inf" if intensity == float("inf") else f"{intensity:.2f}x"
            print(
                f"{c.timestamp.isoformat():<20} {name:<16} {intensity_str:>10}   "
                f"{c.open:.2f}/{c.high:.2f}/{c.low:.2f}/{c.close:.2f}"
            )
    print(f"\n{found} single-candle pattern(s) found across {len(candles)} candles")


if __name__ == "__main__":
    main()
