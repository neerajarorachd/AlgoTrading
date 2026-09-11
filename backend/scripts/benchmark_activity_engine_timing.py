"""Benchmarks how much wall-clock time each detection category spends on
real backfilled candles_today data, broken down by category (single-candle,
two-candle, three-candle, price-action) and rolled up per stock and overall
across several stocks. Read-only — replays candles the same way
replay_activity_engine.py does, using the exact same detector functions, but
only times detection and never writes to instrument_activity.

    .venv/Scripts/python.exe backend/scripts/benchmark_activity_engine_timing.py [SYMBOL ...]

With no arguments, benchmarks the first 7 active registered symbols.

graph_patterns (double top/bottom, head & shoulders, flags, triangles, etc.)
isn't built yet — swing-high/swing-low detection is a prerequisite that
doesn't exist yet either — so that category always reports zero.
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import (
    SINGLE_CANDLE_PATTERNS,
    THREE_CANDLE_PATTERNS,
    TWO_CANDLE_PATTERNS,
    compute_bollinger,
    detect_bb_squeeze,
    detect_bb_widening,
    detect_price_vwap_divergence,
    detect_vwap_gap_fill,
)
from brokers.models import Candle
from db.models import CandleToday, SubscribedSymbol
from db.session import build_session_factory, build_engine

TIMEFRAMES = ["1min", "3min", "5min"]
CATEGORIES = ["single_candle", "two_candle", "three_candle", "price_action", "graph_patterns"]

# Mirrors ActivityEngine's own lookback sizes (backend/activity_engine.py) —
# duplicated here rather than imported since they're private module
# constants and this is a read-only measurement tool, not a consumer of the
# engine's public contract.
_LOOKBACK = 3
_BB_PERIOD = 20
_BB_WIDTH_LOOKBACK = 20
_VWAP_GAP_LOOKBACK = 10


def _update_vwap(state: dict, candle: Candle) -> float | None:
    day = candle.timestamp.date()
    if state["day"] != day:
        state["day"] = day
        state["cum_pv"] = 0.0
        state["cum_vol"] = 0.0
    if candle.volume:
        typical = (candle.high + candle.low + candle.close) / 3
        state["cum_pv"] += typical * candle.volume
        state["cum_vol"] += candle.volume
    return state["cum_pv"] / state["cum_vol"] if state["cum_vol"] > 0 else None


def benchmark_symbol(symbol: str, exchange_segment: str, session_factory) -> tuple[int, dict, dict]:
    timings: dict = defaultdict(float)
    hits: dict = defaultdict(int)
    n_candles = 0

    for timeframe in TIMEFRAMES:
        with session_factory() as session:
            rows = (
                session.query(CandleToday)
                .filter_by(symbol=symbol, exchange_segment=exchange_segment, timeframe=timeframe)
                .order_by(CandleToday.ts.asc())
                .all()
            )
            candles = [
                Candle(
                    symbol=symbol, timeframe=timeframe, timestamp=r.ts,
                    open=float(r.open_price), high=float(r.high_price),
                    low=float(r.low_price), close=float(r.close_price), volume=r.volume,
                )
                for r in rows
            ]

        recent: deque = deque(maxlen=_LOOKBACK)
        closes: deque = deque(maxlen=_BB_PERIOD)
        widths: deque = deque(maxlen=_BB_WIDTH_LOOKBACK)
        gaps: deque = deque(maxlen=_VWAP_GAP_LOOKBACK)
        vwap_state = {"day": None, "cum_pv": 0.0, "cum_vol": 0.0}

        for candle in candles:
            n_candles += 1
            recent.append(candle)
            candles_list = list(recent)

            t0 = time.perf_counter()
            for detector in SINGLE_CANDLE_PATTERNS.values():
                if detector(candle):
                    hits["single_candle"] += 1
            t1 = time.perf_counter()
            timings["single_candle"] += t1 - t0

            for detector in TWO_CANDLE_PATTERNS.values():
                if detector(candles_list):
                    hits["two_candle"] += 1
            t2 = time.perf_counter()
            timings["two_candle"] += t2 - t1

            for detector in THREE_CANDLE_PATTERNS.values():
                if detector(candles_list):
                    hits["three_candle"] += 1
            t3 = time.perf_counter()
            timings["three_candle"] += t3 - t2

            closes.append(candle.close)
            bb = compute_bollinger(closes)
            if bb is not None:
                widths.append(bb.width)
                if detect_bb_squeeze(widths):
                    hits["price_action"] += 1
                if detect_bb_widening(widths):
                    hits["price_action"] += 1
            vwap = _update_vwap(vwap_state, candle)
            if vwap:
                gaps.append((candle.close - vwap) / vwap)
                if detect_price_vwap_divergence(gaps):
                    hits["price_action"] += 1
                if detect_vwap_gap_fill(gaps):
                    hits["price_action"] += 1
            t4 = time.perf_counter()
            timings["price_action"] += t4 - t3

    timings["graph_patterns"] = 0.0  # not built yet
    return n_candles, timings, hits


def _fmt_ms(seconds: float) -> str:
    return f"{seconds * 1000:.2f}ms"


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)

    requested = sys.argv[1:]
    with session_factory() as session:
        rows = session.query(SubscribedSymbol).filter_by(active=True).order_by(SubscribedSymbol.id.asc()).all()
        if requested:
            targets = [(r.symbol, r.exchange_segment) for r in rows if r.symbol in requested]
        else:
            targets = [(r.symbol, r.exchange_segment) for r in rows[:7]]

    if not targets:
        print("No matching active symbols found.")
        return

    header = f"{'symbol':<12} {'candles':>8} " + " ".join(f"{c:>16}" for c in CATEGORIES) + f" {'total':>12}"
    print(header)
    print("-" * len(header))

    grand_timings: dict = defaultdict(float)
    grand_candles = 0

    for symbol, exchange_segment in targets:
        start = time.perf_counter()
        n_candles, timings, hits = benchmark_symbol(symbol, exchange_segment, session_factory)
        wall = time.perf_counter() - start

        total = sum(timings[c] for c in CATEGORIES)
        grand_candles += n_candles
        for c in CATEGORIES:
            grand_timings[c] += timings[c]

        row = f"{symbol:<12} {n_candles:>8} " + " ".join(f"{_fmt_ms(timings[c]):>16}" for c in CATEGORIES) + f" {_fmt_ms(total):>12}"
        print(row)
        print(f"             (wall clock incl. DB reads: {wall * 1000:.1f}ms; hits: {dict(hits)})")

    grand_total = sum(grand_timings[c] for c in CATEGORIES)
    print("-" * len(header))
    row = f"{'TOTAL':<12} {grand_candles:>8} " + " ".join(f"{_fmt_ms(grand_timings[c]):>16}" for c in CATEGORIES) + f" {_fmt_ms(grand_total):>12}"
    print(row)
    print(f"\nAverage per stock: {_fmt_ms(grand_total / len(targets))} detection time across {grand_candles / len(targets):.0f} candles/stock on average")
    print("graph_patterns is 0 for all stocks — not built yet (needs swing-high/swing-low detection first).")


if __name__ == "__main__":
    main()
