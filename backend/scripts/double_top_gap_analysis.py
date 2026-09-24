"""One-off analysis: does the candle-gap between a double_top's two swing
highs correlate with its actual win rate? Re-derives each historical
double_top's gap (activity_engine.py's detect_double_top only checks price
similarity/valley depth, never gap — see this session's own investigation),
then joins each occurrence to its ALREADY-COMPUTED PatternOutcome row (same
instrument/timeframe/pattern/detected_ts) to get real win/loss at every
checkpoint, using the exact same sign-agreement rule LibPatternOutcomes.
_band_stats already uses everywhere else in this project.

Read-only — writes nothing, reuses activity_engine.py's real detection
functions directly rather than reimplementing them, so results reflect
exactly what the live/replay pipeline actually detects.

Run manually:
    .venv/Scripts/python.exe backend/scripts/double_top_gap_analysis.py [SYMBOL]
"""
from __future__ import annotations

import sys
import statistics
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import SwingPoint, _SWING_LOOKBACK, detect_double_top, detect_swing_high, detect_swing_low
from db.models import PatternOutcome, SubscribedSymbol
from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory
from brokers.models import Candle

TIMEFRAME = "1min"
PATTERN = "double_top"
CHECKPOINTS = (5, 10, 15, 20, 30)
GAP_BUCKETS = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 10**9)]


def bucket_label(lo: int, hi: int) -> str:
    return f"{lo}-{hi - 1}" if hi < 10**9 else f"{lo}+"


def main() -> None:
    symbol_arg = sys.argv[1] if len(sys.argv) > 1 else "HINDCOPPER"
    engine = build_engine()
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        symbol_row = session.query(SubscribedSymbol).filter_by(symbol=symbol_arg).one()
        instrument_id = symbol_row.id

        historical = LibCandlesHistorical.get_range(session, symbol_arg, symbol_row.exchange_segment, TIMEFRAME)
        print(f"{symbol_arg}: {len(historical)} historical {TIMEFRAME} candles loaded")

        outcomes_by_ts = {
            o.detected_ts: o
            for o in session.query(PatternOutcome).filter_by(
                instrument_id=instrument_id, timeframe=TIMEFRAME, pattern=PATTERN,
            ).all()
        }
        print(f"{len(outcomes_by_ts)} existing PatternOutcome rows for {PATTERN}")

    # Convert once — detect_swing_high/low/double_top only touch .high/.low/.timestamp
    candles = [
        Candle(symbol=symbol_arg, timeframe=TIMEFRAME, timestamp=r.ts,
               open=float(r.open_price), high=float(r.high_price),
               low=float(r.low_price), close=float(r.close_price), volume=r.volume)
        for r in historical
    ]

    window = deque(maxlen=2 * _SWING_LOOKBACK + 1)
    points: list[SwingPoint] = []
    point_index: list[int] = []  # candles[] index of each point's own swing candle
    detections: list[dict] = []  # {detected_ts, gap, outcome}

    for i, candle in enumerate(candles):
        window.append(candle)
        if len(window) < window.maxlen:
            continue
        window_list = list(window)
        swing_candle_index = i - _SWING_LOOKBACK

        for kind_check, kind_name in ((detect_swing_high, "high"), (detect_swing_low, "low")):
            if not kind_check(window_list, _SWING_LOOKBACK):
                continue
            swing_candle = window_list[_SWING_LOOKBACK]
            price = swing_candle.high if kind_name == "high" else swing_candle.low
            points.append(SwingPoint(kind=kind_name, price=price, candle=swing_candle))
            point_index.append(swing_candle_index)

            if kind_name == "high" and detect_double_top(points):
                a_idx, c_idx = point_index[-3], point_index[-1]
                detected_ts = points[-1].candle.timestamp
                detections.append({
                    "detected_ts": detected_ts,
                    "gap": c_idx - a_idx,
                    "outcome": outcomes_by_ts.get(detected_ts),
                })

    print(f"\n{len(detections)} double_top detections found in replay")
    matched_to_outcome = sum(1 for d in detections if d["outcome"] is not None)
    print(f"{matched_to_outcome} matched to an existing PatternOutcome row by exact detected_ts")

    gaps = [d["gap"] for d in detections]
    if gaps:
        print(f"gap stats: min={min(gaps)} median={statistics.median(gaps)} "
              f"mean={statistics.fmean(gaps):.1f} max={max(gaps)}")

    print(f"\n{'Gap bucket':<12}{'N':>6}" + "".join(f"{'win@'+str(n):>10}" for n in CHECKPOINTS))
    print("-" * (12 + 6 + 10 * len(CHECKPOINTS)))
    for lo, hi in GAP_BUCKETS:
        bucket = [d for d in detections if lo <= d["gap"] < hi and d["outcome"] is not None]
        if not bucket:
            print(f"{bucket_label(lo, hi):<12}{0:>6}")
            continue
        cells = []
        for n in CHECKPOINTS:
            wins = total = 0
            for d in bucket:
                pct = getattr(d["outcome"], f"pct_change_{n}")
                if pct is None:
                    continue
                total += 1
                if float(pct) < 0:  # bear pattern — win means price actually fell
                    wins += 1
            cells.append(f"{wins}/{total}={wins/total*100:.0f}%" if total else "—")
        print(f"{bucket_label(lo, hi):<12}{len(bucket):>6}" + "".join(f"{c:>10}" for c in cells))


if __name__ == "__main__":
    main()
