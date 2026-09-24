"""End-to-end check: create a real Strategy row carrying the winning
double_top configuration found in this investigation (widened stop, 1%
GROSS daily loss cap, exit at first loss, real costs/liquidity/spread_fills),
build its EngineConfig via engine_config_from_strategy, and confirm the
real backtest result matches what the hand-built EngineConfig produced
earlier (671 trades, 35.2% win, -21.6% return) -- proving the new Strategy
columns actually drive a real run, not just passing unit tests against a
fake object.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_strategy_driven_config.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.models import Base
from db.ops import LibStrategies
from db.session import build_engine, build_session_factory
from order_backtest import engine_config_from_strategy, simulate, summarize

SYMBOL = "HINDCOPPER"
EXCHANGE_SEGMENT = "NSE_EQ"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
STARTING_CAPITAL = 200_000.0

STRATEGY_NAME = "verify_strategy_driven_config -- double_top winning combo"


def main() -> None:
    engine_db = build_engine()
    Base.metadata.create_all(engine_db)
    session_factory = build_session_factory(engine_db)

    with session_factory() as session:
        existing = LibStrategies.get_by_name(session, STRATEGY_NAME)
        if existing is not None:
            LibStrategies.delete(session, existing.id)
            session.commit()

        strategy_id = LibStrategies.create(session, {
            "name": STRATEGY_NAME, "strategy_type": "pattern",
            "direction": "bear", "sl_formula_type": "fixed_percent",
            "sl_fixed_value": 0.004, "target_formula_type": "fixed_percent", "target_fixed_value": 0.008,
            "capital_per_trade": STARTING_CAPITAL, "max_vol_per_call": 100_000,
            "max_orders_at_a_time": 1, "exit_at_loss_count": 1,
            "pattern_filter": "double_top",
            "max_daily_loss_pct": 0.01,
            "spread_fills": True, "max_fill_candles": 3, "max_exit_candles": 10,
            "liquidity_safety_divisor": 40, "itemized_costs": True, "compounding": True,
        }, tree=None)
        session.commit()
        strategy = LibStrategies.get_by_id(session, strategy_id)
        config = engine_config_from_strategy(strategy)

    print("EngineConfig built from the Strategy row:")
    print(f"  sl_target={config.sl_target}")
    print(f"  max_daily_loss_pct={config.max_daily_loss_pct}  exit_at_loss_count={config.exit_at_loss_count}")
    print(f"  spread_fills={config.spread_fills}  liquidity_safety_divisor={config.liquidity_safety_divisor}")
    print(f"  itemized_costs={config.itemized_costs}  compounding={config.compounding}")
    print(f"  pattern_filter={config.pattern_filter}\n")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    trades = simulate(session_factory, SYMBOL, EXCHANGE_SEGMENT, [TIMEFRAME], start, end, config)
    stats = summarize(trades)
    final_capital = STARTING_CAPITAL + stats["total_net_pnl"]
    wr = f"{stats['win_ratio']*100:.1f}%" if stats["win_ratio"] is not None else "n/a"
    print(f"\nResult: trades={stats['total_trades']}  win={wr}  net=Rs {stats['total_net_pnl']:,.0f}  "
          f"final=Rs {final_capital:,.0f}  return={(final_capital/STARTING_CAPITAL-1)*100:.1f}%")
    print("(expected to match the earlier hand-built-EngineConfig result: "
          "671 trades, 35.2% win, -21.6% return)")


if __name__ == "__main__":
    main()
