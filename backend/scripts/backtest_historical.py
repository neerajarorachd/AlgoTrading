"""Runs ActivityEngine + PredictionTracker over persisted candles_historical
data for one symbol/timeframe/date-range — every tracked candle pattern,
crossover, structure shift, and graph formation treated as its own
standalone "strategy" (per prediction_tracker.py's own stance) — then
computes a position-sized, cost-adjusted P&L report with daily/monthly/
yearly/overall breakdowns and win ratio.

This is NOT the full Strategy (AND/OR condition tree) backtesting engine
described in strategy_system_architecture.md — that's still unbuilt. This
reuses exactly what already exists (ActivityEngine's detectors +
PredictionTracker's ATR/measured-move stop-target resolution) and adds
only the position-sizing/P&L-aggregation layer on top, which didn't exist
anywhere yet.

Run manually:

    .venv/Scripts/python.exe backend/scripts/backtest_historical.py \
        SYMBOL TIMEFRAME START_DATE END_DATE [CAPITAL_PER_TRADE] [--json OUT]

e.g.
    .venv/Scripts/python.exe backend/scripts/backtest_historical.py \
        RELIANCE 1day 2024-09-14 2026-09-14 2000000 --json out.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import ActivityEngine, seed_pattern_definitions
from brokers.models import Candle
from db.models import Base, PatternPrediction
from db.ops import LibCandlesHistorical, LibSymbols
from db.ops.LibStrategyElements import seed_strategy_elements
from db.session import build_engine, build_session_factory
from prediction_tracker import PredictionTracker

# Dhan's real round-trip intraday equity cost, researched earlier this
# project (~0.085% of price) — applied to entry notional as a single
# round-trip deduction. Net-of-cost, not raw win rate, is what actually
# matters (this session's own critical finding: a 33% breakeven win rate
# assumption looks very different once real costs are subtracted).
ROUND_TRIP_COST_RATE = 0.00085


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def replay(session_factory, symbol: str, exchange_segment: str, timeframe: str,
           start: datetime, end: datetime) -> None:
    """Feeds every persisted candle in [start, end] through a fresh
    ActivityEngine + PredictionTracker, in order — same mechanism
    replay_activity_engine.py already uses for candles_today, just reading
    from candles_historical (persistent, multi-day) instead."""
    with session_factory() as session:
        rows = LibCandlesHistorical.get_range(session, symbol, exchange_segment, timeframe, start, end)
    candles = [
        Candle(symbol=symbol, timeframe=timeframe, timestamp=r.ts,
               open=float(r.open_price), high=float(r.high_price),
               low=float(r.low_price), close=float(r.close_price), volume=r.volume)
        for r in rows
    ]
    print(f"Loaded {len(candles)} {timeframe} candles for {symbol} ({start.date()} -> {end.date()})")
    if not candles:
        return

    engine = ActivityEngine(session_factory)
    tracker = PredictionTracker(session_factory, engine)

    for candle in candles:
        tracker.check_pending(symbol, exchange_segment, candle)
        new_activities = engine.on_candle_closed(symbol, exchange_segment, candle)
        tracker.on_activities(symbol, exchange_segment, new_activities)

    flushed = engine.flush()
    inserted, updated = tracker.flush()
    print(f"Flushed {flushed} activities, {inserted} new / {updated} resolved predictions")


def _close_at_or_before(candles_by_ts: Dict[datetime, float], ts: datetime, ordered_ts: List[datetime]) -> Optional[float]:
    """Nearest known close at-or-before ts — used for a 'sideways' exit
    price, since neither target nor stop was actually hit; the real close
    when the outcome-scan window timed out is the only sensible fill."""
    if ts in candles_by_ts:
        return candles_by_ts[ts]
    # ordered_ts is sorted ascending; find the last one <= ts
    lo, hi = 0, len(ordered_ts)
    while lo < hi:
        mid = (lo + hi) // 2
        if ordered_ts[mid] <= ts:
            lo = mid + 1
        else:
            hi = mid
    return candles_by_ts[ordered_ts[lo - 1]] if lo > 0 else None


def compute_trades(session_factory, symbol: str, exchange_segment: str, timeframe: str,
                    start: datetime, end: datetime, capital_per_trade: float) -> List[dict]:
    with session_factory() as session:
        instrument_id = LibSymbols.get_instrument_id(session, symbol, exchange_segment)
        if instrument_id is None:
            return []
        predictions = (
            session.query(PatternPrediction)
            .filter(
                PatternPrediction.instrument_id == instrument_id,
                PatternPrediction.timeframe == timeframe,
                PatternPrediction.detected_ts >= start,
                PatternPrediction.detected_ts <= end,
                PatternPrediction.outcome.isnot(None),
            )
            .order_by(PatternPrediction.detected_ts)
            .all()
        )
        candle_rows = LibCandlesHistorical.get_range(session, symbol, exchange_segment, timeframe, start, end)

    candles_by_ts = {r.ts.replace(tzinfo=timezone.utc) if r.ts.tzinfo is None else r.ts: float(r.close_price) for r in candle_rows}
    ordered_ts = sorted(candles_by_ts.keys())

    trades = []
    for p in predictions:
        entry = float(p.entry_price)
        signed = 1 if p.direction == "bull" else -1
        outcome_ts = p.outcome_ts.replace(tzinfo=timezone.utc) if p.outcome_ts and p.outcome_ts.tzinfo is None else p.outcome_ts

        if p.outcome == "target_hit":
            exit_price = float(p.target)
        elif p.outcome == "stop_hit":
            exit_price = float(p.stop_loss)
        else:  # sideways — neither hit; exit at the actual close when the scan window timed out
            exit_price = _close_at_or_before(candles_by_ts, outcome_ts, ordered_ts) if outcome_ts else entry
            if exit_price is None:
                exit_price = entry

        quantity = int(capital_per_trade // entry) if entry > 0 else 0
        gross_pnl = quantity * signed * (exit_price - entry)
        cost = quantity * entry * ROUND_TRIP_COST_RATE
        net_pnl = gross_pnl - cost

        trades.append({
            "pattern": p.pattern, "direction": p.direction,
            "detected_ts": p.detected_ts.isoformat(), "outcome_ts": outcome_ts.isoformat() if outcome_ts else None,
            "entry_price": entry, "exit_price": exit_price, "outcome": p.outcome,
            "quantity": quantity, "gross_pnl": round(gross_pnl, 2), "net_pnl": round(net_pnl, 2),
        })
    return trades


def summarize(trades: List[dict]) -> dict:
    wins = [t for t in trades if t["outcome"] == "target_hit"]
    losses = [t for t in trades if t["outcome"] == "stop_hit"]
    sideways = [t for t in trades if t["outcome"] == "sideways"]
    decided = len(wins) + len(losses)
    win_ratio = (len(wins) / decided) if decided else None

    total_gross = sum(t["gross_pnl"] for t in trades)
    total_net = sum(t["net_pnl"] for t in trades)

    # daily P&L — bucketed by the day a trade actually closed (outcome_ts),
    # not when it opened, matching standard realized-P&L reporting
    daily: Dict[str, float] = defaultdict(float)
    for t in trades:
        if t["outcome_ts"]:
            day = t["outcome_ts"][:10]
            daily[day] += t["net_pnl"]
    winning_days = sum(1 for v in daily.values() if v > 0)
    losing_days = sum(1 for v in daily.values() if v < 0)
    flat_days = sum(1 for v in daily.values() if v == 0)

    monthly: Dict[str, float] = defaultdict(float)
    monthly_trades: Dict[str, int] = defaultdict(int)
    monthly_wins: Dict[str, int] = defaultdict(int)
    for t in trades:
        if t["outcome_ts"]:
            month = t["outcome_ts"][:7]
            monthly[month] += t["net_pnl"]
            monthly_trades[month] += 1
            if t["outcome"] == "target_hit":
                monthly_wins[month] += 1

    yearly: Dict[str, float] = defaultdict(float)
    yearly_trades: Dict[str, int] = defaultdict(int)
    yearly_wins: Dict[str, int] = defaultdict(int)
    for t in trades:
        if t["outcome_ts"]:
            year = t["outcome_ts"][:4]
            yearly[year] += t["net_pnl"]
            yearly_trades[year] += 1
            if t["outcome"] == "target_hit":
                yearly_wins[year] += 1

    return {
        "total_trades": len(trades), "wins": len(wins), "losses": len(losses), "sideways": len(sideways),
        "win_ratio": win_ratio,
        "total_gross_pnl": round(total_gross, 2), "total_net_pnl": round(total_net, 2),
        "total_cost": round(total_gross - total_net, 2),
        "winning_days": winning_days, "losing_days": losing_days, "flat_days": flat_days,
        "days_with_trades": len(daily),
        "daily_pnl": dict(sorted(daily.items())),
        "monthly_pnl": {m: round(v, 2) for m, v in sorted(monthly.items())},
        "monthly_trades": dict(sorted(monthly_trades.items())),
        "monthly_win_ratio": {
            m: round(monthly_wins[m] / monthly_trades[m], 3) if monthly_trades[m] else None
            for m in sorted(monthly_trades)
        },
        "yearly_pnl": {y: round(v, 2) for y, v in sorted(yearly.items())},
        "yearly_trades": dict(sorted(yearly_trades.items())),
        "yearly_win_ratio": {
            y: round(yearly_wins[y] / yearly_trades[y], 3) if yearly_trades[y] else None
            for y in sorted(yearly_trades)
        },
    }


def main() -> None:
    args = sys.argv[1:]
    json_out = None
    if "--json" in args:
        idx = args.index("--json")
        json_out = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    symbol, timeframe, start_s, end_s = args[0], args[1], args[2], args[3]
    capital_per_trade = float(args[4]) if len(args) > 4 else 2_000_000.0
    start, end = _parse_date(start_s), _parse_date(end_s)

    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    Base.metadata.create_all(engine_db)
    seed_pattern_definitions(session_factory)

    from activity_engine import PATTERN_CATALOG
    seed_strategy_elements(session_factory, PATTERN_CATALOG)

    with session_factory() as session:
        row = LibSymbols.get_by_natural_key(session, symbol, "NSE", "EQUITY")
        if row is None:
            print(f"{symbol} is not a registered instrument.")
            return
        exchange_segment = row.exchange_segment

    replay(session_factory, symbol, exchange_segment, timeframe, start, end)
    trades = compute_trades(session_factory, symbol, exchange_segment, timeframe, start, end, capital_per_trade)
    summary = summarize(trades)

    print(f"\n=== {symbol} {timeframe} backtest, {start.date()} -> {end.date()}, "
          f"capital/trade={capital_per_trade:,.0f} ===")
    print(f"Total trades: {summary['total_trades']}  "
          f"(wins={summary['wins']} losses={summary['losses']} sideways={summary['sideways']})")
    win_pct = f"{summary['win_ratio']*100:.1f}%" if summary["win_ratio"] is not None else "n/a"
    print(f"Win ratio (excl. sideways): {win_pct}")
    print(f"Gross P&L: {summary['total_gross_pnl']:,.2f}   "
          f"Cost: {summary['total_cost']:,.2f}   Net P&L: {summary['total_net_pnl']:,.2f}")
    print(f"Days with trades: {summary['days_with_trades']}  "
          f"winning={summary['winning_days']} losing={summary['losing_days']} flat={summary['flat_days']}")

    print("\nYearly:")
    for year, pnl in summary["yearly_pnl"].items():
        wr = summary["yearly_win_ratio"][year]
        wr_s = f"{wr*100:.1f}%" if wr is not None else "n/a"
        print(f"  {year}: net={pnl:>12,.2f}  trades={summary['yearly_trades'][year]:>4}  win_ratio={wr_s}")

    print("\nMonthly:")
    for month, pnl in summary["monthly_pnl"].items():
        wr = summary["monthly_win_ratio"][month]
        wr_s = f"{wr*100:.1f}%" if wr is not None else "n/a"
        print(f"  {month}: net={pnl:>12,.2f}  trades={summary['monthly_trades'][month]:>4}  win_ratio={wr_s}")

    if json_out:
        with open(json_out, "w") as f:
            json.dump({"symbol": symbol, "timeframe": timeframe, "start": start_s, "end": end_s,
                       "capital_per_trade": capital_per_trade, "summary": summary, "trades": trades}, f, indent=2)
        print(f"\nWrote {json_out}")


if __name__ == "__main__":
    main()
