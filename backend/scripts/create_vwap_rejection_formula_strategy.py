"""Creates the real Strategy the user specified, 2026-09-18, using the new
formula-based condition tree:

    high[-1] < VWAP and high[-2] < VWAP and low[-1] < VWAP and low[-2] < VWAP and
    (close[-1] < open[-1] and
     (close[-2] < open[-2] or close[-3] < open[-3])))

Then evaluates it for real against HINDCOPPER 1min data (build_indicator_
dataframe + evaluate_tree) as evidence it actually fires correctly, not
just that it persists/round-trips.

Run manually:
    .venv/Scripts/python.exe backend/scripts/create_vwap_rejection_formula_strategy.py
"""
from __future__ import annotations

import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from condition_evaluator import IST_OFFSET, build_indicator_dataframe, evaluate_tree
from db.models import Base
from db.ops import LibStrategies
from db.session import build_engine, build_session_factory

SYMBOL = "HINDCOPPER"
EXCHANGE_SEGMENT = "NSE_EQ"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
STRATEGY_NAME = "VWAP rejection + 2-of-3 bearish candles"


def _cond(left_formula, operator=None, right_formula=None):
    return {"left_formula": left_formula, "operator": operator, "right_formula": right_formula}


TREE = {
    "operator": "AND",
    "conditions": [
        _cond("high[-1]", "<", "vwap"),
        _cond("high[-2]", "<", "vwap"),
        _cond("low[-1]", "<", "vwap"),
        _cond("low[-2]", "<", "vwap"),
    ],
    "groups": [
        {
            "operator": "AND",
            "conditions": [_cond("close[-1]", "<", "open[-1]")],
            "groups": [
                {
                    "operator": "OR",
                    "conditions": [
                        _cond("close[-2]", "<", "open[-2]"),
                        _cond("close[-3]", "<", "open[-3]"),
                    ],
                    "groups": [],
                }
            ],
        }
    ],
}


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
            "name": STRATEGY_NAME, "strategy_type": "formula",
            "description": "high/low of last 2 candles below VWAP, plus last candle bearish "
                            "and at least 1 of the 2 before it also bearish",
            "direction": "bear",
            # explicit instruction, 2026-09-18: "strategy time starts at
            # 10:15" -- the SAME field order_backtest.py's
            # engine_config_from_strategy already reads as
            # EngineConfig.new_order_start_time, no new column needed.
            "trading_start_time": time(10, 15),
        }, tree=TREE)
        session.commit()
        print(f"Created strategy id={strategy_id}, trading_start_time=10:15\n")

    # --- real evaluation against real data
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)
    with session_factory() as session:
        df = build_indicator_dataframe(session, SYMBOL, EXCHANGE_SEGMENT, TIMEFRAME, start, end)
    print(f"{SYMBOL}: {len(df)} candles loaded into the indicator dataframe\n")

    fired = evaluate_tree(TREE, df).fillna(False)
    n_fired_raw = int(fired.sum())
    print(f"Strategy condition tree fires on {n_fired_raw} / {len(df)} candles ({n_fired_raw/len(df)*100:.2f}%) "
          f"before the trading_start_time gate\n")

    # trading_start_time=10:15 gate applied separately (the same
    # order-management field order_backtest.py's own can_open() already
    # checks) -- the condition tree itself doesn't know about it.
    ist_time = (df["ts"] + IST_OFFSET).dt.time
    after_start = ist_time >= time(10, 15)
    fired_gated = fired & after_start
    n_fired = int(fired_gated.sum())
    print(f"After the 10:15 IST start-time gate: {n_fired} / {len(df)} candles "
          f"({n_fired/len(df)*100:.2f}%)\n")

    print("First 5 gated firing candles (real evidence, not just a persistence check):")
    fired_rows = df[fired_gated].head(5)
    for _, row in fired_rows.iterrows():
        vwap_str = f"{row['vwap']:.2f}" if row["vwap"] == row["vwap"] else "NaN"
        print(f"  {row['ts']}  O={row['open']:.2f} H={row['high']:.2f} L={row['low']:.2f} C={row['close']:.2f}  "
              f"vwap={vwap_str}")

    # day-boundary sanity: every firing candle's own 3-candle lookback
    # must stay within its own trading day
    print("\nDay-boundary check on the first 5 gated fires (offsets must never cross a session):")
    for idx in df[fired_gated].index[:5]:
        today = df.loc[idx, "_trading_date"]
        lookback_dates = {df.loc[idx - k, "_trading_date"] for k in range(1, 4) if idx - k >= 0}
        print(f"  {df.loc[idx, 'ts']}  own day={today}  lookback candles' days={lookback_dates}  "
              f"all same day: {lookback_dates == {today}}")


if __name__ == "__main__":
    main()
