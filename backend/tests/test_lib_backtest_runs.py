from datetime import datetime, time, timezone

from db.models import (
    Strategy, StrategyCondition, StrategyConditionGroup, SubscribedSymbol,
)
from db.ops import LibBacktestRuns, LibWatchlists

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"


def _dt(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def _register_symbol(session_factory, symbol=SYMBOL) -> int:
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id=f"SEC-{symbol}", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _create_strategy(session_factory, **overrides) -> int:
    fields = dict(
        name="Test Strategy", strategy_type="entry", max_vol_per_call=5000,
        max_orders_at_a_time=1, exit_at_loss=True, exit_at_loss_count=1,
        trading_start_time=time(9, 15), new_order_end_time=time(14, 0), trading_end_time=time(14, 50),
    )
    fields.update(overrides)
    with session_factory() as session:
        strategy = Strategy(**fields)
        session.add(strategy)
        session.commit()
        return strategy.id


def _create_ranged_condition(session_factory, strategy_id) -> int:
    with session_factory() as session:
        group = StrategyConditionGroup(strategy_id=strategy_id, parent_group_id=None, operator="AND")
        session.add(group)
        session.flush()
        cond = StrategyCondition(
            group_id=group.id, element_code="rsi", operator=">",
            compare_type="static", static_value_min=40.0, static_value_max=60.0, static_value_step=5.0,
        )
        session.add(cond)
        session.commit()
        return cond.id


# --------------------------------------------------------------------- BacktestRunMaster

def test_create_and_get_run_master(session_factory):
    instrument_id = _register_symbol(session_factory)
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "name": "1min vs 3min", "instrument_id": instrument_id,
            "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        session.commit()

    with session_factory() as session:
        master = LibBacktestRuns.get_run_master(session, run_master_id)
    assert master.name == "1min vs 3min"
    assert master.status == "queued"
    assert master.instrument_id == instrument_id


def test_get_all_run_masters_orders_newest_first(session_factory):
    instrument_id = _register_symbol(session_factory)
    with session_factory() as session:
        first = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        session.commit()
    with session_factory() as session:
        second = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 4, 1), "date_to": _dt(2025, 4, 30),
        })
        session.commit()

    with session_factory() as session:
        masters = LibBacktestRuns.get_all_run_masters(session)
    assert [m.id for m in masters] == [second, first]


def test_update_run_master_fields(session_factory):
    instrument_id = _register_symbol(session_factory)
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        session.commit()

    with session_factory() as session:
        LibBacktestRuns.update_run_master_fields(session, run_master_id, {
            "status": "completed", "run_count": 2, "best_run_id": 7,
        })
        session.commit()

    with session_factory() as session:
        master = LibBacktestRuns.get_run_master(session, run_master_id)
    assert master.status == "completed"
    assert master.run_count == 2
    assert master.best_run_id == 7


# --------------------------------------------------------------------- resolve_run_instrument_ids

def test_resolve_run_instrument_ids_for_a_single_instrument_session(session_factory):
    instrument_id = _register_symbol(session_factory)
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        session.commit()

    with session_factory() as session:
        master = LibBacktestRuns.get_run_master(session, run_master_id)
        instrument_ids = LibBacktestRuns.resolve_run_instrument_ids(session, master)
    assert instrument_ids == [instrument_id]


def test_resolve_run_instrument_ids_expands_a_watchlist_session(session_factory):
    """"one run per stock in watchlist" — a session scoped to a watchlist
    resolves to every active member's instrument_id, not the watchlist
    itself."""
    reliance_id = _register_symbol(session_factory, "RELIANCE")
    tcs_id = _register_symbol(session_factory, "TCS")
    with session_factory() as session:
        watchlist_id = LibWatchlists.create(session, {"name": "WL"}, [reliance_id, tcs_id])
        session.commit()

    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "watchlist_id": watchlist_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        session.commit()

    with session_factory() as session:
        master = LibBacktestRuns.get_run_master(session, run_master_id)
        instrument_ids = LibBacktestRuns.resolve_run_instrument_ids(session, master)
    assert set(instrument_ids) == {reliance_id, tcs_id}


def test_a_session_can_expand_to_multiple_strategies_for_one_stock(session_factory):
    """1 stock, 3 strategies — each run shares the session's instrument_id
    but carries its own strategy_id."""
    instrument_id = _register_symbol(session_factory)
    strategy_a = _create_strategy(session_factory, name="Strategy A")
    strategy_b = _create_strategy(session_factory, name="Strategy B")
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id,
            "strategy_id": strategy_a, "timeframe": "3min",
        })
        LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id,
            "strategy_id": strategy_b, "timeframe": "3min",
        })
        session.commit()

    with session_factory() as session:
        runs = LibBacktestRuns.get_runs_for_master(session, run_master_id)
    assert {r.strategy_id for r in runs} == {strategy_a, strategy_b}
    assert all(r.instrument_id == instrument_id for r in runs)


def test_occurrence_count_mode_run_has_no_strategy(session_factory):
    """Backtesting type 1 — a run counting pattern occurrences has no
    strategy_id at all, unlike a strategy_trade-mode run."""
    instrument_id = _register_symbol(session_factory)
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "mode": "occurrence_count", "instrument_id": instrument_id,
            "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        run_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id, "timeframe": "3min",
        })
        session.commit()

    with session_factory() as session:
        run = LibBacktestRuns.get_run(session, run_id)
        master = LibBacktestRuns.get_run_master(session, run_master_id)
    assert run.strategy_id is None
    assert master.mode == "occurrence_count"


# --------------------------------------------------------------------- BacktestRun

def test_create_run_and_get_runs_for_master(session_factory):
    instrument_id = _register_symbol(session_factory)
    strategy_id = _create_strategy(session_factory)
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        session.commit()

    with session_factory() as session:
        run1_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id, "strategy_id": strategy_id, "timeframe": "1min",
        })
        run2_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id, "strategy_id": strategy_id, "timeframe": "3min",
        })
        session.commit()

    with session_factory() as session:
        runs = LibBacktestRuns.get_runs_for_master(session, run_master_id)
    assert [r.id for r in runs] == [run1_id, run2_id]
    assert {r.timeframe for r in runs} == {"1min", "3min"}


def test_update_run_fields(session_factory):
    instrument_id = _register_symbol(session_factory)
    strategy_id = _create_strategy(session_factory)
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        run_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id, "strategy_id": strategy_id, "timeframe": "1min",
        })
        session.commit()

    with session_factory() as session:
        LibBacktestRuns.update_run_fields(session, run_id, {"status": "failed", "error_message": "boom"})
        session.commit()

    with session_factory() as session:
        run = LibBacktestRuns.get_run(session, run_id)
    assert run.status == "failed"
    assert run.error_message == "boom"


# --------------------------------------------------------------------- BacktestRunParameter

def test_save_and_get_run_parameters(session_factory):
    instrument_id = _register_symbol(session_factory)
    strategy_id = _create_strategy(session_factory)
    condition_id = _create_ranged_condition(session_factory, strategy_id)
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        run_45_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id, "strategy_id": strategy_id, "timeframe": "3min",
        })
        run_50_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id, "strategy_id": strategy_id, "timeframe": "3min",
        })
        session.commit()

    with session_factory() as session:
        LibBacktestRuns.save_run_parameters(session, run_45_id, [(condition_id, 45.0)])
        LibBacktestRuns.save_run_parameters(session, run_50_id, [(condition_id, 50.0)])
        session.commit()

    with session_factory() as session:
        params_45 = LibBacktestRuns.get_run_parameters(session, run_45_id)
        params_50 = LibBacktestRuns.get_run_parameters(session, run_50_id)
    assert [p.value for p in params_45] == [45.0]
    assert [p.value for p in params_50] == [50.0]
    assert params_45[0].strategy_condition_id == condition_id


# --------------------------------------------------------------------- BacktestRunResult

def test_upsert_run_result_inserts_then_updates(session_factory):
    instrument_id = _register_symbol(session_factory)
    strategy_id = _create_strategy(session_factory)
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        run_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id, "strategy_id": strategy_id, "timeframe": "3min",
        })
        session.commit()

    with session_factory() as session:
        LibBacktestRuns.upsert_run_result(session, run_id, {
            "total_trades": 5, "wins": 2, "losses": 3, "win_ratio": 0.4,
            "total_gross_pnl": 1000.0, "total_expenses": 100.0, "total_net_pnl": 900.0,
        })
        session.commit()
    with session_factory() as session:
        result = LibBacktestRuns.get_run_result(session, run_id)
    assert result.total_trades == 5
    assert float(result.total_net_pnl) == 900.0

    with session_factory() as session:
        LibBacktestRuns.upsert_run_result(session, run_id, {"total_trades": 7, "total_net_pnl": 1200.0})
        session.commit()
    with session_factory() as session:
        result = LibBacktestRuns.get_run_result(session, run_id)
        count = session.query(type(result)).filter_by(run_id=run_id).count()
    assert result.total_trades == 7
    assert float(result.total_net_pnl) == 1200.0
    assert count == 1  # updated in place, not duplicated


def test_get_run_results_for_master_across_multiple_runs(session_factory):
    instrument_id = _register_symbol(session_factory)
    strategy_id = _create_strategy(session_factory)
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        run1_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id, "strategy_id": strategy_id, "timeframe": "1min",
        })
        run2_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id, "strategy_id": strategy_id, "timeframe": "3min",
        })
        session.commit()

    with session_factory() as session:
        LibBacktestRuns.upsert_run_result(session, run1_id, {"total_trades": 3, "total_net_pnl": 100.0})
        LibBacktestRuns.upsert_run_result(session, run2_id, {"total_trades": 5, "total_net_pnl": -50.0})
        session.commit()

    with session_factory() as session:
        results = LibBacktestRuns.get_run_results_for_master(session, run_master_id)
    assert set(results.keys()) == {run1_id, run2_id}
    assert float(results[run1_id].total_net_pnl) == 100.0
    assert float(results[run2_id].total_net_pnl) == -50.0


# --------------------------------------------------------------------- BacktestTrade

def test_persist_trades_bulk_and_get_trades_for_run(session_factory):
    instrument_id = _register_symbol(session_factory)
    strategy_id = _create_strategy(session_factory)
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        run_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id, "strategy_id": strategy_id, "timeframe": "3min",
        })
        session.commit()

    trades = [
        dict(
            instrument_id=instrument_id, timeframe="3min", pattern="hammer", direction="bull",
            entry_ts=_dt(2025, 3, 3, 9, 15), entry_price=100.0, stop_loss=99.0, target=102.0,
            exit_ts=_dt(2025, 3, 3, 9, 18), exit_price=102.0, exit_reason="target_hit",
            quantity=5000, gross_pnl=10000.0, expenses=425.0, net_pnl=9575.0,
        ),
        dict(
            instrument_id=instrument_id, timeframe="3min", pattern="shooting_star", direction="bear",
            entry_ts=_dt(2025, 3, 3, 10, 0), entry_price=101.0, stop_loss=102.0, target=99.0,
            exit_ts=_dt(2025, 3, 3, 10, 3), exit_price=102.0, exit_reason="stop_hit",
            quantity=5000, gross_pnl=-5000.0, expenses=429.25, net_pnl=-5429.25,
        ),
    ]
    LibBacktestRuns.persist_trades_bulk(session_factory, run_id, trades)

    with session_factory() as session:
        rows = LibBacktestRuns.get_trades_for_run(session, run_id)
    assert len(rows) == 2
    assert rows[0].entry_ts < rows[1].entry_ts
    assert rows[0].pattern == "hammer"
    assert float(rows[1].net_pnl) == -5429.25


def test_persist_trades_bulk_handles_empty_list(session_factory):
    LibBacktestRuns.persist_trades_bulk(session_factory, 999, [])  # must not raise


# --------------------------------------------------------------------- BacktestDayResult

def test_persist_day_results_bulk_and_get_day_results_for_run(session_factory):
    instrument_id = _register_symbol(session_factory)
    strategy_id = _create_strategy(session_factory)
    with session_factory() as session:
        run_master_id = LibBacktestRuns.create_run_master(session, {
            "instrument_id": instrument_id, "date_from": _dt(2025, 3, 1), "date_to": _dt(2025, 3, 31),
        })
        run_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id, "strategy_id": strategy_id, "timeframe": "3min",
        })
        session.commit()

    day_results = [
        dict(date=_dt(2025, 3, 3), trade_count=1, win_count=0, loss_count=1, net_pnl=-4844.66),
        dict(date=_dt(2025, 3, 5), trade_count=3, win_count=2, loss_count=1, net_pnl=9726.07),
    ]
    LibBacktestRuns.persist_day_results_bulk(session_factory, run_id, day_results)

    with session_factory() as session:
        rows = LibBacktestRuns.get_day_results_for_run(session, run_id)
    assert len(rows) == 2
    assert rows[0].date < rows[1].date
    assert float(rows[1].net_pnl) == 9726.07
