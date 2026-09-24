"""Executes order_backtest.py's simulate() against a Strategy/instrument/
timeframe/date-range that already exist as DB rows, and persists the full
result (trades, day rollups, aggregate) back into the BacktestRun* tables —
the execution-wiring step that was deliberately deferred when those tables
were first built (see [[backtest_run_db_structure_plan]]).

This REPLACES hand-editing EngineConfig(...) literals in a script for every
test iteration (explicit instruction, 2026-09-14: "dont change code every
time with different config values. create db record for run and make all
params database driven.") — every order-management/SL-target parameter now
lives on the Strategy row (see order_backtest.py's
engine_config_from_strategy), and a run is just "this strategy, on this
instrument/timeframe/date-range."

Run manually:

    .venv/Scripts/python.exe backend/scripts/run_backtest.py \\
        --strategy-id ID --symbol SYMBOL --timeframe TIMEFRAME \\
        --start YYYY-MM-DD --end YYYY-MM-DD [--session-name NAME]

Creates a new BacktestRunMaster (1 instrument, date_from/to = --start/--end)
and one BacktestRun under it, executes, and prints the resulting run_id/
run_master_id plus a summary — same numbers order_backtest.py's own main()
used to print, but now sourced from and written back to the DB.
"""
from __future__ import annotations

import argparse
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from activity_engine import seed_pattern_definitions
from db.models import Base
from db.ops import LibBacktestRuns, LibStrategies, LibSymbols
from db.session import build_engine, build_session_factory
from order_backtest import engine_config_from_strategy, simulate, summarize


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run_backtest(session_factory, strategy_id: int, symbol: str, timeframe: str,
                  start: datetime, end: datetime, session_name: str = None,
                  run_master_id: int = None, source: str = None) -> dict:
    """run_master_id: attach this run to an EXISTING BacktestRunMaster
    session (e.g. adding a "3min" run next to an already-run "1min" one
    for the same comparison) instead of creating a new one — explicit
    instruction: a session groups multiple runs answering one question
    several ways (see BacktestRunMaster's own docstring). When given, the
    existing master's own date range/scope is left untouched; only its
    run_count/best_run_id/ended_at/time_elapsed_seconds get refreshed
    once this run completes, by re-scanning EVERY run under it, not just
    this one.

    source: who/what requested this run ("RS1", "RS2", ...) — defaults to
    "Strategy-<name>" when not given (2026-09-15: "all backtests should be
    recorded. you can set the source of the backtest as RS1/Strategy-abc/
    RS2 etc."). Only set on a NEWLY created master — attaching a run to an
    existing run_master_id never changes that session's own source."""
    created_new_master = run_master_id is None
    with session_factory() as session:
        symbol_row = LibSymbols.get_by_natural_key(session, symbol, "NSE", "EQUITY")
        instrument_id = symbol_row.id
        exchange_segment = symbol_row.exchange_segment
        strategy = LibStrategies.get_by_id(session, strategy_id)
        if strategy is None:
            raise ValueError(f"No strategy with id={strategy_id}")
        if run_master_id is not None and LibBacktestRuns.get_run_master(session, run_master_id) is None:
            raise ValueError(f"No BacktestRunMaster with id={run_master_id}")
        config = engine_config_from_strategy(strategy)

        if created_new_master:
            run_master_id = LibBacktestRuns.create_run_master(session, {
                "name": session_name or f"{strategy.name} / {symbol} / {timeframe}",
                "mode": "strategy_trade", "source": source or f"Strategy-{strategy.name}",
                "instrument_id": instrument_id,
                "date_from": start, "date_to": end, "status": "running", "started_at": _utcnow(),
            })
        run_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id,
            "strategy_id": strategy_id, "timeframe": timeframe,
            "status": "running", "started_at": _utcnow(),
        })
        session.commit()

    run_started = _time.monotonic()
    try:
        trades = simulate(session_factory, symbol, exchange_segment, [timeframe], start, end, config)
        summary = summarize(trades)
        elapsed = _time.monotonic() - run_started

        trades_for_db = []
        for t in trades:
            row = dict(t)
            row["instrument_id"] = instrument_id
            row["entry_ts"] = datetime.fromisoformat(row["entry_ts"])
            row["exit_ts"] = datetime.fromisoformat(row["exit_ts"])
            trades_for_db.append(row)
        LibBacktestRuns.persist_trades_bulk(session_factory, run_id, trades_for_db)

        day_results = _day_results_from_trades(trades)
        LibBacktestRuns.persist_day_results_bulk(session_factory, run_id, day_results)

        with session_factory() as session:
            LibBacktestRuns.upsert_run_result(session, run_id, {
                "total_trades": summary["total_trades"], "wins": summary["wins"], "losses": summary["losses"],
                "win_ratio": summary["win_ratio"], "total_gross_pnl": summary["total_gross_pnl"],
                "total_expenses": summary["total_expenses"], "total_net_pnl": summary["total_net_pnl"],
                "pre_run_capital": config.capital_per_trade,
                "post_run_capital": config.capital_per_trade + summary["total_net_pnl"],
                "winning_days": summary["winning_days"], "losing_days": summary["losing_days"],
                "flat_days": summary["flat_days"], "days_with_trades": summary["days_with_trades"],
            })
            # This session has autoflush=False — without an explicit flush,
            # the upsert_run_result row just added above wouldn't be visible
            # yet to get_run_results_for_master's own fresh query just below
            # (found via a failing test, not by inspection).
            session.flush()
            LibBacktestRuns.update_run_fields(session, run_id, {
                "status": "completed", "ended_at": _utcnow(), "time_elapsed_seconds": round(elapsed, 2),
            })
            # Re-scan every run under this session, not just this one — a
            # session can already have other completed runs when
            # run_master_id was passed in (e.g. an earlier "1min" run
            # sitting next to this new "3min" one), so run_count and
            # best_run_id (highest total_net_pnl) both need to reflect the
            # WHOLE session's current state, not just this single run.
            sibling_runs = LibBacktestRuns.get_runs_for_master(session, run_master_id)
            sibling_results = LibBacktestRuns.get_run_results_for_master(session, run_master_id)
            best_run_id, best_net = None, None
            for sibling in sibling_runs:
                result_row = sibling_results.get(sibling.id)
                if result_row is None:
                    continue
                net = float(result_row.total_net_pnl)
                if best_net is None or net > best_net:
                    best_net, best_run_id = net, sibling.id
            LibBacktestRuns.update_run_master_fields(session, run_master_id, {
                "status": "completed", "ended_at": _utcnow(), "time_elapsed_seconds": round(elapsed, 2),
                "run_count": len(sibling_runs), "best_run_id": best_run_id,
            })
            session.commit()
    except Exception as exc:
        with session_factory() as session:
            LibBacktestRuns.update_run_fields(session, run_id, {"status": "failed", "error_message": str(exc)[:512]})
            LibBacktestRuns.update_run_master_fields(session, run_master_id, {"status": "failed"})
            session.commit()
        raise

    return {"run_master_id": run_master_id, "run_id": run_id, "summary": summary, "run_time_seconds": round(elapsed, 2)}


def _day_results_from_trades(trades: list) -> list:
    from collections import defaultdict
    by_day = defaultdict(lambda: {"trade_count": 0, "win_count": 0, "loss_count": 0, "net_pnl": 0.0})
    for t in trades:
        day = datetime.fromisoformat(t["exit_ts"]).date()
        bucket = by_day[day]
        bucket["trade_count"] += 1
        bucket["net_pnl"] += t["net_pnl"]
        if t["net_pnl"] > 0:
            bucket["win_count"] += 1
        else:
            bucket["loss_count"] += 1
    return [
        {"date": datetime(day.year, day.month, day.day, tzinfo=timezone.utc), **{k: v for k, v in b.items() if k != "net_pnl"},
         "net_pnl": round(b["net_pnl"], 2)}
        for day, b in sorted(by_day.items())
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy-id", type=int, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--session-name", default=None)
    parser.add_argument("--run-master-id", type=int, default=None,
                         help="Attach this run to an existing BacktestRunMaster session instead of creating a new one")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)

    engine_db = build_engine()
    session_factory = build_session_factory(engine_db)
    Base.metadata.create_all(engine_db)
    seed_pattern_definitions(session_factory)

    result = run_backtest(session_factory, args.strategy_id, args.symbol, args.timeframe, start, end,
                           args.session_name, args.run_master_id)

    summary = result["summary"]
    print(f"run_master_id={result['run_master_id']}  run_id={result['run_id']}  "
          f"run_time={result['run_time_seconds']}s")
    print(f"Total trades: {summary['total_trades']}  (wins={summary['wins']} losses={summary['losses']})")
    wr = f"{summary['win_ratio']*100:.1f}%" if summary["win_ratio"] is not None else "n/a"
    print(f"Win ratio: {wr}")
    print(f"Gross: {summary['total_gross_pnl']:,.2f}  Expenses: {summary['total_expenses']:,.2f}  "
          f"Net: {summary['total_net_pnl']:,.2f}")
    print(f"Days: {summary['days_with_trades']} (winning={summary['winning_days']} "
          f"losing={summary['losing_days']} flat={summary['flat_days']})")


if __name__ == "__main__":
    main()
