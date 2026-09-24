"""Apply the full winning setup found for double_top -- wider stop (0.40%
SL), 1% GROSS daily loss cap, exit at the first loss of the day -- to the
other 5 patterns that showed an inflated 70%+ checkpoint sign-agreement
rate (all swing/structure-based, same stale-timestamp mechanism already
diagnosed for double_top): triple_top, double_bottom, triple_bottom,
bearish_structure_shift, bullish_structure_shift. double_top included
too, as the known baseline for comparison.

Real engine, real Rs 200k capital, real itemized costs, real liquidity
cap, spread_fills on -- identical setup to every other full-engine test in
this investigation, just swapping pattern_filter.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_70pct_patterns_full.py
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

PATTERNS = [
    "double_top", "triple_top", "double_bottom", "triple_bottom",
    "bearish_structure_shift", "bullish_structure_shift",
]
COMBOS = [(0.004, 0.008, "0.40%/0.80%"), (0.004, 0.012, "0.40%/1.20%")]


def run(session_factory, start, end, pattern: str, sl_pct: float, tg_pct: float) -> dict:
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
    )
    trades = simulate(session_factory, SYMBOL, EXCHANGE_SEGMENT, [TIMEFRAME], start, end, config)
    stats = summarize(trades)
    stats["final_capital"] = STARTING_CAPITAL + stats["total_net_pnl"]
    return stats


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    print(f"Setup: SL widened, 1% GROSS daily loss cap, exit at FIRST loss, "
          f"Rs {STARTING_CAPITAL:,.0f} capital, real costs+liquidity, spread_fills on\n", flush=True)

    results = []
    for pattern in PATTERNS:
        for sl_pct, tg_pct, tag in COMBOS:
            stats = run(session_factory, start, end, pattern, sl_pct, tg_pct)
            wr = f"{stats['win_ratio']*100:.1f}%" if stats["win_ratio"] is not None else "n/a"
            print(f"{pattern:<28}{tag:<14}trades={stats['total_trades']:<5}win={wr:<7}"
                  f"net=Rs {stats['total_net_pnl']:>11,.0f}  final=Rs {stats['final_capital']:>11,.0f}  "
                  f"return={(stats['final_capital']/STARTING_CAPITAL-1)*100:>6.1f}%", flush=True)
            results.append((pattern, tag, stats))

    print("\n\n=== FINAL SUMMARY (sorted by final capital, best first) ===")
    header = f"{'Pattern':<28}{'Combo':<14}{'Trades':>8}{'WinRatio':>10}{'NetPnL':>14}{'FinalCapital':>15}{'Return':>9}"
    print(header)
    print("-" * len(header))
    for pattern, tag, stats in sorted(results, key=lambda r: r[2]["final_capital"], reverse=True):
        wr = f"{stats['win_ratio']*100:.1f}%" if stats["win_ratio"] is not None else "n/a"
        ret = (stats["final_capital"] / STARTING_CAPITAL - 1) * 100
        print(f"{pattern:<28}{tag:<14}{stats['total_trades']:>8}{wr:>10}"
              f"{stats['total_net_pnl']:>14,.0f}{stats['final_capital']:>15,.0f}{ret:>8.1f}%")

    profitable = [r for r in results if r[2]["total_net_pnl"] > 0]
    print(f"\n{len(profitable)}/{len(results)} (pattern, combo) runs were net profitable.")


if __name__ == "__main__":
    main()
