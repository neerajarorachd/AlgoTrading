"""Does 'start low, size up on a win streak' help double_top? Real engine,
real Rs 200k capital, real itemized costs, real liquidity cap, spread_fills
on, 1% daily loss halt included (the best combo found so far) -- with and
without EngineConfig.win_streak_multipliers, at both the original
0.25%/0.50% SL/TG and the wider 0.50%/0.50% combo.

order_volume_multipliers is pinned to (1.0,) throughout (disabling the
EXISTING day-order-sequence-based multiplier) so the win-streak effect can
be seen in isolation, not conflated with that separate mechanism.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_win_streak_sizing.py
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

# Start low (0.5x), double up to 1x after 1 win, 2x after 2, 3x after 3,
# capped at 4x for a streak of 4+ -- deliberately conservative escalation.
WIN_STREAK = (0.5, 1.0, 2.0, 3.0, 4.0)


def run(session_factory, start, end, label: str, sl_pct: float, tg_pct: float, win_streak) -> None:
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
        max_daily_loss_pct=0.01,
        order_volume_multipliers=(1.0,),
        win_streak_multipliers=win_streak,
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

    for sl_pct, tg_pct, tag in [(0.0025, 0.005, "SL 0.25%/TG 0.50%"), (0.005, 0.005, "SL 0.50%/TG 0.50%")]:
        run(session_factory, start, end, f"{tag} -- flat sizing (no win streak)", sl_pct, tg_pct, None)
        run(session_factory, start, end, f"{tag} -- win-streak sizing {WIN_STREAK}", sl_pct, tg_pct, WIN_STREAK)


if __name__ == "__main__":
    main()
