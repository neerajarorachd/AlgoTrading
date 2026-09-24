"""BacktestRunMaster / BacktestRun / BacktestRunParameter / BacktestRunResult
/ BacktestTrade / BacktestDayResult reads/writes — backtesting session/run
management, modeled on the Trading project's own BT* tables (reviewed
read-only via SSH, 2026-09-14, never copied — see db/models.py's own
docstrings on each class for the specific mapping and what changed).

Session-ownership convention, matching the rest of this package: entity-
level CRUD (run masters, runs, parameters, the 1:1 result row) takes an
already-open `session` — these are typically called from within a request
or a single execution thread that already holds one. The two potentially
large per-run collections (trades, day results) take a `session_factory`
and use the same optimistic-bulk-insert-with-per-row-fallback strategy as
LibCandles.persist_bulk, since a single run can produce hundreds of trades.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy.exc import IntegrityError

from db.models import (
    BacktestDayResult, BacktestRun, BacktestRunMaster, BacktestRunParameter,
    BacktestRunResult, BacktestTrade,
)
from db.ops import LibWatchlists
from db.session import session_scope

# --------------------------------------------------------------------- BacktestRunMaster


def create_run_master(session, fields: dict) -> int:
    master = BacktestRunMaster(**fields)
    session.add(master)
    session.flush()
    return master.id


def get_run_master(session, run_master_id: int) -> Optional[BacktestRunMaster]:
    return session.get(BacktestRunMaster, run_master_id)


def resolve_run_instrument_ids(session, run_master: BacktestRunMaster) -> List[int]:
    """Which instrument(s) a session's runs should be expanded across —
    the single instrument it was scoped to, or every active member of the
    watchlist it was scoped to ("one run per stock in watchlist", explicit
    instruction). Exactly one of instrument_id/watchlist_id is expected to
    be set (see BacktestRunMaster's own docstring)."""
    if run_master.instrument_id is not None:
        return [run_master.instrument_id]
    if run_master.watchlist_id is not None:
        members = LibWatchlists.get_members(session, run_master.watchlist_id)
        return [symbol_row.id for _, symbol_row in members]
    return []


def get_all_run_masters(session) -> List[BacktestRunMaster]:
    # id DESC as an explicit tiebreaker — two sessions created back-to-back
    # can land on the same created_at value at this column's timestamp
    # precision, and created_at alone then leaves their relative order
    # unspecified (id is monotonic autoincrement, so it never ties).
    return (
        session.query(BacktestRunMaster)
        .order_by(BacktestRunMaster.created_at.desc(), BacktestRunMaster.id.desc())
        .all()
    )


def update_run_master_fields(session, run_master_id: int, fields: dict) -> None:
    if fields:
        session.query(BacktestRunMaster).filter_by(id=run_master_id).update(fields)


# --------------------------------------------------------------------- BacktestRun


def create_run(session, fields: dict) -> int:
    run = BacktestRun(**fields)
    session.add(run)
    session.flush()
    return run.id


def get_run(session, run_id: int) -> Optional[BacktestRun]:
    return session.get(BacktestRun, run_id)


def get_runs_for_master(session, run_master_id: int) -> List[BacktestRun]:
    return (
        session.query(BacktestRun)
        .filter_by(run_master_id=run_master_id)
        .order_by(BacktestRun.id)
        .all()
    )


def update_run_fields(session, run_id: int, fields: dict) -> None:
    if fields:
        session.query(BacktestRun).filter_by(id=run_id).update(fields)


# --------------------------------------------------------------------- BacktestRunParameter


def save_run_parameters(session, run_id: int, parameters: Sequence[Tuple[int, float]]) -> None:
    """parameters: [(strategy_condition_id, value), ...] — the concrete
    value this run used for each of its strategy's ranged conditions."""
    for strategy_condition_id, value in parameters:
        session.add(BacktestRunParameter(
            run_id=run_id, strategy_condition_id=strategy_condition_id, value=value,
        ))


def get_run_parameters(session, run_id: int) -> List[BacktestRunParameter]:
    return session.query(BacktestRunParameter).filter_by(run_id=run_id).all()


# --------------------------------------------------------------------- BacktestRunResult


def upsert_run_result(session, run_id: int, fields: dict) -> None:
    existing = session.query(BacktestRunResult).filter_by(run_id=run_id).one_or_none()
    if existing is None:
        session.add(BacktestRunResult(run_id=run_id, **fields))
    else:
        for key, value in fields.items():
            setattr(existing, key, value)


def get_run_result(session, run_id: int) -> Optional[BacktestRunResult]:
    return session.query(BacktestRunResult).filter_by(run_id=run_id).one_or_none()


def get_run_results_for_master(session, run_master_id: int) -> Dict[int, BacktestRunResult]:
    """run_id -> its BacktestRunResult, for every run in a session — one
    query rather than N, for rendering a session's own comparison view."""
    run_ids = [r.id for r in get_runs_for_master(session, run_master_id)]
    if not run_ids:
        return {}
    rows = session.query(BacktestRunResult).filter(BacktestRunResult.run_id.in_(run_ids)).all()
    return {row.run_id: row for row in rows}


# --------------------------------------------------------------------- BacktestTrade


def persist_trades_bulk(session_factory, run_id: int, trades: List[dict]) -> None:
    """trades: dicts shaped like order_backtest.py's OrderBook.closed_trades
    entries (pattern/direction/entry_ts/entry_price/stop_loss/target/
    exit_ts/exit_price/exit_reason/quantity/gross_pnl/expenses/net_pnl),
    plus instrument_id/timeframe. Same optimistic-insert-then-per-row-
    fallback strategy as LibCandles.persist_bulk.

    BacktestTrade has no `symbol` column (this table is already scoped to
    one instrument, via instrument_id) — closed_trades entries gained that
    key 2026-09-17 for simulate_portfolio's own multi-symbol reporting, so
    it's stripped here rather than added as a redundant column just for
    this single-symbol call path."""
    if not trades:
        return
    trades = [{k: v for k, v in t.items() if k != "symbol"} for t in trades]
    try:
        with session_scope(session_factory) as session:
            session.add_all([BacktestTrade(run_id=run_id, **t) for t in trades])
            session.flush()
    except IntegrityError:
        for t in trades:
            with session_factory() as session:
                try:
                    session.add(BacktestTrade(run_id=run_id, **t))
                    session.commit()
                except IntegrityError:
                    session.rollback()


def get_trades_for_run(session, run_id: int) -> List[BacktestTrade]:
    return (
        session.query(BacktestTrade)
        .filter_by(run_id=run_id)
        .order_by(BacktestTrade.entry_ts)
        .all()
    )


def get_trade(session, trade_id: int) -> Optional[BacktestTrade]:
    return session.get(BacktestTrade, trade_id)


# --------------------------------------------------------------------- BacktestDayResult


def persist_day_results_bulk(session_factory, run_id: int, day_results: List[dict]) -> None:
    """day_results: dicts with date/trade_count/win_count/loss_count/net_pnl."""
    if not day_results:
        return
    try:
        with session_scope(session_factory) as session:
            session.add_all([BacktestDayResult(run_id=run_id, **d) for d in day_results])
            session.flush()
    except IntegrityError:
        for d in day_results:
            with session_factory() as session:
                try:
                    session.add(BacktestDayResult(run_id=run_id, **d))
                    session.commit()
                except IntegrityError:
                    session.rollback()


def get_day_results_for_run(session, run_id: int) -> List[BacktestDayResult]:
    return (
        session.query(BacktestDayResult)
        .filter_by(run_id=run_id)
        .order_by(BacktestDayResult.date)
        .all()
    )
