"""What separates a WINNING double_top_ed trade from a LOSING one? Same
technique as yesterday's "straight vs rescue" indicator comparison, applied
to the real double_top_ed candidates from the PRODUCTION detector (not a
standalone replay) -- RSI/MACD/Stochastic/ATR value+state at the signal's
own real candle, plus volume and day-of-week/time-of-day, searched broadly
for anything that discriminates win from loss.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_double_top_ed_loss_discriminators.py
"""
from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
import sys
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
EXCHANGE_SEGMENT = "NSE_EQ"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
MAX_RACE_CANDLES = 120
SL_PCT, TG_PCT = 0.0025, 0.005
IST_OFFSET = timedelta(hours=5, minutes=30)


def race(candles, entry_index, entry_price) -> str:
    stop_price, target_price = fixed_pct_levels(entry_price, "bear", SL_PCT, TG_PCT)
    entry_day = candles[entry_index].timestamp.date()
    for j in range(entry_index + 1, min(entry_index + 1 + MAX_RACE_CANDLES, len(candles))):
        c = candles[j]
        if c.timestamp.date() != entry_day:
            break
        if c.low <= stop_price <= c.high:
            return "loss"
        if c.low <= target_price <= c.high:
            return "win"
    return "timeout"


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    with session_factory() as session:
        row = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one()
        iid, exchange_segment = row.id, row.exchange_segment
        historical = LibCandlesHistorical.get_range(session, SYMBOL, exchange_segment, TIMEFRAME, start, end)
        indicator_rows = session.query(CandleIndicators).filter_by(instrument_id=iid, timeframe=TIMEFRAME).all()

    candles = [
        Candle(symbol=SYMBOL, timeframe=TIMEFRAME,
               timestamp=(r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)),
               open=float(r.open_price), high=float(r.high_price), low=float(r.low_price),
               close=float(r.close_price), volume=r.volume)
        for r in historical
    ]
    ts_to_index = {c.timestamp: i for i, c in enumerate(candles)}
    indicators_by_index = {}
    for r in indicator_rows:
        ts = r.ts.replace(tzinfo=timezone.utc) if r.ts.tzinfo is None else r.ts
        idx = ts_to_index.get(ts)
        if idx is not None:
            indicators_by_index[idx] = r
    print(f"{SYMBOL}: {len(candles)} candles, {len(indicators_by_index)} with indicators\n", flush=True)

    engine = ActivityEngine(session_factory)
    detections = []
    for i, candle in enumerate(candles):
        for a in engine.on_candle_closed(SYMBOL, exchange_segment, candle):
            if a["activity"] == "double_top_ed":
                detections.append({"index": i, "price": float(candle.close), "ts": candle.timestamp})
    print(f"{len(detections)} double_top_ed candidates (production detector)\n", flush=True)

    groups = {"win": [], "loss": [], "timeout": []}
    for d in detections:
        r = race(candles, d["index"], d["price"])
        ind = indicators_by_index.get(d["index"])
        ist_ts = d["ts"] + IST_OFFSET
        row = {
            "rsi": float(ind.rsi) if ind and ind.rsi is not None else None,
            "macd_line": float(ind.macd_line) if ind and ind.macd_line is not None else None,
            "macd_signal": float(ind.macd_signal) if ind and ind.macd_signal is not None else None,
            "stoch_k": float(ind.stoch_k) if ind and ind.stoch_k is not None else None,
            "atr": float(ind.atr) if ind and ind.atr is not None else None,
            "atr_pct": (float(ind.atr) / d["price"] * 100) if ind and ind.atr is not None else None,
            "ma21": float(ind.ma21) if ind and ind.ma21 is not None else None,
            "ma50": float(ind.ma50) if ind and ind.ma50 is not None else None,
            "volume": candles[d["index"]].volume,
            "hour": ist_ts.hour,
            "weekday": ist_ts.weekday(),
        }
        groups[r].append(row)

    print(f"win={len(groups['win'])}  loss={len(groups['loss'])}  timeout={len(groups['timeout'])}\n")

    def _stats(rows, key):
        vals = [r[key] for r in rows if r[key] is not None]
        return (statistics.mean(vals), statistics.median(vals)) if vals else (None, None)

    print(f"{'Feature':<14}{'Win(mean/med)':>22}{'Loss(mean/med)':>22}")
    print("-" * 58)
    for key in ("rsi", "stoch_k", "atr_pct", "volume"):
        w_mean, w_med = _stats(groups["win"], key)
        l_mean, l_med = _stats(groups["loss"], key)
        w_str = f"{w_mean:.2f}/{w_med:.2f}" if w_mean is not None else "n/a"
        l_str = f"{l_mean:.2f}/{l_med:.2f}" if l_mean is not None else "n/a"
        print(f"{key:<14}{w_str:>22}{l_str:>22}")

    # MACD/RSI/Stochastic classified STATE distribution
    for state_name, classify_row in (
        ("rsi_state", lambda r: classify_rsi(r["rsi"])),
        ("macd_state", lambda r: classify_macd(r["macd_line"], r["macd_signal"])),
        ("stoch_state", lambda r: classify_stochastic(r["stoch_k"])),
    ):
        print(f"\n{state_name} distribution:")
        for label in ("win", "loss"):
            counts = Counter(classify_row(r) for r in groups[label])
            total = sum(counts.values()) or 1
            parts = ", ".join(f"{k}={v} ({v/total*100:.1f}%)" for k, v in counts.most_common())
            print(f"  {label:<6}: {parts}")

    # MA21 vs MA50 trend context (above/below)
    print("\nma21 vs ma50 (short-term trend context):")
    for label in ("win", "loss"):
        rows = groups[label]
        above = sum(1 for r in rows if r["ma21"] is not None and r["ma50"] is not None and r["ma21"] > r["ma50"])
        below = sum(1 for r in rows if r["ma21"] is not None and r["ma50"] is not None and r["ma21"] <= r["ma50"])
        total = above + below or 1
        print(f"  {label:<6}: ma21>ma50={above} ({above/total*100:.1f}%)  ma21<=ma50={below} ({below/total*100:.1f}%)")

    # hour-of-day distribution
    print("\nhour-of-day (IST) distribution:")
    for label in ("win", "loss"):
        counts = Counter(r["hour"] for r in groups[label])
        total = sum(counts.values()) or 1
        parts = ", ".join(f"{h}h={v}({v/total*100:.0f}%)" for h, v in sorted(counts.items()))
        print(f"  {label:<6}: {parts}")

    # weekday distribution
    print("\nweekday distribution (0=Mon):")
    for label in ("win", "loss"):
        counts = Counter(r["weekday"] for r in groups[label])
        total = sum(counts.values()) or 1
        parts = ", ".join(f"{d}={v}({v/total*100:.0f}%)" for d, v in sorted(counts.items()))
        print(f"  {label:<6}: {parts}")


if __name__ == "__main__":
    main()
