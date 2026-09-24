"""One-off analysis: simulates a real stop-loss/target trade for every
historical double_top occurrence, instead of LibPatternOutcomes' fixed-
checkpoint sign-agreement rule — walks forward through REAL candle highs/
lows (not just closes) to see which level would actually have been hit
first. Tests several sl_pct/tg_pct combinations in one pass over the same
detection replay double_top_gap_analysis.py already built.

Same-candle-hits-both-levels convention: assumes the stop was hit first
(the conservative, standard backtesting assumption without tick data) —
stated explicitly here since it materially affects results on volatile
candles.

Caps the forward walk at MAX_CANDLES or the end of the entry candle's own
trading day, whichever comes first — a short intraday SL/TG shouldn't be
graded against a trade nobody would actually still be holding by tomorrow.

Run manually:
    .venv/Scripts/python.exe backend/scripts/double_top_sltg_backtest.py [SYMBOL]
"""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import SwingPoint, _SWING_LOOKBACK, detect_double_top, detect_swing_high, detect_swing_low
from brokers.models import Candle
from db.models import SubscribedSymbol
from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory

TIMEFRAME = "1min"
MAX_CANDLES = 120  # ~2 hours on 1min — generous for a 0.2-0.6% intraday target

SL_TG_COMBOS = [(0.2, 0.5), (0.25, 0.5), (0.25, 0.55), (0.25, 0.6), (0.3, 0.6)]


def find_detections(candles: list[Candle]) -> list[dict]:
    """Same replay as double_top_gap_analysis.py — returns each double_top
    detection's own candle index (for the forward SL/TG walk) and entry
    price (the second top's own close, matching recommendation_engine's
    real entry_price convention: activity["close_price"])."""
    window = deque(maxlen=2 * _SWING_LOOKBACK + 1)
    points: list[SwingPoint] = []
    point_index: list[int] = []
    detections = []

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
                c_idx = point_index[-1]
                detections.append({
                    "detected_ts": points[-1].candle.timestamp,
                    "entry_index": c_idx,
                    "entry_price": candles[c_idx].close,
                })
    return detections


def simulate(candles: list[Candle], entry_index: int, entry_price: float, sl_pct: float, tg_pct: float) -> dict:
    """Bear trade (double_top): stop ABOVE entry, target BELOW entry.
    Returns {"result": "win"|"loss"|"timeout", "close_pct": ...} — close_pct
    is always populated (the actual pct_change at whichever candle ends the
    walk: the hit candle's own close for win/loss, or the last-scanned
    candle's close for a timeout) so a timeout's real, if-you'd-closed-it-
    then outcome is never silently discarded."""
    stop_price = entry_price * (1 + sl_pct / 100)
    target_price = entry_price * (1 - tg_pct / 100)
    entry_day = candles[entry_index].timestamp.date()

    last_seen = candles[entry_index]
    for j in range(entry_index + 1, min(entry_index + 1 + MAX_CANDLES, len(candles))):
        c = candles[j]
        if c.timestamp.date() != entry_day:
            break
        last_seen = c
        hit_stop = c.high >= stop_price
        hit_target = c.low <= target_price
        if hit_stop:  # same-candle ambiguity resolved conservatively: stop first
            return {"result": "loss", "close_pct": (c.close - entry_price) / entry_price}
        if hit_target:
            return {"result": "win", "close_pct": (c.close - entry_price) / entry_price}
    return {"result": "timeout", "close_pct": (last_seen.close - entry_price) / entry_price}


def main() -> None:
    symbol_arg = sys.argv[1] if len(sys.argv) > 1 else "HINDCOPPER"
    engine = build_engine()
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        symbol_row = session.query(SubscribedSymbol).filter_by(symbol=symbol_arg).one()
        historical = LibCandlesHistorical.get_range(session, symbol_arg, symbol_row.exchange_segment, TIMEFRAME)

    candles = [
        Candle(symbol=symbol_arg, timeframe=TIMEFRAME, timestamp=r.ts,
               open=float(r.open_price), high=float(r.high_price),
               low=float(r.low_price), close=float(r.close_price), volume=r.volume)
        for r in historical
    ]
    print(f"{symbol_arg}: {len(candles)} historical {TIMEFRAME} candles loaded")

    detections = find_detections(candles)
    print(f"{len(detections)} double_top detections found\n")

    print("=== Decided-only (timeouts excluded, as before) ===")
    print(f"{'SL%':>6}{'TG%':>6}{'RR':>6}{'N':>7}{'Win':>7}{'Loss':>7}{'Timeout':>9}"
          f"{'WinRate':>10}{'Expectancy%':>13}")
    print("-" * 71)

    all_sims = {}  # (sl,tg) -> list of sim dicts, reused below for the timeout breakdown
    for sl_pct, tg_pct in SL_TG_COMBOS:
        sims = [simulate(candles, d["entry_index"], d["entry_price"], sl_pct, tg_pct) for d in detections]
        all_sims[(sl_pct, tg_pct)] = sims
        results = {"win": 0, "loss": 0, "timeout": 0}
        for s in sims:
            results[s["result"]] += 1

        decided = results["win"] + results["loss"]
        win_rate = results["win"] / decided * 100 if decided else 0.0
        expectancy = (results["win"] * tg_pct - results["loss"] * sl_pct) / decided if decided else 0.0
        rr = tg_pct / sl_pct
        print(f"{sl_pct:>6.2f}{tg_pct:>6.2f}{rr:>6.2f}{len(detections):>7}"
              f"{results['win']:>7}{results['loss']:>7}{results['timeout']:>9}"
              f"{win_rate:>9.1f}%{expectancy:>12.3f}%")

    print("\n=== What actually happened to the timeouts (marked to their own cutoff close) ===")
    print(f"{'SL%':>6}{'TG%':>6}{'Timeout N':>11}{'StillFavor':>12}{'Adverse':>9}{'AvgClose%':>11}")
    print("-" * 60)
    for sl_pct, tg_pct in SL_TG_COMBOS:
        timeouts = [s for s in all_sims[(sl_pct, tg_pct)] if s["result"] == "timeout"]
        if not timeouts:
            print(f"{sl_pct:>6.2f}{tg_pct:>6.2f}{0:>11}")
            continue
        favorable = sum(1 for s in timeouts if s["close_pct"] < 0)  # bear: negative = still moving down
        adverse = sum(1 for s in timeouts if s["close_pct"] > 0)
        avg_close_pct = sum(s["close_pct"] for s in timeouts) / len(timeouts) * 100
        print(f"{sl_pct:>6.2f}{tg_pct:>6.2f}{len(timeouts):>11}{favorable:>12}{adverse:>9}{avg_close_pct:>10.3f}%")

    print("\n=== All-inclusive (timeouts marked to cutoff close, not excluded) ===")
    print(f"{'SL%':>6}{'TG%':>6}{'N':>7}{'WinRate':>10}{'Expectancy%':>13}")
    print("-" * 46)
    for sl_pct, tg_pct in SL_TG_COMBOS:
        sims = all_sims[(sl_pct, tg_pct)]
        soft_wins = sum(1 for s in sims if s["result"] == "win" or (s["result"] == "timeout" and s["close_pct"] < 0))
        win_rate_all = soft_wins / len(sims) * 100
        # Win/loss use their real +tg%/-sl%; a timeout is marked to its actual
        # close_pct instead — no more excluding it as "undecided".
        total_pct = sum(
            tg_pct if s["result"] == "win" else -sl_pct if s["result"] == "loss" else s["close_pct"] * 100
            for s in sims
        )
        expectancy_all = total_pct / len(sims)
        print(f"{sl_pct:>6.2f}{tg_pct:>6.2f}{len(sims):>7}{win_rate_all:>9.1f}%{expectancy_all:>12.3f}%")


if __name__ == "__main__":
    main()
