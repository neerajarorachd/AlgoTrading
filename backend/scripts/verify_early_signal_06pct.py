"""Adds a stricter VWAP-gap condition to the early-recommendation idea --
explicit instruction 2026-09-18: "vwap - BBB > .6% (higher than our .5%
target). similarly for bullish reversal BBU-VWAP > .6%" -- i.e. require the
rejection candle's own gap from VWAP to exceed 0.6%, wider than the 0.5%
target itself (so a rejection this clean is already bigger than the whole
profit target before the trade even starts). Tests BOTH double_top
(bearish, gap = (vwap - candle.high)/vwap) and its mirror double_bottom
(bullish, gap = (candle.low - vwap)/vwap), at 0.5% (activity_engine's own
existing "_strong" gate, for reference) and 0.6% (the new ask), using the
REAL per-candle vwap from CandleIndicators (not activity_engine's own
baked-in 0.5% "_strong" category) so the exact threshold can be tested
directly via detect_vwap_rejection_bear/bull's own min_gap_pct parameter.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_early_signal_06pct.py
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

from activity_engine import (
    SwingPoint, _DOUBLE_SIMILARITY, _SWING_LOOKBACK,
    detect_double_bottom, detect_double_top, detect_swing_high, detect_swing_low,
    detect_vwap_rejection_bear, detect_vwap_rejection_bull,
)
from brokers.models import Candle
from db.models import CandleIndicators, SubscribedSymbol
from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory
from order_backtest import fixed_pct_levels

SYMBOL = "HINDCOPPER"
EXCHANGE_SEGMENT = "NSE_EQ"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
MAX_WATCH_CANDLES = 120
MAX_RACE_CANDLES = 120
SL_PCT, TG_PCT = 0.0025, 0.005


def race(candles: list[Candle], entry_index: int, entry_price: float, direction: str) -> str:
    stop_price, target_price = fixed_pct_levels(entry_price, direction, SL_PCT, TG_PCT)
    entry_day = candles[entry_index].timestamp.date()
    for j in range(entry_index + 1, min(entry_index + 1 + MAX_RACE_CANDLES, len(candles))):
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


def find_candidates(candles, vwap_by_index: dict[int, float], pattern: str, min_gap_pct: float) -> list[dict]:
    """pattern: 'double_top' (bearish) or 'double_bottom' (bullish)."""
    is_top = pattern == "double_top"
    kind_a, kind_b = ("high", "low") if is_top else ("low", "high")
    direction = "bear" if is_top else "bull"
    detect_fn = detect_double_top if is_top else detect_double_bottom
    rejection_fn = detect_vwap_rejection_bear if is_top else detect_vwap_rejection_bull

    swing_window = deque(maxlen=2 * _SWING_LOOKBACK + 1)
    recent2: deque[Candle] = deque(maxlen=2)
    points: list[SwingPoint] = []
    watching = None
    out = []

    for i, candle in enumerate(candles):
        recent2.append(candle)
        vwap = vwap_by_index.get(i)

        if watching is not None:
            a, b = watching["a"], watching["b"]
            age = i - watching["since"]
            if not watching["early_fired"]:
                ref_price = candle.high if is_top else candle.low
                near_first = abs(ref_price - a.price) / a.price <= _DOUBLE_SIMILARITY if a.price else False
                rejected = vwap is not None and len(recent2) == 2 and rejection_fn(list(recent2), vwap, min_gap_pct)
                if near_first and rejected:
                    watching["early_fired"] = True
                    out.append({"a": a, "b": b, "early_index": i, "early_price": float(candle.close),
                                "outcome": None, "confirmed_index": None})
            if age > MAX_WATCH_CANDLES:
                if watching["early_fired"]:
                    out[-1]["outcome"] = "timeout"
                watching = None

        swing_window.append(candle)
        if len(swing_window) == swing_window.maxlen:
            window_list = list(swing_window)
            for kind_check, kind_name in ((detect_swing_high, "high"), (detect_swing_low, "low")):
                if not kind_check(window_list, _SWING_LOOKBACK):
                    continue
                swing_candle = window_list[_SWING_LOOKBACK]
                price = swing_candle.high if kind_name == "high" else swing_candle.low
                points.append(SwingPoint(kind=kind_name, price=price, candle=swing_candle))

                if watching is not None and watching["early_fired"]:
                    a, b = watching["a"], watching["b"]
                    invalidating_kind = kind_b  # a new point of b's own kind, beyond b, invalidates
                    if kind_name == invalidating_kind:
                        # double_top's b is the neckline LOW -- invalidated by breaking BELOW it;
                        # double_bottom's b is the neckline HIGH -- invalidated by breaking ABOVE it.
                        broke = price < b.price if is_top else price > b.price
                        if broke:
                            out[-1]["outcome"] = "invalidated"
                            watching = None
                    if watching is not None and kind_name == kind_a and detect_fn([a, b, points[-1]]):
                        out[-1]["outcome"] = "confirmed"
                        out[-1]["confirmed_index"] = i
                        watching = None

                if watching is None and len(points) >= 2 and points[-2].kind == kind_a and points[-1].kind == kind_b:
                    watching = {"a": points[-2], "b": points[-1], "since": i, "early_fired": False}

    if watching is not None and watching["early_fired"] and out and out[-1]["outcome"] is None:
        out[-1]["outcome"] = "timeout"
    return out, direction


def report(label: str, candidates: list[dict], direction: str, candles: list[Candle]) -> None:
    n = len(candidates) or 1
    confirmed = sum(1 for d in candidates if d["outcome"] == "confirmed")
    invalidated = sum(1 for d in candidates if d["outcome"] == "invalidated")
    timeout = sum(1 for d in candidates if d["outcome"] == "timeout")

    wins = losses = timeouts_race = 0
    for d in candidates:
        r = race(candles, d["early_index"], d["early_price"], direction)
        if r == "win":
            wins += 1
        elif r == "loss":
            losses += 1
        else:
            timeouts_race += 1
    decided = wins + losses
    wr = wins / decided * 100 if decided else 0.0
    edge = (wins / decided * TG_PCT - losses / decided * SL_PCT) * 100 if decided else 0.0
    print(f"{label:<45}N={len(candidates):<6}confirmed={confirmed/n*100:5.1f}%  "
          f"win_ratio={wr:5.1f}%  edge={edge:+.3f}%")


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    with session_factory() as session:
        row = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one()
        exchange_segment = row.exchange_segment
        historical = LibCandlesHistorical.get_range(session, SYMBOL, exchange_segment, TIMEFRAME, start, end)
        indicator_rows = session.query(CandleIndicators).filter_by(
            instrument_id=row.id, timeframe=TIMEFRAME).all()

    candles = [
        Candle(symbol=SYMBOL, timeframe=TIMEFRAME,
               timestamp=(r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)),
               open=float(r.open_price), high=float(r.high_price), low=float(r.low_price),
               close=float(r.close_price), volume=r.volume)
        for r in historical
    ]
    ts_to_index = {c.timestamp: i for i, c in enumerate(candles)}
    vwap_by_index: dict[int, float] = {}
    for r in indicator_rows:
        ts = r.ts.replace(tzinfo=timezone.utc) if r.ts.tzinfo is None else r.ts
        idx = ts_to_index.get(ts)
        if idx is not None and r.vwap is not None:
            vwap_by_index[idx] = float(r.vwap)

    print(f"{SYMBOL}: {len(candles)} candles, {len(vwap_by_index)} with real vwap\n", flush=True)

    for pattern in ("double_top", "double_bottom"):
        print(f"--- {pattern} ---")
        for min_gap_pct in (0.005, 0.006):
            candidates, direction = find_candidates(candles, vwap_by_index, pattern, min_gap_pct)
            report(f"  gap >= {min_gap_pct*100:.1f}%", candidates, direction, candles)
        print()

    print("(baseline for reference: double_top official confirmation, real entry: N=3554, win_ratio=35.1%, edge=+0.013%)")


if __name__ == "__main__":
    main()
