"""Follow-up to the double_top race-vs-eventual-move cross-tab: of the 798
detections (22.5% of all double_top signals) that got stopped out at 0.25%
but WOULD have eventually reached +0.5% favorable, how many get rescued by
(a) a wider stop, or (b) a short delay before committing capital?

Same real HINDCOPPER data, same real entry price (current-candle close),
same range-containment/stop-first resolution as every other script in this
investigation -- only the stop width or the entry timing changes.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_double_top_rescue.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine
from brokers.models import Candle
from db.models import SubscribedSymbol
from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory
from order_backtest import fixed_pct_levels

SYMBOL = "HINDCOPPER"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
MAX_CANDLES = 120
BASE_SL, BASE_TG = 0.0025, 0.005

WIDER_STOP_COMBOS = [
    (0.004, 0.005),   # SL widened to 0.4%, target unchanged
    (0.004, 0.008),   # SL widened to 0.4%, target scaled to keep 2:1
    (0.005, 0.005),   # SL widened to 0.5%, target unchanged (1:1)
    (0.005, 0.010),   # SL widened to 0.5%, target scaled to keep 2:1
]
DELAYS = [2, 3, 5]  # candles waited before committing, same 0.25%/0.50% SL/TG from the NEW entry price


def race(candles: list[Candle], entry_index: int, entry_price: float, direction: str,
         sl_pct: float, tg_pct: float) -> str:
    stop_price, target_price = fixed_pct_levels(entry_price, direction, sl_pct, tg_pct)
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

    activity_engine = ActivityEngine(session_factory)
    dets = []
    for i, candle in enumerate(candles):
        for a in activity_engine.on_candle_closed(SYMBOL, exchange_segment, candle):
            if a["activity"] == "double_top":
                dets.append({"entry_index": i, "entry_price": float(candle.close), "direction": "bear"})
    print(f"{len(dets)} double_top detections\n", flush=True)

    # Baseline: identify the exact "stopped out but would've eventually won" group.
    rescue_group = []
    baseline_counts = {"win": 0, "loss": 0, "timeout": 0}
    for d in dets:
        r = race(candles, d["entry_index"], d["entry_price"], d["direction"], BASE_SL, BASE_TG)
        baseline_counts[r] += 1
        if r == "loss":
            # would it eventually have reached +0.5% favorable, ignoring the stop?
            entry_price, direction = d["entry_price"], d["direction"]
            entry_day = candles[d["entry_index"]].timestamp.date()
            extreme = entry_price
            ever = False
            for j in range(d["entry_index"] + 1, min(d["entry_index"] + 1 + MAX_CANDLES, len(candles))):
                c = candles[j]
                if c.timestamp.date() != entry_day:
                    break
                extreme = min(extreme, c.low) if direction == "bear" else max(extreme, c.high)
                move = (entry_price - extreme) / entry_price if direction == "bear" else (extreme - entry_price) / entry_price
                if move >= BASE_TG:
                    ever = True
                    break
            if ever:
                rescue_group.append(d)

    decided = baseline_counts["win"] + baseline_counts["loss"]
    print(f"=== Baseline (SL {BASE_SL*100:.2f}% / TG {BASE_TG*100:.2f}%) ===")
    print(f"win={baseline_counts['win']} loss={baseline_counts['loss']} timeout={baseline_counts['timeout']} "
          f"win_ratio={baseline_counts['win']/decided*100:.1f}%")
    print(f"'rescue group' (stopped out, but eventually reached +0.5% anyway): {len(rescue_group)} "
          f"({len(rescue_group)/len(dets)*100:.1f}% of all detections)\n")

    # --- Experiment A: wider stop, same entry timing, whole pattern re-tested.
    print("=== Experiment A: wider stop (whole double_top population re-tested) ===")
    print(f"{'SL%':>6}{'TG%':>6}{'N':>7}{'Win':>7}{'Loss':>7}{'Timeout':>9}{'WinRatio':>10}{'Edge':>9}"
          f"{'RescueFlips':>14}")
    for sl_pct, tg_pct in WIDER_STOP_COMBOS:
        wins = losses = timeouts = 0
        for d in dets:
            r = race(candles, d["entry_index"], d["entry_price"], d["direction"], sl_pct, tg_pct)
            if r == "win":
                wins += 1
            elif r == "loss":
                losses += 1
            else:
                timeouts += 1
        decided = wins + losses
        wr = wins / decided * 100 if decided else 0.0
        edge = (wins / decided * tg_pct - losses / decided * sl_pct) * 100 if decided else 0.0

        flips = 0
        for d in rescue_group:
            r = race(candles, d["entry_index"], d["entry_price"], d["direction"], sl_pct, tg_pct)
            if r == "win":
                flips += 1
        print(f"{sl_pct*100:>5.2f}%{tg_pct*100:>5.2f}%{len(dets):>7}{wins:>7}{losses:>7}{timeouts:>9}"
              f"{wr:>9.1f}%{edge:>+8.3f}%{flips:>10}/{len(rescue_group)}")

    # --- Experiment B: delayed entry, original 0.25%/0.50% SL/TG from the NEW entry.
    print("\n=== Experiment B: delayed entry (wait N candles, re-anchor entry price, SL 0.25%/TG 0.50%) ===")
    print(f"{'Delay':>7}{'N':>7}{'Win':>7}{'Loss':>7}{'Timeout':>9}{'WinRatio':>10}{'Edge':>9}{'RescueFlips':>14}")
    for delay in DELAYS:
        wins = losses = timeouts = 0
        n_valid = 0
        for d in dets:
            new_idx = d["entry_index"] + delay
            if new_idx >= len(candles) or candles[new_idx].timestamp.date() != candles[d["entry_index"]].timestamp.date():
                continue  # ran past the trading day -- skip, can't delay into tomorrow
            n_valid += 1
            new_entry_price = float(candles[new_idx].close)
            r = race(candles, new_idx, new_entry_price, d["direction"], BASE_SL, BASE_TG)
            if r == "win":
                wins += 1
            elif r == "loss":
                losses += 1
            else:
                timeouts += 1
        decided = wins + losses
        wr = wins / decided * 100 if decided else 0.0
        edge = (wins / decided * BASE_TG - losses / decided * BASE_SL) * 100 if decided else 0.0

        flips = 0
        flip_denom = 0
        for d in rescue_group:
            new_idx = d["entry_index"] + delay
            if new_idx >= len(candles) or candles[new_idx].timestamp.date() != candles[d["entry_index"]].timestamp.date():
                continue
            flip_denom += 1
            new_entry_price = float(candles[new_idx].close)
            r = race(candles, new_idx, new_entry_price, d["direction"], BASE_SL, BASE_TG)
            if r == "win":
                flips += 1
        print(f"{delay:>6}c{n_valid:>7}{wins:>7}{losses:>7}{timeouts:>9}{wr:>9.1f}%{edge:>+8.3f}%"
              f"{flips:>10}/{flip_denom}")


if __name__ == "__main__":
    main()
