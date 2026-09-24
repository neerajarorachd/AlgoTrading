"""How long (in candles) does it actually take for price to move 0.2%/0.3%/
0.4%/0.5% in the pattern's OWN predicted direction, from the real entry
price -- no stop-loss race, just "does it get there, and how fast." Same
single-pass detection gathering (real current-candle entry price) as
verify_all_patterns_winrate.py, same MAX_CANDLES/same-day cap.

For each pattern: % of detections that ever reach each threshold within the
window, and the median/p25/p75 candle-count to get there for the ones that
do. Thresholds are cumulative in one forward walk (tracking the running
favorable extreme) rather than 4 separate walks.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_time_to_move.py
"""
from __future__ import annotations

import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine
from brokers.models import Candle
from db.models import SubscribedSymbol
from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory
from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

SYMBOL = "HINDCOPPER"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
MAX_CANDLES = 120
THRESHOLDS = [0.002, 0.003, 0.004, 0.005]  # 0.2% / 0.3% / 0.4% / 0.5%

# The 6 patterns whose checkpoint sign-agreement rate looked like "70%+" --
# the ones this whole investigation started from.
HIGHLIGHT = {"double_top", "triple_top", "double_bottom", "triple_bottom",
             "bearish_structure_shift", "bullish_structure_shift"}


def time_to_move(candles: list[Candle], entry_index: int, entry_price: float, direction: str) -> dict:
    """candles_to_reach[threshold] = candle count (1-based) at which the
    running favorable extreme first cleared that threshold, or None if it
    never did within the window."""
    entry_day = candles[entry_index].timestamp.date()
    reached: dict[float, int | None] = {t: None for t in THRESHOLDS}
    extreme = entry_price  # running best (highest high for bull, lowest low for bear)

    for j in range(entry_index + 1, min(entry_index + 1 + MAX_CANDLES, len(candles))):
        c = candles[j]
        if c.timestamp.date() != entry_day:
            break
        candles_elapsed = j - entry_index
        if direction == "bull":
            extreme = max(extreme, c.high)
            move_pct = (extreme - entry_price) / entry_price
        else:
            extreme = min(extreme, c.low)
            move_pct = (entry_price - extreme) / entry_price
        for t in THRESHOLDS:
            if reached[t] is None and move_pct >= t:
                reached[t] = candles_elapsed
        if all(v is not None for v in reached.values()):
            break
    return reached


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    with session_factory() as session:
        symbol_row = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one()
        exchange_segment = symbol_row.exchange_segment
        historical = LibCandlesHistorical.get_range(session, SYMBOL, exchange_segment, TIMEFRAME, start, end)

    candles = [
        Candle(symbol=SYMBOL, timeframe=TIMEFRAME, timestamp=r.ts, open=float(r.open_price),
               high=float(r.high_price), low=float(r.low_price), close=float(r.close_price), volume=r.volume)
        for r in historical
    ]
    print(f"{SYMBOL}: {len(candles)} historical {TIMEFRAME} candles loaded\n", flush=True)

    activity_engine = ActivityEngine(session_factory)
    detections: dict[str, list[dict]] = {}
    for i, candle in enumerate(candles):
        for activity in activity_engine.on_candle_closed(SYMBOL, exchange_segment, candle):
            pattern = activity["activity"]
            if pattern in BULLISH_PATTERNS:
                direction = "bull"
            elif pattern in BEARISH_PATTERNS:
                direction = "bear"
            else:
                continue
            detections.setdefault(pattern, []).append({
                "entry_index": i, "entry_price": float(candle.close), "direction": direction,
            })
        if (i + 1) % 40_000 == 0:
            print(f"  ...{i + 1}/{len(candles)} candles scanned", flush=True)

    print(f"\n{sum(len(v) for v in detections.values())} total detections\n", flush=True)

    header = (f"{'Pattern':<28}{'Dir':<6}{'N':>7}   "
              f"{'>=0.2%':>16}{'>=0.3%':>16}{'>=0.4%':>16}{'>=0.5%':>16}")
    print(header)
    print("  (reach% / median candles / p25-p75)")
    print("-" * len(header))

    rows = []
    for pattern, dets in sorted(detections.items()):
        direction = dets[0]["direction"]
        per_threshold = {t: [] for t in THRESHOLDS}
        for d in dets:
            reached = time_to_move(candles, d["entry_index"], d["entry_price"], d["direction"])
            for t in THRESHOLDS:
                if reached[t] is not None:
                    per_threshold[t].append(reached[t])

        cells = []
        for t in THRESHOLDS:
            times = per_threshold[t]
            reach_pct = len(times) / len(dets) * 100
            if times:
                med = statistics.median(times)
                p25 = statistics.quantiles(times, n=4)[0] if len(times) >= 4 else min(times)
                p75 = statistics.quantiles(times, n=4)[2] if len(times) >= 4 else max(times)
                cells.append(f"{reach_pct:4.0f}% {med:4.0f}c [{p25:.0f}-{p75:.0f}]")
            else:
                cells.append(f"{reach_pct:4.0f}%   n/a")
        marker = " *" if pattern in HIGHLIGHT else ""
        print(f"{pattern:<28}{direction:<6}{len(dets):>7}   " + "  ".join(f"{c:>15}" for c in cells) + marker)

    print("\n* = one of the 6 patterns flagged earlier as showing an inflated 70%+ checkpoint rate")


if __name__ == "__main__":
    main()
