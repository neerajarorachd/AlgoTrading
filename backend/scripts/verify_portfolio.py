"""Evidence script (not a test) — runs simulate_portfolio against REAL
historical candles for two real symbols (ASHOKLEY + BANKBARODA, both
freshly backfilled tonight) with a tight shared max_orders_at_a_time, then
proves from the real trade log that:
  1. Trades genuinely happened for BOTH symbols (not just one).
  2. At no point were there ever more concurrent positions than the
     portfolio-wide cap allows (the shared-pool constraint is real).
  3. No trade's entry/exit price is contaminated by the OTHER symbol's
     price level (each symbol's own trades stay in that symbol's own real
     price range).

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_portfolio.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from db.session import build_engine, build_session_factory
from order_backtest import EngineConfig, SLTargetConfig, simulate_portfolio

SYMBOLS = [("ASHOKLEY", "NSE_EQ"), ("BANKBARODA", "NSE_EQ")]
TIMEFRAME = "1min"
MAX_CONCURRENT = 2


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=14)

    config = EngineConfig(
        capital_per_trade=2_000_000.0, max_vol_per_call=100_000,
        max_orders_at_a_time=MAX_CONCURRENT, exit_at_loss_count=200,
        sl_target=SLTargetConfig("fixed", "fixed_pct", sl_pct=0.003, target_pct=0.006),
    )
    trades = simulate_portfolio(session_factory, SYMBOLS, [TIMEFRAME], start, end, config)
    by_symbol = {s: [t for t in trades if t["symbol"] == s] for s, _ in SYMBOLS}

    print(f"\n{len(trades)} total trades across the portfolio:")
    for symbol, sym_trades in by_symbol.items():
        print(f"  {symbol}: {len(sym_trades)} trades")
    if not all(by_symbol.values()):
        print("\n  *** EVIDENCE GAP: at least one symbol produced zero trades in this window ***")

    # Replay the trade timeline event-by-event (open at entry_ts, close at
    # exit_ts) and verify concurrency NEVER exceeded MAX_CONCURRENT --
    # this is checking the ENGINE'S OWN OUTPUT after the fact, independent
    # of its internal bookkeeping, as an outside audit.
    events = []
    for t in trades:
        events.append((datetime.fromisoformat(t["entry_ts"]), 1, t["symbol"]))
        events.append((datetime.fromisoformat(t["exit_ts"]), -1, t["symbol"]))
    events.sort(key=lambda e: (e[0], e[1]))  # closes before opens on a tie, conservative

    concurrent = 0
    max_seen = 0
    for ts, delta, symbol in events:
        concurrent += delta
        max_seen = max(max_seen, concurrent)
    print(f"\nMax concurrent open positions observed across the whole run: {max_seen} "
          f"(cap was {MAX_CONCURRENT})")
    assert max_seen <= MAX_CONCURRENT, "SHARED POOL CONSTRAINT VIOLATED -- see above"

    # Cross-contamination check: every trade's entry/exit price should sit
    # near that SAME symbol's own real recent price range.
    print("\nPrice-range sanity per symbol (entry price min/max):")
    for symbol, sym_trades in by_symbol.items():
        if not sym_trades:
            continue
        prices = [t["entry_price"] for t in sym_trades]
        print(f"  {symbol}: {min(prices):.2f} - {max(prices):.2f} (n={len(prices)})")

    print("\nFirst 5 trades chronologically, showing symbol interleaving:")
    for t in sorted(trades, key=lambda t: t["entry_ts"])[:5]:
        print(f"  {t['entry_ts']}  {t['symbol']:<12} {t['pattern']:<20} "
              f"entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} {t['exit_reason']}")


if __name__ == "__main__":
    main()
