"""Tests an additional pre-trade filter for double_top: require the "M"
shape's depth -- (avg of the two tops - the trough between them) / avg of
the two tops -- to clear a stricter minimum than activity_engine.py's own
built-in floor, before taking the trade. Explicit instruction, 2026-09-17:
"check the gap between ((second top - 5 candles)-prev bottom) > .08 or .09".

Two things already true about this depth check that are worth being clear
on before reading results:
  - activity_engine.py ALREADY enforces a minimum depth on every double_top
    it detects at all -- _DOUBLE_MIN_DEPTH = 0.003 (0.3%). A threshold
    tighter than that (0.8%/0.9%, the interpretation used here -- 0.08/0.09
    read as already-percent, matching this session's ".23"="0.23%"
    shorthand) genuinely filters a SUBSET of existing detections; a
    threshold looser than 0.3% would filter nothing at all.
  - This exact ratio is already computed today as "intensity"
    (double_top_intensity = depth / _DOUBLE_MIN_DEPTH, see
    activity_engine.py) and persisted per-detection on InstrumentActivity
    -- but this script recomputes it directly from the same swing-point
    replay used elsewhere in this investigation (not a DB read), matching
    real entry price (current candle's close, not the stale swing-candle
    price -- see [[stale_entry_price_bug]]) and the honest SL/TG race
    methodology (range containment, stop-first tie-break) used throughout.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_double_top_depth_filter.py
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import SwingPoint, _SWING_LOOKBACK, detect_double_top, detect_swing_high, detect_swing_low
from brokers.models import Candle
from db.models import SubscribedSymbol
from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory
from order_backtest import fixed_pct_levels

SYMBOL = "HINDCOPPER"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
MAX_CANDLES = 120
SL_PCT, TG_PCT = 0.0025, 0.005
# (entry_price - previous_bottom/neckline_low) / entry_price -- the REAL,
# tradeable entry price vs. the most recent swing low ("B" right before
# the pattern's own T-B-T), not the stale swing-high peak's own price and
# not the two tops' average. Explicit clarification, 2026-09-17: "if
# target is .5% then there must be at least .8% difference between entry
# price and previous bottom... or say Low on graph."
DEPTH_THRESHOLDS = [0.003, 0.005, 0.008, 0.009, 0.012, 0.015]


def race(candles: list[Candle], entry_index: int, entry_price: float, direction: str) -> str:
    stop_price, target_price = fixed_pct_levels(entry_price, direction, SL_PCT, TG_PCT)
    entry_day = candles[entry_index].timestamp.date()
    for j in range(entry_index + 1, min(entry_index + 1 + MAX_CANDLES, len(candles))):
        c = candles[j]
        if c.timestamp.date() != entry_day:
            break
        low, high = c.low, c.high
        hit_target = low <= target_price <= high
        hit_stop = low <= stop_price <= high
        if not (hit_target or hit_stop):
            continue
        return "loss" if hit_stop else "win"
    return "timeout"


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

    window = deque(maxlen=2 * _SWING_LOOKBACK + 1)
    points: list[SwingPoint] = []
    dets = []  # each: entry_index (REAL, current-candle), entry_price (REAL), depth_pct

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

            if kind_name == "high" and detect_double_top(points):
                b = points[-2]  # the neckline low between the two tops -- "previous bottom" / "Low on graph"
                # REAL entry: the CURRENT candle (i) closing now -- NOT the
                # stale swing candle (swing_candle_index, 5 candles back).
                entry_price = float(candle.close)
                depth_pct = (entry_price - b.price) / entry_price if entry_price else 0.0
                dets.append({
                    "entry_index": i, "entry_price": entry_price,
                    "direction": "bear", "depth_pct": depth_pct,
                })

    print(f"{len(dets)} double_top detections (real entry price)\n", flush=True)

    for threshold in DEPTH_THRESHOLDS:
        kept = [d for d in dets if d["depth_pct"] >= threshold]
        dropped_pct = (1 - len(kept) / len(dets)) * 100 if dets else 0.0
        wins = losses = timeouts = 0
        for d in kept:
            r = race(candles, d["entry_index"], d["entry_price"], d["direction"])
            if r == "win":
                wins += 1
            elif r == "loss":
                losses += 1
            else:
                timeouts += 1
        decided = wins + losses
        wr = wins / decided * 100 if decided else 0.0
        edge = (wins / decided * TG_PCT - losses / decided * SL_PCT) * 100 if decided else 0.0
        print(f"depth >= {threshold*100:.2f}%  N={len(kept):<5} ({dropped_pct:5.1f}% dropped)  "
              f"win={wins:<5} loss={losses:<5} timeout={timeouts:<5} win_ratio={wr:5.1f}%  edge={edge:+.3f}%")

    # baseline (no depth filter at all, the existing activity_engine floor
    # of 0.3% is already baked into every detection) for direct comparison
    wins = losses = timeouts = 0
    for d in dets:
        r = race(candles, d["entry_index"], d["entry_price"], d["direction"])
        if r == "win":
            wins += 1
        elif r == "loss":
            losses += 1
        else:
            timeouts += 1
    decided = wins + losses
    wr = wins / decided * 100 if decided else 0.0
    print(f"\nbaseline (no extra filter, N={len(dets)}): win_ratio={wr:.1f}%  win={wins} loss={losses} timeout={timeouts}")

    # sanity: depth_pct distribution
    depths = sorted(d["depth_pct"] for d in dets)
    n = len(depths)
    print(f"\ndepth_pct distribution: min={depths[0]*100:.3f}%  p25={depths[n//4]*100:.3f}%  "
          f"median={depths[n//2]*100:.3f}%  p75={depths[3*n//4]*100:.3f}%  max={depths[-1]*100:.3f}%")


if __name__ == "__main__":
    main()
