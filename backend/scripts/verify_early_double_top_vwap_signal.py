"""Tests the "item 1" early-recommendation idea: instead of waiting for
detect_double_top's own 5-candle-lagged swing confirmation, fire an EARLY
candidate the moment (a) a prior top already showed a clean VWAP rejection
down (vwap_rejection_bear, real-time, no lag) and (b) price comes back up
near that same level and rejects from VWAP again — i.e. the second top's
own VWAP rejection, checked in real time as it happens, not 5 candles
after the fact.

Methodology:
  1. Replay real HINDCOPPER 1min candles through the real ActivityEngine
     (for its real vwap_rejection_bear detection -- reused directly, not
     reimplemented) while independently tracking the same swing-point
     sequence detect_double_top itself uses (matching
     verify_double_top_depth_filter.py's approach).
  2. Whenever a fresh (high=a, low=b) pair appears at the tail of the
     swing-point sequence (a confirmed top followed by a confirmed
     neckline low -- the same geometry detect_double_top needs before its
     own 3rd point), START WATCHING from the very next candle: the first
     candle where price's high comes back within _DOUBLE_SIMILARITY of a's
     own price AND vwap_rejection_bear fires on that SAME candle is the
     EARLY CANDIDATE -- real, current-candle entry price, no stale-price
     issue at all (see [[stale_entry_price_bug]]).
  3. Keep watching that same (a, b) pair afterward for its real outcome:
     CONFIRMED if a later high c makes detect_double_top([a,b,c]) true,
     INVALIDATED if a new low breaks below b first, TIMEOUT after
     MAX_WATCH_CANDLES with neither.
  4. Report: how many early candidates fired, precision (confirmed vs
     invalidated/timeout), how many candles earlier the early candidate
     fired vs the eventual official confirmation (lead time), and the
     honest SL/TG race win rate trading AT the early candidate's own real
     entry price -- compared directly against the baseline (real entry
     price at OFFICIAL confirmation, no early signal) from earlier in this
     investigation.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_early_double_top_vwap_signal.py
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
MAX_WATCH_CANDLES = 120  # give up watching a (a,b) pair after this many candles
MAX_RACE_CANDLES = 120
SL_PCT, TG_PCT = 0.0025, 0.005  # same baseline used throughout this investigation


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
    print(f"{SYMBOL}: {len(candles)} historical {TIMEFRAME} candles loaded\n", flush=True)

    activity_engine = ActivityEngine(session_factory)
    swing_window = deque(maxlen=2 * _SWING_LOOKBACK + 1)
    points: list[SwingPoint] = []

    watching = None  # {"a": SwingPoint, "b": SwingPoint, "since": i, "early_fired": bool}
    early_candidates = []  # each: dict with a,b,early_index,early_price,outcome ("confirmed"/"invalidated"/"timeout"),confirmed_index

    for i, candle in enumerate(candles):
        activities = activity_engine.on_candle_closed(SYMBOL, exchange_segment, candle)
        activity_names = {a["activity"] for a in activities}

        # --- resolve any active watch session against THIS candle first
        if watching is not None:
            a, b = watching["a"], watching["b"]
            age = i - watching["since"]

            if not watching["early_fired"]:
                near_first_top = abs(candle.high - a.price) / a.price <= _DOUBLE_SIMILARITY if a.price else False
                if near_first_top and "vwap_rejection_bear" in activity_names:
                    watching["early_fired"] = True
                    early_candidates.append({
                        "a": a, "b": b, "early_index": i, "early_price": float(candle.close),
                        "outcome": None, "confirmed_index": None,
                    })

            if age > MAX_WATCH_CANDLES:
                if watching["early_fired"]:
                    early_candidates[-1]["outcome"] = "timeout"
                watching = None

        # --- independent swing-point replay (same as verify_double_top_depth_filter.py)
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
                        early_candidates[-1]["outcome"] = "invalidated"
                        watching = None
                    elif kind_name == "high" and detect_double_top([a, b, points[-1]]):
                        early_candidates[-1]["outcome"] = "confirmed"
                        early_candidates[-1]["confirmed_index"] = i
                        watching = None

                # start (or restart) a watch whenever a fresh (high, low) pair appears
                if watching is None and len(points) >= 2 and points[-2].kind == "high" and points[-1].kind == "low":
                    watching = {"a": points[-2], "b": points[-1], "since": i, "early_fired": False}

        if (i + 1) % 40_000 == 0:
            print(f"  ...{i + 1}/{len(candles)} candles scanned, {len(early_candidates)} early candidates so far", flush=True)

    # any still-open watch at the end of data counts as timeout for its fired candidate
    if watching is not None and watching["early_fired"] and early_candidates[-1]["outcome"] is None:
        early_candidates[-1]["outcome"] = "timeout"

    print(f"\n{len(early_candidates)} early candidates fired\n", flush=True)

    confirmed = [d for d in early_candidates if d["outcome"] == "confirmed"]
    invalidated = [d for d in early_candidates if d["outcome"] == "invalidated"]
    timeout = [d for d in early_candidates if d["outcome"] == "timeout"]
    print(f"confirmed (real double_top eventually formed): {len(confirmed)} "
          f"({len(confirmed)/len(early_candidates)*100:.1f}%)")
    print(f"invalidated (neckline broke first): {len(invalidated)} ({len(invalidated)/len(early_candidates)*100:.1f}%)")
    print(f"timeout (never resolved): {len(timeout)} ({len(timeout)/len(early_candidates)*100:.1f}%)")

    if confirmed:
        lead_times = [d["confirmed_index"] - d["early_index"] for d in confirmed]
        lead_times.sort()
        n = len(lead_times)
        print(f"\nlead time (candles earlier than official confirmation), confirmed only:")
        print(f"  median={lead_times[n//2]}  p25={lead_times[n//4]}  p75={lead_times[3*n//4]}  "
              f"min={lead_times[0]}  max={lead_times[-1]}")

    # honest SL/TG race, trading EVERY early candidate at its own real entry price
    print(f"\n=== honest SL/TG race trading the early candidate's own real entry price ===")
    wins = losses = timeouts_race = 0
    for d in early_candidates:
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
    print(f"N={len(early_candidates)}  win={wins}  loss={losses}  timeout={timeouts_race}  "
          f"win_ratio={wr:.1f}%  edge={edge:+.3f}%")
    print("(baseline from yesterday, real entry at OFFICIAL confirmation, same SL/TG: "
          "N=3554, win_ratio=35.1%, edge=+0.013%)")


if __name__ == "__main__":
    main()
