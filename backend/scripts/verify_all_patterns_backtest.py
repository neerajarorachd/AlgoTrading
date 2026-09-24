"""Same real-engine backtest as verify_full_engine_backtest.py (spread_fills,
liquidity_safety_divisor, itemized real costs, Rs 200k starting capital), but
run separately for EVERY directional pattern instead of just double_top, so
patterns can be compared on equal footing -- same SL/target (0.25%/0.50%
fixed_pct, not each pattern's own neckline/ATR geometry), same capital, same
cost model, only the entry signal varies. HINDCOPPER only.

Each pattern gets its own independent Rs 200,000 (not compounded across
patterns -- these are 36 separate what-if backtests, not one blended
portfolio), matching "same money" for a fair per-pattern comparison.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_all_patterns_backtest.py
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
from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

SYMBOL = "HINDCOPPER"
EXCHANGE_SEGMENT = "NSE_EQ"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
STARTING_CAPITAL = 200_000.0

ALL_PATTERNS = sorted(BULLISH_PATTERNS | BEARISH_PATTERNS)


def run_one(session_factory, start, end, pattern: str) -> dict:
    config = EngineConfig(
        capital_per_trade=STARTING_CAPITAL,
        max_vol_per_call=100_000,
        max_orders_at_a_time=1,
        exit_at_loss_count=10_000,
        liquidity_safety_divisor=40,
        spread_fills=True, max_fill_candles=3, max_exit_candles=10,
        itemized_costs=True,
        compounding=False,
        pattern_filter=(pattern,),
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=0.0025, target_pct=0.005),
    )
    trades = simulate(session_factory, SYMBOL, EXCHANGE_SEGMENT, [TIMEFRAME], start, end, config)
    stats = summarize(trades)
    stats["pattern"] = pattern
    stats["direction"] = "bull" if pattern in BULLISH_PATTERNS else "bear"
    return stats


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    print(f"Testing {len(ALL_PATTERNS)} directional patterns on {SYMBOL}, "
          f"Rs {STARTING_CAPITAL:,.0f} each, SL 0.25% / Target 0.5%, real costs+liquidity, spread_fills on\n",
          flush=True)

    results = []
    for i, pattern in enumerate(ALL_PATTERNS, 1):
        started = datetime.now()
        stats = run_one(session_factory, start, end, pattern)
        elapsed = (datetime.now() - started).total_seconds()
        results.append(stats)
        wr = f"{stats['win_ratio']:.3f}" if stats["win_ratio"] is not None else "n/a"
        print(f"[{i}/{len(ALL_PATTERNS)}] {pattern:<28} ({stats['direction']})  "
              f"trades={stats['total_trades']:<5} win_ratio={wr:<6} "
              f"net_pnl=Rs {stats['total_net_pnl']:>12,.2f}  ({elapsed:.1f}s)", flush=True)

    print("\n\n=== FINAL SUMMARY (sorted by net P&L, best first) ===")
    header = f"{'Pattern':<28}{'Dir':<6}{'Trades':>8}{'WinRatio':>10}{'GrossPnL':>14}{'Expenses':>14}{'NetPnL':>14}{'Return%':>10}"
    print(header)
    print("-" * len(header))
    for stats in sorted(results, key=lambda s: s["total_net_pnl"], reverse=True):
        wr = f"{stats['win_ratio']:.3f}" if stats["win_ratio"] is not None else "n/a"
        ret_pct = stats["total_net_pnl"] / STARTING_CAPITAL * 100
        print(f"{stats['pattern']:<28}{stats['direction']:<6}{stats['total_trades']:>8}{wr:>10}"
              f"{stats['total_gross_pnl']:>14,.2f}{stats['total_expenses']:>14,.2f}"
              f"{stats['total_net_pnl']:>14,.2f}{ret_pct:>9.1f}%")

    profitable = [s for s in results if s["total_net_pnl"] > 0]
    print(f"\n{len(profitable)}/{len(results)} patterns were net profitable at these SL/target/cost settings.")


if __name__ == "__main__":
    main()
