"""Real-money test: double_top_ed, restricted to signals detected during
the 11:00-12:00 IST hour only (the real win-rate breakdown showed 50.7% at
this hour vs the 41-45% overall) -- Rs 2 lac and Rs 5 lac capital, real
itemized costs, real liquidity cap, spread_fills, compounding.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_double_top_ed_11am_money.py
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
from order_backtest import EngineConfig, OrderBook, SLTargetConfig, fixed_pct_levels, summarize

SYMBOL = "HINDCOPPER"
EXCHANGE_SEGMENT = "NSE_EQ"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
IST_OFFSET = timedelta(hours=5, minutes=30)
SL_PCT, TG_PCT = 0.0025, 0.005


def load_candles(session_factory):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    with session_factory() as session:
        row = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one()
        exchange_segment = row.exchange_segment
        historical = LibCandlesHistorical.get_range(session, SYMBOL, exchange_segment, TIMEFRAME, start, end)
    candles = [
        Candle(symbol=SYMBOL, timeframe=TIMEFRAME,
               timestamp=(c.ts if c.ts.tzinfo else c.ts.replace(tzinfo=timezone.utc)),
               open=float(c.open_price), high=float(c.high_price), low=float(c.low_price),
               close=float(c.close_price), volume=c.volume)
        for c in historical
    ]
    return candles, exchange_segment


def find_11am_candidates(session_factory, candles, exchange_segment) -> dict[int, float]:
    activity_engine = ActivityEngine(session_factory)
    out = {}
    for i, candle in enumerate(candles):
        for a in activity_engine.on_candle_closed(SYMBOL, exchange_segment, candle):
            if a["activity"] != "double_top_ed":
                continue
            ist_hour = (candle.timestamp + IST_OFFSET).hour
            if ist_hour == 11:
                out[i] = float(candle.close)
    return out


def run(candles, exchange_segment, candidates: dict[int, float], starting_capital: float, exit_at_loss_count: int,
        margin_multiplier: float = 5.0, liquidity_safety_divisor: int = 40, max_fill_candles: int = 3) -> dict:
    config = EngineConfig(
        capital_per_trade=starting_capital,
        max_vol_per_call=100_000,
        max_orders_at_a_time=1,
        exit_at_loss_count=exit_at_loss_count,
        liquidity_safety_divisor=liquidity_safety_divisor,
        spread_fills=True, max_fill_candles=max_fill_candles, max_exit_candles=10,
        itemized_costs=True,
        compounding=True,
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=SL_PCT, target_pct=TG_PCT),
        max_daily_loss_pct=0.01,
        # blanket MIS-style intraday margin -- same slot for every order
        # (this strategy takes at most ~1 trade/day, so the default's
        # order-SEQUENCE-based 1x/1x/5x would almost never reach 5x at all)
        order_volume_multipliers=(margin_multiplier, margin_multiplier, margin_multiplier),
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
            book.try_open(SYMBOL, "double_top_ed", "bear", entry, ts, stop_loss, target,
                          candle.timeframe, candle.volume)
    if candles:
        book.force_close_all(float(candles[-1].close), candles[-1].timestamp)
    stats = summarize(book.closed_trades)
    stats["final_capital"] = starting_capital + stats["total_net_pnl"]
    return stats


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    candles, exchange_segment = load_candles(session_factory)
    print(f"{SYMBOL}: {len(candles)} candles loaded\n", flush=True)

    candidates = find_11am_candidates(session_factory, candles, exchange_segment)
    print(f"{len(candidates)} double_top_ed candidates at 11:00-12:00 IST\n", flush=True)

    # (liquidity_safety_divisor, max_fill_candles) -- realistic PER-CANDLE
    # cap kept intact, spread the SAME desired packet across more real
    # candles instead of assuming one candle absorbs it all (explicit
    # instruction, 2026-09-18: "you can not buy everything in 1 min
    # candle... it will span in 5-6 candles").
    combos = [(40, 3), (40, 6), (20, 6), (10, 6)]
    for capital in (200_000.0, 500_000.0):
        for liquidity_safety_divisor, max_fill_candles in combos:
            stats = run(candles, exchange_segment, candidates, capital, 10_000, 5.0,
                        liquidity_safety_divisor, max_fill_candles)
            wr = f"{stats['win_ratio']*100:.1f}%" if stats["win_ratio"] is not None else "n/a"
            ret = (stats["final_capital"] / capital - 1) * 100
            print(f"=== Rs {capital:,.0f} cash -- 5x margin -- liquidity divisor 1/{liquidity_safety_divisor} "
                  f"-- max_fill_candles={max_fill_candles} ===")
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
                running_capital = capital + cumulative
                print(f"    {month}   {trades:>4}   {wr_m:5.1f}%   {pnl:>10,.2f}   {cumulative:>10,.2f}   {running_capital:>12,.2f}")
            print(flush=True)


if __name__ == "__main__":
    main()
