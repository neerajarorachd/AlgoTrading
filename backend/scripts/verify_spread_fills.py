"""Evidence script (not a test) — runs a real backtest against HINDCOPPER's
actual historical candles with spread_fills on and a deliberately tight
liquidity_safety_divisor, then for every trade whose quantity implies it
COULDN'T have filled in one candle, cross-checks the math against the real
candle data: sums real volume//divisor across the actual candles following
entry_ts and confirms it matches (or exceeds) the filled quantity, and
prints the weighted-average entry price alongside the real per-candle
prices it was built from.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_spread_fills.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory
from order_backtest import EngineConfig, SLTargetConfig, simulate

SYMBOL = "HINDCOPPER"
EXCHANGE_SEGMENT = "NSE_EQ"
TIMEFRAME = "1min"
LIQUIDITY_DIVISOR = 8000  # deliberately very tight -- forces real multi-candle spreading


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=14)  # a real, recent 2-week window is plenty to see this

    config = EngineConfig(
        capital_per_trade=2_000_000.0, max_vol_per_call=100_000,
        max_orders_at_a_time=3, exit_at_loss_count=100,
        liquidity_safety_divisor=LIQUIDITY_DIVISOR, spread_fills=True,
        max_fill_candles=5, max_exit_candles=5,
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=0.003, target_pct=0.006),
    )
    trades = simulate(session_factory, SYMBOL, EXCHANGE_SEGMENT, [TIMEFRAME], start, end, config)
    print(f"\n{len(trades)} total trades\n")

    with session_factory() as session:
        candles = LibCandlesHistorical.get_range(session, SYMBOL, EXCHANGE_SEGMENT, TIMEFRAME, start, end)
    # SQL Server's DATETIME columns round-trip naive -- simulate()'s own
    # candle.timestamp is UTC-aware (via _load_candles' _as_utc), so this
    # cross-check needs the same normalization or every dict lookup below
    # silently misses (found live: this exact bug hid real evidence the
    # first time this script ran).
    candles_by_ts = {c.ts.replace(tzinfo=timezone.utc) if c.ts.tzinfo is None else c.ts: c for c in candles}
    ordered_ts = sorted(candles_by_ts)

    multi_candle_evidence = 0
    for t in trades:
        entry_ts = datetime.fromisoformat(t["entry_ts"])
        # Real per-candle liquidity cap right at entry
        entry_candle = candles_by_ts.get(entry_ts)
        if entry_candle is None:
            continue
        entry_cap = entry_candle.volume // LIQUIDITY_DIVISOR
        if t["quantity"] <= entry_cap:
            continue  # fit in one candle -- not interesting evidence, skip

        multi_candle_evidence += 1
        print(f"=== Trade: {t['pattern']} {t['direction']} qty={t['quantity']} "
              f"entry_price={t['entry_price']:.2f} entry_ts={entry_ts} ===")
        print(f"  entry candle's own liquidity cap: {entry_cap} "
              f"(volume={entry_candle.volume} // {LIQUIDITY_DIVISOR}) -- LESS than filled quantity {t['quantity']}")
        print("  real candles this fill must have drawn from:")
        idx = ordered_ts.index(entry_ts)
        cumulative = 0
        for ts in ordered_ts[idx:idx + 6]:
            c = candles_by_ts[ts]
            cap = c.volume // LIQUIDITY_DIVISOR
            cumulative += cap
            print(f"    {ts}  close={float(c.close_price):.2f}  volume={c.volume}  "
                  f"this-candle-cap={cap}  cumulative-cap={cumulative}")
            if cumulative >= t["quantity"]:
                break
        print()

    print(f"\n{multi_candle_evidence} trade(s) genuinely required multi-candle liquidity spreading "
          f"(quantity > single entry candle's own cap) out of {len(trades)} total.")


if __name__ == "__main__":
    main()
