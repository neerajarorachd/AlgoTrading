"""Layer exit_on_macd_reversal=True (exit a position once MACD flips
against its direction, before its fixed stop/target fires -- smoother than
reacting to a single spike wick, per explicit instruction 2026-09-17) on
top of the best setup found so far (widened stop, 1% GROSS daily loss cap,
exit at first loss), for double_top plus the 2 patterns that showed a real
(if thin) edge: triple_top and bearish_structure_shift. Real engine, real
Rs 200k capital, real itemized costs, real liquidity cap, spread_fills on.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_macd_reversal_exit.py
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

PATTERNS_AND_COMBOS = [
    ("double_top", 0.004, 0.008), ("double_top", 0.004, 0.012),
    ("triple_top", 0.004, 0.012),
    ("bearish_structure_shift", 0.004, 0.012),
]


def run(session_factory, start, end, pattern: str, sl_pct: float, tg_pct: float, macd_exit: bool) -> dict:
    config = EngineConfig(
        capital_per_trade=STARTING_CAPITAL,
        max_vol_per_call=100_000,
        max_orders_at_a_time=1,
        exit_at_loss_count=1,
        liquidity_safety_divisor=40,
        spread_fills=True, max_fill_candles=3, max_exit_candles=10,
        itemized_costs=True,
        compounding=True,
        pattern_filter=(pattern,),
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=sl_pct, target_pct=tg_pct),
        max_daily_loss_pct=0.01,
        exit_on_macd_reversal=macd_exit,
    )
    trades = simulate(session_factory, SYMBOL, EXCHANGE_SEGMENT, [TIMEFRAME], start, end, config)
    stats = summarize(trades)
    stats["final_capital"] = STARTING_CAPITAL + stats["total_net_pnl"]
    reversal_exits = sum(1 for t in trades if t["exit_reason"] == "indicator_reversal")
    stats["reversal_exits"] = reversal_exits
    return stats


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    for pattern, sl_pct, tg_pct in PATTERNS_AND_COMBOS:
        tag = f"{pattern} {sl_pct*100:.2f}%/{tg_pct*100:.2f}%"
        for macd_exit in (False, True):
            stats = run(session_factory, start, end, pattern, sl_pct, tg_pct, macd_exit)
            label = "WITH macd-reversal exit" if macd_exit else "without (baseline)"
            wr = f"{stats['win_ratio']*100:.1f}%" if stats["win_ratio"] is not None else "n/a"
            ret = (stats["final_capital"] / STARTING_CAPITAL - 1) * 100
            extra = f"  reversal_exits={stats['reversal_exits']}" if macd_exit else ""
            print(f"{tag:<38}{label:<26}trades={stats['total_trades']:<5}win={wr:<7}"
                  f"net=Rs {stats['total_net_pnl']:>10,.0f}  final=Rs {stats['final_capital']:>11,.0f}  "
                  f"return={ret:>6.1f}%{extra}", flush=True)
        print()


if __name__ == "__main__":
    main()
