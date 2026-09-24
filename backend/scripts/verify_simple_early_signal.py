"""Compares two flavors of "Type 2: early recommendation" for double_top:
  A) yesterday's version -- fires on the first candle where price is near
     the first top's level AND vwap_rejection_bear fires (2 consecutive
     candles below VWAP).
  B) today's simpler version -- fires on the first SINGLE candle that is
     just bearish (close < open) and near the first top's level, no VWAP
     condition at all -- "after 1 bullish/bearish candle" per the user's
     own framing, generalized: once (a, b) -- the top and the neckline --
     are already confirmed, don't wait 5 more candles for the 2nd top to
     also get officially confirmed; fire on the very first directionally-
     consistent candle instead.

Same precision/lead-time/honest-SL-TG-race metrics as
verify_early_double_top_vwap_signal.py, so A and B are directly comparable
on equal footing.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_simple_early_signal.py
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
    ActivityEngine, SwingPoint, _DOUBLE_SIMILARITY, _SWING_LOOKBACK,
    detect_double_top, detect_swing_high, detect_swing_low,
)
from brokers.models import Candle
from db.models import SubscribedSymbol
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


def race(candles: list[Candle], entry_index: int, entry_price: float) -> str:
    stop_price, target_price = fixed_pct_levels(entry_price, "bear", SL_PCT, TG_PCT)
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


def find_candidates(candles, activity_engine, exchange_segment, require_vwap: bool) -> list[dict]:
    swing_window = deque(maxlen=2 * _SWING_LOOKBACK + 1)
    points: list[SwingPoint] = []
    watching = None
    out = []

    for i, candle in enumerate(candles):
        activity_names = None
        if require_vwap:
            activities = activity_engine.on_candle_closed(SYMBOL, exchange_segment, candle)
            activity_names = {a["activity"] for a in activities}

        if watching is not None:
            a, b = watching["a"], watching["b"]
            age = i - watching["since"]
            if not watching["early_fired"]:
                near_first_top = abs(candle.high - a.price) / a.price <= _DOUBLE_SIMILARITY if a.price else False
                is_bearish_candle = candle.close < candle.open
                condition = near_first_top and is_bearish_candle
                if require_vwap:
                    condition = condition and "vwap_rejection_bear" in activity_names
                if condition:
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
                    if kind_name == "low" and price < b.price:
                        out[-1]["outcome"] = "invalidated"
                        watching = None
                    elif kind_name == "high" and detect_double_top([a, b, points[-1]]):
                        out[-1]["outcome"] = "confirmed"
                        out[-1]["confirmed_index"] = i
                        watching = None

                if watching is None and len(points) >= 2 and points[-2].kind == "high" and points[-1].kind == "low":
                    watching = {"a": points[-2], "b": points[-1], "since": i, "early_fired": False}

    if watching is not None and watching["early_fired"] and out and out[-1]["outcome"] is None:
        out[-1]["outcome"] = "timeout"
    return out


def report(label: str, candidates: list[dict], candles: list[Candle]) -> None:
    print(f"\n=== {label} ===")
    print(f"{len(candidates)} early candidates fired")
    confirmed = [d for d in candidates if d["outcome"] == "confirmed"]
    invalidated = [d for d in candidates if d["outcome"] == "invalidated"]
    timeout = [d for d in candidates if d["outcome"] == "timeout"]
    n = len(candidates) or 1
    print(f"confirmed: {len(confirmed)} ({len(confirmed)/n*100:.1f}%)  "
          f"invalidated: {len(invalidated)} ({len(invalidated)/n*100:.1f}%)  "
          f"timeout: {len(timeout)} ({len(timeout)/n*100:.1f}%)")
    if confirmed:
        lead_times = sorted(d["confirmed_index"] - d["early_index"] for d in confirmed)
        m = len(lead_times)
        print(f"lead time (confirmed only): median={lead_times[m//2]}  p25={lead_times[m//4]}  p75={lead_times[3*m//4]}")

    wins = losses = timeouts_race = 0
    for d in candidates:
        r = race(candles, d["early_index"], d["early_price"])
        if r == "win":
            wins += 1
        elif r == "loss":
            losses += 1
        else:
            timeouts_race += 1
    decided = wins + losses
    wr = wins / decided * 100 if decided else 0.0
    edge = (wins / decided * TG_PCT - losses / decided * SL_PCT) * 100 if decided else 0.0
    print(f"honest SL/TG race: win={wins}  loss={losses}  timeout={timeouts_race}  win_ratio={wr:.1f}%  edge={edge:+.3f}%")


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    with session_factory() as session:
        row = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one()
        exchange_segment = row.exchange_segment
        historical = LibCandlesHistorical.get_range(session, SYMBOL, exchange_segment, TIMEFRAME, start, end)

    candles = [
        Candle(symbol=SYMBOL, timeframe=TIMEFRAME, timestamp=r.ts, open=float(r.open_price),
               high=float(r.high_price), low=float(r.low_price), close=float(r.close_price), volume=r.volume)
        for r in historical
    ]
    print(f"{SYMBOL}: {len(candles)} historical {TIMEFRAME} candles loaded", flush=True)

    activity_engine_a = ActivityEngine(session_factory)
    candidates_a = find_candidates(candles, activity_engine_a, exchange_segment, require_vwap=True)
    report("A) VWAP-gated early signal (yesterday's version)", candidates_a, candles)

    activity_engine_b = ActivityEngine(session_factory)
    candidates_b = find_candidates(candles, activity_engine_b, exchange_segment, require_vwap=False)
    report("B) simple 1-candle early signal (no VWAP requirement)", candidates_b, candles)

    print("\n(baseline: official confirmation only, real entry price: N=3554, win_ratio=35.1%, edge=+0.013%)")


if __name__ == "__main__":
    main()
