"""SL 0.4%/TG 0.8% and SL 0.4%/TG 1.2%, with the CORRECTED gross-daily-loss
circuit breaker (day's loss and profit tracked separately -- halts on
cumulative LOSS reaching 1% of capital, not net P&L, per explicit
instruction 2026-09-17). Real engine, real Rs 200k capital, real itemized
costs, real liquidity cap, spread_fills on -- same setup as every other
full-engine test in this investigation. Each combo run with and without
the halt for context.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_wider_combos_gross_halt.py
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


def run(session_factory, start, end, label: str, sl_pct: float, tg_pct: float, max_daily_loss_pct) -> None:
    config = EngineConfig(
        capital_per_trade=STARTING_CAPITAL,
        max_vol_per_call=100_000,
        max_orders_at_a_time=1,
        exit_at_loss_count=10_000,
        liquidity_safety_divisor=40,
        spread_fills=True, max_fill_candles=3, max_exit_candles=10,
        itemized_costs=True,
        compounding=True,
        pattern_filter=("double_top",),
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=sl_pct, target_pct=tg_pct),
        max_daily_loss_pct=max_daily_loss_pct,
    )
    trades = simulate(session_factory, SYMBOL, EXCHANGE_SEGMENT, [TIMEFRAME], start, end, config)
    stats = summarize(trades)
    final_capital = STARTING_CAPITAL + stats["total_net_pnl"]

    print(f"\n=== {label} ===")
    wr = f"{stats['win_ratio']*100:.1f}%" if stats["win_ratio"] is not None else "n/a"
    print(f"Trades: {stats['total_trades']}  Win: {stats['wins']}  Loss: {stats['losses']}  Win ratio: {wr}")
    print(f"Gross P&L: Rs {stats['total_gross_pnl']:,.2f}")
    print(f"Expenses:  Rs {stats['total_expenses']:,.2f}")
    print(f"Net P&L:   Rs {stats['total_net_pnl']:,.2f}")
    print(f"Final capital: Rs {final_capital:,.2f}  (return: {(final_capital/STARTING_CAPITAL - 1)*100:.1f}%)")
    print(f"Days traded: {stats['days_with_trades']}  winning: {stats['winning_days']}  losing: {stats['losing_days']}")


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    for sl_pct, tg_pct, tag in [(0.004, 0.008, "SL 0.40%/TG 0.80%"), (0.004, 0.012, "SL 0.40%/TG 1.20%")]:
        run(session_factory, start, end, f"{tag} -- NO daily loss cap", sl_pct, tg_pct, None)
        run(session_factory, start, end, f"{tag} -- WITH 1% GROSS daily loss cap", sl_pct, tg_pct, 0.01)


if __name__ == "__main__":
    main()
