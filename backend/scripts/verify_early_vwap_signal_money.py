"""Real-money test of the early VWAP-symmetry double_top signal (see
verify_early_double_top_vwap_signal.py) -- enters directly on the EARLY
candidate's own real price, never waiting for detect_double_top's own
5-candle-lagged confirmation at all. Two configs, as requested: one
without exit_at_loss_count (the raw strategy on its own), one with
exit_at_loss_count=1 (yesterday's proven discipline lever) -- both keep
the other already-proven levers (0.40% widened stop, 1% GROSS daily loss
cap, real itemized costs, real liquidity cap, spread_fills, Rs 200k
compounding capital) constant, since those aren't what's being tested here.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_early_vwap_signal_money.py
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
from order_backtest import EngineConfig, OrderBook, SLTargetConfig, fixed_pct_levels, summarize

SYMBOL = "HINDCOPPER"
EXCHANGE_SEGMENT = "NSE_EQ"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
STARTING_CAPITAL = 500_000.0
MAX_WATCH_CANDLES = 120
SL_PCT, TG_PCT = 0.004, 0.008  # the already-proven widened stop/target


def load_candles(session_factory):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    with session_factory() as session:
        row = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one()
        exchange_segment = row.exchange_segment
        historical = LibCandlesHistorical.get_range(session, SYMBOL, exchange_segment, TIMEFRAME, start, end)
    candles = [
        Candle(symbol=SYMBOL, timeframe=TIMEFRAME, timestamp=(c.ts if c.ts.tzinfo else c.ts.replace(tzinfo=timezone.utc)),
               open=float(c.open_price), high=float(c.high_price), low=float(c.low_price),
               close=float(c.close_price), volume=c.volume)
        for c in historical
    ]
    return candles, exchange_segment


def find_early_candidates(session_factory, candles, exchange_segment) -> dict[int, float]:
    """Same replay as verify_early_double_top_vwap_signal.py's early-
    candidate detection -- returns {candle_index: real_entry_price} for
    every early candidate fired (confirmed, invalidated, or timeout all
    included -- a live system can't know in advance which it'll be)."""
    activity_engine = ActivityEngine(session_factory)
    swing_window = deque(maxlen=2 * _SWING_LOOKBACK + 1)
    points: list[SwingPoint] = []
    watching = None
    candidates: dict[int, float] = {}

    for i, candle in enumerate(candles):
        activities = activity_engine.on_candle_closed(SYMBOL, exchange_segment, candle)
        activity_names = {a["activity"] for a in activities}

        if watching is not None:
            a, b = watching["a"], watching["b"]
            age = i - watching["since"]
            if not watching["early_fired"]:
                near_first_top = abs(candle.high - a.price) / a.price <= _DOUBLE_SIMILARITY if a.price else False
                if near_first_top and "vwap_rejection_bear" in activity_names:
                    watching["early_fired"] = True
                    candidates[i] = float(candle.close)
            if age > MAX_WATCH_CANDLES:
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
                        watching = None
                    elif kind_name == "high" and detect_double_top([a, b, points[-1]]):
                        watching = None

                if watching is None and len(points) >= 2 and points[-2].kind == "high" and points[-1].kind == "low":
                    watching = {"a": points[-2], "b": points[-1], "since": i, "early_fired": False}

    return candidates


def run(candles, exchange_segment, candidates: dict[int, float], exit_at_loss_count: int) -> dict:
    config = EngineConfig(
        capital_per_trade=STARTING_CAPITAL,
        max_vol_per_call=100_000,
        max_orders_at_a_time=1,
        exit_at_loss_count=exit_at_loss_count,
        liquidity_safety_divisor=40,
        spread_fills=True, max_fill_candles=3, max_exit_candles=10,
        itemized_costs=True,
        compounding=True,
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=SL_PCT, target_pct=TG_PCT),
        max_daily_loss_pct=0.01,
    )
    book = OrderBook(config)
    for i, candle in enumerate(candles):
        ts = candle.timestamp
        book.on_new_candle_day(ts)
        book.resolve_against_candle(candle)
        book.continue_closing(candle)
        book.continue_entries(candle)
        book.maybe_squareoff(candle)

        if i in candidates and book.can_open(ts):
            entry = candidates[i]
            stop_loss, target = fixed_pct_levels(entry, "bear", SL_PCT, TG_PCT)
            book.try_open(SYMBOL, "double_top_early_vwap", "bear", entry, ts, stop_loss, target,
                          candle.timeframe, candle.volume)

    if candles:
        book.force_close_all(float(candles[-1].close), candles[-1].timestamp)

    stats = summarize(book.closed_trades)
    stats["final_capital"] = STARTING_CAPITAL + stats["total_net_pnl"]
    return stats


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    candles, exchange_segment = load_candles(session_factory)
    print(f"{SYMBOL}: {len(candles)} candles loaded\n", flush=True)

    candidates = find_early_candidates(session_factory, candles, exchange_segment)
    print(f"{len(candidates)} early candidates found\n", flush=True)

    for label, exit_at_loss_count in [("Set 1: strategy alone (no exit-at-loss halt)", 10_000),
                                       ("Set 2: WITH exit_at_loss_count=1", 1)]:
        stats = run(candles, exchange_segment, candidates, exit_at_loss_count)
        wr = f"{stats['win_ratio']*100:.1f}%" if stats["win_ratio"] is not None else "n/a"
        ret = (stats["final_capital"] / STARTING_CAPITAL - 1) * 100
        print(f"=== {label} ===")
        print(f"Trades: {stats['total_trades']}  Win: {stats['wins']}  Loss: {stats['losses']}  Win ratio: {wr}")
        print(f"Gross P&L: Rs {stats['total_gross_pnl']:,.2f}  Expenses: Rs {stats['total_expenses']:,.2f}")
        print(f"Net P&L: Rs {stats['total_net_pnl']:,.2f}  Final capital: Rs {stats['final_capital']:,.2f}  "
              f"Return: {ret:.1f}%")

        print("  Monthly: month  trades  win%   net_pnl   cumulative   running_capital")
        cumulative = 0.0
        for month in sorted(stats["monthly"]["pnl"]):
            pnl = stats["monthly"]["pnl"][month]
            trades = stats["monthly"]["trades"][month]
            wr_m = stats["monthly"]["win_ratio"][month] * 100
            cumulative += pnl
            running_capital = STARTING_CAPITAL + cumulative
            print(f"    {month}   {trades:>4}   {wr_m:5.1f}%   {pnl:>10,.2f}   {cumulative:>10,.2f}   {running_capital:>12,.2f}")
        print(flush=True)


if __name__ == "__main__":
    main()
