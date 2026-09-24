"""Does a 1%-of-capital daily loss circuit breaker help double_top? Real
engine (order_backtest.simulate), real Rs 200k capital, real itemized
costs, real liquidity cap, spread_fills on -- same setup as
verify_full_engine_backtest.py -- run with and without
EngineConfig.max_daily_loss_pct=0.01, at both the original 0.25%/0.50%
SL/TG and the wider 0.50%/0.50% combo found to work better earlier in this
investigation.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_daily_loss_halt.py
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

    halted_days = 0
    if max_daily_loss_pct is not None:
        threshold = -max_daily_loss_pct * STARTING_CAPITAL
        # crude but honest proxy: count days whose realized net_pnl (summed
        # from closed_trades that day) breached the threshold at some point --
        # summarize()'s own daily bucket already sums net pnl per day.
        for day_pnl in stats["daily"]["pnl"].values():
            if day_pnl <= threshold:
                halted_days += 1

    print(f"\n=== {label} ===")
    wr = f"{stats['win_ratio']*100:.1f}%" if stats["win_ratio"] is not None else "n/a"
    print(f"Trades: {stats['total_trades']}  Win: {stats['wins']}  Loss: {stats['losses']}  Win ratio: {wr}")
    print(f"Gross P&L: Rs {stats['total_gross_pnl']:,.2f}")
    print(f"Expenses:  Rs {stats['total_expenses']:,.2f}")
    print(f"Net P&L:   Rs {stats['total_net_pnl']:,.2f}")
    print(f"Final capital: Rs {final_capital:,.2f}  (return: {(final_capital/STARTING_CAPITAL - 1)*100:.1f}%)")
    print(f"Days traded: {stats['days_with_trades']}  winning: {stats['winning_days']}  losing: {stats['losing_days']}")
    if max_daily_loss_pct is not None:
        print(f"Days that hit/breached the -{max_daily_loss_pct*100:.0f}% daily loss threshold: {halted_days}")


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    for sl_pct, tg_pct, tag in [(0.0025, 0.005, "SL 0.25%/TG 0.50%"), (0.005, 0.005, "SL 0.50%/TG 0.50%")]:
        run(session_factory, start, end, f"{tag} -- NO daily loss halt", sl_pct, tg_pct, None)
        run(session_factory, start, end, f"{tag} -- WITH 1% daily loss halt", sl_pct, tg_pct, 0.01)


if __name__ == "__main__":
    main()
