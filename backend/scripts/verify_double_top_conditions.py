"""What indicator conditions, AT THE REAL DETECTION CANDLE (not the stale
swing-candle timestamp PatternOutcome itself uses for structure patterns --
see the stale-entry-price bug already found in this investigation), separate
double_top trades that go STRAIGHT to target from ones that FLUCTUATE
(bounce through the stop first, even if price eventually proves the call
right) from ones that are just WRONG?

Groups (same baseline SL 0.25% / TG 0.50% as the rest of this investigation):
  straight  -- target hit before stop (a real win)
  rescue    -- stop hit first, but price eventually reached +0.5% anyway
               (the whipsaw group from the earlier cross-tab)
  dead      -- stop hit first, price never got there at all
  timeout   -- neither hit within the window

Indicator values are looked up directly from CandleIndicators at the REAL
entry candle's own timestamp -- NOT PatternOutcome.entry_rsi/etc, which for
double_top is keyed to the stale swing-candle ts and would silently pull
the WRONG candle's indicator snapshot.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_double_top_conditions.py
"""
from __future__ import annotations

import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine
from brokers.models import Candle
from db.models import CandleIndicators, SubscribedSymbol
from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory
from indicators import classify_macd, classify_rsi, classify_stochastic
from order_backtest import fixed_pct_levels

SYMBOL = "HINDCOPPER"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
MAX_CANDLES = 120
SL_PCT, TG_PCT = 0.0025, 0.005


def classify_outcome(candles, entry_index, entry_price, direction) -> str:
    stop_price, target_price = fixed_pct_levels(entry_price, direction, SL_PCT, TG_PCT)
    entry_day = candles[entry_index].timestamp.date()
    race_result = None
    extreme = entry_price
    ever = False
    for j in range(entry_index + 1, min(entry_index + 1 + MAX_CANDLES, len(candles))):
        c = candles[j]
        if c.timestamp.date() != entry_day:
            break
        if race_result is None:
            hit_target = c.low <= target_price <= c.high
            hit_stop = c.low <= stop_price <= c.high
            if hit_stop:
                race_result = "loss"
            elif hit_target:
                race_result = "win"
        extreme = min(extreme, c.low) if direction == "bear" else max(extreme, c.high)
        move = (entry_price - extreme) / entry_price if direction == "bear" else (extreme - entry_price) / entry_price
        if move >= TG_PCT:
            ever = True
    if race_result == "win":
        return "straight"
    if race_result == "loss":
        return "rescue" if ever else "dead"
    return "timeout"


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    with session_factory() as session:
        symbol_row = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one()
        exchange_segment = symbol_row.exchange_segment
        iid = symbol_row.id
        historical = LibCandlesHistorical.get_range(session, SYMBOL, exchange_segment, TIMEFRAME, start, end)

        indicator_rows = session.query(CandleIndicators).filter_by(
            instrument_id=iid, timeframe=TIMEFRAME).all()
        indicators_by_ts = {r.ts.replace(tzinfo=timezone.utc) if r.ts.tzinfo is None else r.ts: r
                             for r in indicator_rows}

    candles = [
        Candle(symbol=SYMBOL, timeframe=TIMEFRAME, timestamp=r.ts, open=float(r.open_price),
               high=float(r.high_price), low=float(r.low_price), close=float(r.close_price), volume=r.volume)
        for r in historical
    ]
    print(f"{SYMBOL}: {len(candles)} candles, {len(indicators_by_ts)} indicator rows loaded\n", flush=True)

    activity_engine = ActivityEngine(session_factory)
    dets = []
    for i, candle in enumerate(candles):
        for a in activity_engine.on_candle_closed(SYMBOL, exchange_segment, candle):
            if a["activity"] == "double_top":
                dets.append({"entry_index": i, "entry_price": float(candle.close), "direction": "bear"})
    print(f"{len(dets)} double_top detections\n", flush=True)

    groups: dict[str, list[dict]] = {"straight": [], "rescue": [], "dead": [], "timeout": []}
    missing_indicators = 0
    for d in dets:
        outcome = classify_outcome(candles, d["entry_index"], d["entry_price"], d["direction"])
        ts = candles[d["entry_index"]].timestamp
        ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        ind = indicators_by_ts.get(ts)
        if ind is None:
            missing_indicators += 1
            continue
        atr_pct = (float(ind.atr) / d["entry_price"] * 100) if ind.atr is not None else None
        groups[outcome].append({
            "rsi": float(ind.rsi) if ind.rsi is not None else None,
            "macd_line": float(ind.macd_line) if ind.macd_line is not None else None,
            "macd_signal": float(ind.macd_signal) if ind.macd_signal is not None else None,
            "stoch_k": float(ind.stoch_k) if ind.stoch_k is not None else None,
            "atr_pct": atr_pct,
        })

    print(f"(skipped {missing_indicators} detections with no indicator row at the entry candle -- warm-up period)\n")

    print(f"{'Group':<10}{'N':>6}{'AvgRSI':>9}{'RSI<40':>8}{'RSI 40-60':>10}{'RSI>60':>8}"
          f"{'MACD bear%':>12}{'AvgStochK':>11}{'AvgATR%':>10}")
    print("-" * 84)
    for label in ("straight", "rescue", "dead", "timeout"):
        rows = groups[label]
        n = len(rows)
        if n == 0:
            print(f"{label:<10}{0:>6}")
            continue
        rsi_vals = [r["rsi"] for r in rows if r["rsi"] is not None]
        avg_rsi = statistics.mean(rsi_vals) if rsi_vals else None
        rsi_low = sum(1 for v in rsi_vals if v < 40) / len(rsi_vals) * 100 if rsi_vals else 0
        rsi_mid = sum(1 for v in rsi_vals if 40 <= v <= 60) / len(rsi_vals) * 100 if rsi_vals else 0
        rsi_high = sum(1 for v in rsi_vals if v > 60) / len(rsi_vals) * 100 if rsi_vals else 0
        macd_states = [classify_macd(r["macd_line"], r["macd_signal"]) for r in rows]
        macd_bear_pct = sum(1 for s in macd_states if s == "bearish") / len(macd_states) * 100 if macd_states else 0
        stoch_vals = [r["stoch_k"] for r in rows if r["stoch_k"] is not None]
        avg_stoch = statistics.mean(stoch_vals) if stoch_vals else None
        atr_vals = [r["atr_pct"] for r in rows if r["atr_pct"] is not None]
        avg_atr = statistics.mean(atr_vals) if atr_vals else None

        print(f"{label:<10}{n:>6}"
              f"{(f'{avg_rsi:.1f}' if avg_rsi is not None else 'n/a'):>9}"
              f"{rsi_low:>7.1f}%{rsi_mid:>9.1f}%{rsi_high:>7.1f}%"
              f"{macd_bear_pct:>11.1f}%"
              f"{(f'{avg_stoch:.1f}' if avg_stoch is not None else 'n/a'):>11}"
              f"{(f'{avg_atr:.3f}%' if avg_atr is not None else 'n/a'):>10}")

    # Direct straight-vs-rescue comparison (the pair that actually matters --
    # both are the SAME favorable directional call, differing only in
    # whether the stop got tagged first).
    print("\n=== straight vs rescue: ATR%% distribution (volatility at formation time) ===")
    for label in ("straight", "rescue"):
        atr_vals = [r["atr_pct"] for r in groups[label] if r["atr_pct"] is not None]
        if not atr_vals:
            continue
        atr_vals.sort()
        n = len(atr_vals)
        print(f"{label:<10} n={n:<6} median={statistics.median(atr_vals):.3f}%  "
              f"p25={atr_vals[n//4]:.3f}%  p75={atr_vals[3*n//4]:.3f}%  "
              f"mean={statistics.mean(atr_vals):.3f}%")


if __name__ == "__main__":
    main()
