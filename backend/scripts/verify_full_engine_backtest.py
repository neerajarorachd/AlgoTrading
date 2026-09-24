"""The complete HINDCOPPER double_top test, redone through the real,
tested order_backtest.py engine instead of the earlier ad-hoc double_top_*
scripts — same question ("0.25/0.50 SL/TG, Rs 200k capital, real costs,
real liquidity, fixed vs compounding"), but now exercising spread_fills,
liquidity_safety_divisor, itemized_costs, and compounding together as one
coherent run instead of five separate scripts.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_full_engine_backtest.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.session import build_engine, build_session_factory
from order_backtest import EngineConfig, SLTargetConfig, simulate, summarize

SYMBOL = "HINDCOPPER"
EXCHANGE_SEGMENT = "NSE_EQ"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
STARTING_CAPITAL = 200_000.0


def run(session_factory, start, end, compounding: bool) -> None:
    config = EngineConfig(
        capital_per_trade=STARTING_CAPITAL,
        max_vol_per_call=100_000,           # only liquidity_safety_divisor should actually bind
        max_orders_at_a_time=1,             # sequential -- no overlapping orders
        exit_at_loss_count=10_000,          # effectively unlimited -- not the thing under test here
        liquidity_safety_divisor=40,        # real per-candle volume cap (same as tonight's earlier analysis)
        spread_fills=True, max_fill_candles=3, max_exit_candles=10,
        itemized_costs=True,                # real brokerage/STT/exchange/SEBI/stamp/GST
        compounding=compounding,
        pattern_filter=("double_top",),
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=0.0025, target_pct=0.005),  # the 0.25/0.50 winner
    )
    trades = simulate(session_factory, SYMBOL, EXCHANGE_SEGMENT, [TIMEFRAME], start, end, config)
    stats = summarize(trades)

    label = "COMPOUNDING" if compounding else "FIXED"
    print(f"\n=== {label} (starting capital Rs {STARTING_CAPITAL:,.0f}) ===")
    print(f"Trades: {stats['total_trades']}  Win: {stats['wins']}  Loss: {stats['losses']}  "
          f"Win ratio: {stats['win_ratio']:.3f}" if stats['win_ratio'] is not None else "No trades")
    print(f"Gross P&L: Rs {stats['total_gross_pnl']:,.2f}")
    print(f"Expenses:  Rs {stats['total_expenses']:,.2f} "
          f"({stats['total_expenses']/abs(stats['total_gross_pnl'])*100:.1f}% of gross)"
          if stats['total_gross_pnl'] else "")
    print(f"Net P&L:   Rs {stats['total_net_pnl']:,.2f}")
    print(f"Days: {stats['days_with_trades']} traded, {stats['winning_days']} winning, "
          f"{stats['losing_days']} losing, {stats['flat_days']} flat")
    if compounding:
        final_capital = STARTING_CAPITAL + stats["total_net_pnl"]
        print(f"Final capital: Rs {final_capital:,.2f}  "
              f"(return: {(final_capital/STARTING_CAPITAL - 1)*100:.1f}%)")


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    run(session_factory, start, end, compounding=False)
    run(session_factory, start, end, compounding=True)


if __name__ == "__main__":
    main()
