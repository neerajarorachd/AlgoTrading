"""Pure win-ratio test -- NO capital, NO position sizing, NO liquidity cap,
NO costs, NO order-management (max_orders_at_a_time, sequential admission).
For every directional pattern on HINDCOPPER, across several SL/target
combinations: does the target hit before the stop, walking forward candle-
by-candle from the REAL entry price? This answers "does the pattern call
the next move correctly enough to clear this SL/TG" in isolation, before
any money-management question.

Correctness, reused/verified against the real, unit-tested engine rather
than reimplemented from scratch:
  - entry price = the CURRENT candle's own close (order_backtest.py's fixed
    convention -- NOT activity["close_price"], which for structure/graph-
    formation patterns is the confirming SWING candle's OHLC, up to
    _SWING_LOOKBACK=5 candles stale; see order_backtest.py's own comment at
    its book.try_open() call site). This was the actual bug in the old
    double_top_sltg_backtest.py that inflated its win rate.
  - stop/target levels: order_backtest.fixed_pct_levels(...) itself, imported
    directly rather than re-derived, so this script can't silently drift
    from the engine's own formula.
  - same-candle hit-both-ambiguity: resolved stop-first (conservative),
    matching both the old script's convention AND order_backtest.py's own
    resolve_against_candle (see its docstring).
  - target/stop hit test: RANGE CONTAINMENT (low <= level <= high), matching
    order_backtest.py's resolve_against_candle -- NOT the old script's
    directional-only check (c.high >= stop), which can claim a fill even
    when a candle gapped clean over the level.

Detections (pattern occurrences + their real entry price) are gathered in
ONE single pass over the candles -- they don't depend on SL/TG at all, so
every combination below reuses the same pass instead of re-replaying the
activity engine per combo per pattern.

Run manually:
    .venv/Scripts/python.exe backend/scripts/verify_all_patterns_winrate.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine
from brokers.models import Candle
from db.models import SubscribedSymbol
from db.ops import LibCandlesHistorical
from db.session import build_engine, build_session_factory
from order_backtest import fixed_pct_levels
from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

SYMBOL = "HINDCOPPER"
TIMEFRAME = "1min"
LOOKBACK_DAYS = 730
MAX_CANDLES = 120  # same generous ~2h cap as the old script

# (sl_pct, target_pct) as fractions -- same combo set double_top_sltg_backtest.py
# used (in percent-points there), plus a couple of wider/tighter bookends.
SL_TG_COMBOS = [
    (0.0015, 0.003),   # 0.15% / 0.30% -- tight, 2:1
    (0.002, 0.005),    # 0.20% / 0.50% -- 2.5:1
    (0.0025, 0.005),   # 0.25% / 0.50% -- 2:1 (the one already tested)
    (0.0025, 0.0055),  # 0.25% / 0.55% -- 2.2:1
    (0.0025, 0.006),   # 0.25% / 0.60% -- 2.4:1
    (0.003, 0.006),    # 0.30% / 0.60% -- 2:1, wider
    (0.005, 0.01),     # 0.50% / 1.00% -- wide, 2:1
]


def walk_forward(candles: list[Candle], entry_index: int, entry_price: float, direction: str,
                  sl_pct: float, target_pct: float) -> dict:
    stop_price, target_price = fixed_pct_levels(entry_price, direction, sl_pct, target_pct)
    entry_day = candles[entry_index].timestamp.date()
    last_seen = candles[entry_index]

    for j in range(entry_index + 1, min(entry_index + 1 + MAX_CANDLES, len(candles))):
        c = candles[j]
        if c.timestamp.date() != entry_day:
            break
        last_seen = c
        low, high = c.low, c.high
        hit_target = low <= target_price <= high
        hit_stop = low <= stop_price <= high
        if not (hit_target or hit_stop):
            continue
        if hit_stop:  # same-candle ambiguity -- conservative, matches order_backtest.py
            return {"result": "loss"}
        return {"result": "win"}
    return {"result": "timeout"}


def gather_detections(session_factory) -> tuple[list[Candle], dict[str, list[dict]]]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    with session_factory() as session:
        symbol_row = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one()
        exchange_segment = symbol_row.exchange_segment
        historical = LibCandlesHistorical.get_range(session, SYMBOL, exchange_segment, TIMEFRAME, start, end)

    candles = [
        Candle(symbol=SYMBOL, timeframe=TIMEFRAME, timestamp=r.ts, open=float(r.open_price),
               high=float(r.high_price), low=float(r.low_price), close=float(r.close_price), volume=r.volume)
        for r in historical
    ]
    print(f"{SYMBOL}: {len(candles)} historical {TIMEFRAME} candles loaded\n", flush=True)

    activity_engine = ActivityEngine(session_factory)
    detections: dict[str, list[dict]] = {}

    for i, candle in enumerate(candles):
        new_activities = activity_engine.on_candle_closed(SYMBOL, exchange_segment, candle)
        for activity in new_activities:
            pattern = activity["activity"]
            if pattern in BULLISH_PATTERNS:
                direction = "bull"
            elif pattern in BEARISH_PATTERNS:
                direction = "bear"
            else:
                continue
            # Real, current-candle entry price -- NOT activity["close_price"]
            # (see module docstring: that field is stale for structure/graph
            # formations). `candle` here is always the actual candle just
            # processed, correct for every pattern type.
            detections.setdefault(pattern, []).append({
                "entry_index": i, "entry_price": float(candle.close), "direction": direction,
            })
        if (i + 1) % 40_000 == 0:
            print(f"  ...{i + 1}/{len(candles)} candles scanned, "
                  f"{sum(len(v) for v in detections.values())} detections so far", flush=True)

    total_detections = sum(len(v) for v in detections.values())
    print(f"\n{total_detections} total directional-pattern detections across {len(detections)} patterns\n", flush=True)
    return candles, detections


def main() -> None:
    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)

    candles, detections = gather_detections(session_factory)

    all_results = {}  # (sl_pct, target_pct) -> {pattern: result dict}
    for sl_pct, target_pct in SL_TG_COMBOS:
        rr = target_pct / sl_pct
        print(f"\n\n===== SL {sl_pct*100:.2f}% / Target {target_pct*100:.2f}% (R:R {rr:.2f}:1, "
              f"breakeven win rate {100/(1+rr):.1f}%) =====")
        print(f"{'Pattern':<28}{'Dir':<6}{'N':>7}{'Win':>7}{'Loss':>7}{'Timeout':>9}{'WinRatio':>10}{'Edge':>9}")
        print("-" * 83)

        results_by_pattern = {}
        for pattern, dets in detections.items():
            sims = [walk_forward(candles, d["entry_index"], d["entry_price"], d["direction"], sl_pct, target_pct)
                    for d in dets]
            wins = sum(1 for s in sims if s["result"] == "win")
            losses = sum(1 for s in sims if s["result"] == "loss")
            timeouts = sum(1 for s in sims if s["result"] == "timeout")
            decided = wins + losses
            win_ratio = wins / decided if decided else None
            # "Edge" = decided-only expectancy in %, ignoring timeouts and
            # all costs -- win_ratio*target_pct - (1-win_ratio)*sl_pct.
            edge = (win_ratio * target_pct - (1 - win_ratio) * sl_pct) * 100 if win_ratio is not None else None
            results_by_pattern[pattern] = {
                "direction": dets[0]["direction"], "n": len(dets),
                "wins": wins, "losses": losses, "timeouts": timeouts,
                "win_ratio": win_ratio, "edge": edge,
            }

        for pattern, r in sorted(results_by_pattern.items(),
                                  key=lambda kv: (kv[1]["edge"] if kv[1]["edge"] is not None else -999),
                                  reverse=True):
            wr = f"{r['win_ratio']*100:.1f}%" if r["win_ratio"] is not None else "n/a"
            edge = f"{r['edge']:+.3f}%" if r["edge"] is not None else "n/a"
            print(f"{pattern:<28}{r['direction']:<6}{r['n']:>7}{r['wins']:>7}{r['losses']:>7}"
                  f"{r['timeouts']:>9}{wr:>10}{edge:>9}")

        all_results[(sl_pct, target_pct)] = results_by_pattern

    # --- Cross-combo summary: best (pattern, combo) pairs by gross edge.
    print("\n\n===== TOP 15 (pattern, SL/TG) COMBINATIONS BY GROSS EDGE (no costs) =====")
    flat = []
    for (sl_pct, target_pct), results_by_pattern in all_results.items():
        for pattern, r in results_by_pattern.items():
            if r["edge"] is not None:
                flat.append((r["edge"], pattern, r["direction"], sl_pct, target_pct, r["n"], r["win_ratio"]))
    flat.sort(reverse=True)
    print(f"{'Pattern':<28}{'Dir':<6}{'SL%':>6}{'TG%':>6}{'N':>7}{'WinRatio':>10}{'Edge':>9}")
    print("-" * 72)
    for edge, pattern, direction, sl_pct, target_pct, n, win_ratio in flat[:15]:
        print(f"{pattern:<28}{direction:<6}{sl_pct*100:>5.2f}%{target_pct*100:>5.2f}%{n:>7}"
              f"{win_ratio*100:>9.1f}%{edge:>+8.3f}%")

    # --- Formula sanity check: print 3 concrete example trades (using the
    # 0.25%/0.50% combo) so the entry price / stop / target / decision can
    # be eyeballed directly against the real candle data.
    print("\n=== Spot-check: 3 example trades at SL 0.25%/TG 0.50% (verify by eye) ===")
    sample_pattern = "double_top" if "double_top" in detections else next(iter(detections))
    for d in detections[sample_pattern][:3]:
        idx, entry_price, direction = d["entry_index"], d["entry_price"], d["direction"]
        stop_price, target_price = fixed_pct_levels(entry_price, direction, 0.0025, 0.005)
        sim = walk_forward(candles, idx, entry_price, direction, 0.0025, 0.005)
        print(f"\npattern={sample_pattern} direction={direction} entry_ts={candles[idx].timestamp} "
              f"entry_price={entry_price:.2f} (== candle[{idx}].close, real detection candle)")
        print(f"  stop={stop_price:.4f}  target={target_price:.4f}  -> result={sim['result']}")
        for j in range(idx + 1, min(idx + 6, len(candles))):
            c = candles[j]
            print(f"    {c.timestamp}  O={c.open:.2f} H={c.high:.2f} L={c.low:.2f} C={c.close:.2f}  "
                  f"target_in_range={c.low <= target_price <= c.high}  stop_in_range={c.low <= stop_price <= c.high}")


if __name__ == "__main__":
    main()
