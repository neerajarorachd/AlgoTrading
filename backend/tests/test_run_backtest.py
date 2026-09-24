import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from brokers.models import Candle  # noqa: E402
from db.models import Strategy, SubscribedSymbol  # noqa: E402
from db.ops import LibBacktestRuns, LibCandlesHistorical  # noqa: E402
from run_backtest import run_backtest  # noqa: E402

SYMBOL = "RELIANCE"
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _ts(hour, minute, day=16):
    return datetime(2026, 6, day, hour, minute, tzinfo=timezone.utc) - _IST_OFFSET


def _zigzag_highs(checkpoints):
    highs = []
    for (i0, h0), (i1, h1) in zip(checkpoints, checkpoints[1:]):
        for j in range(i0, i1):
            highs.append(h0 + (h1 - h0) * (j - i0) / (i1 - i0))
    highs.append(checkpoints[-1][1])
    return highs


def _seed_double_top_candles(session_factory):
    """Same shape as test_order_backtest.py's own verified double_top
    fixture — two comparable swing highs with a deep valley between them,
    guaranteed to produce at least one real trade via simulate()."""
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id="1333", previous_close=100.0,
        ))
        session.commit()

    highs = _zigzag_highs([(0, 90.0), (10, 100.3), (20, 95.0), (30, 100.2), (40, 92.0)])
    candles = [
        (SYMBOL, "NSE_EQ", Candle(
            symbol=SYMBOL, timeframe="1min", timestamp=_ts(9, 15) + timedelta(minutes=i),
            open=h - 0.5, high=h, low=h - 1.0, close=h - 0.5, volume=100,
        ))
        for i, h in enumerate(highs)
    ]
    LibCandlesHistorical.persist_bulk(session_factory, candles)
    start = candles[0][2].timestamp
    end = candles[-1][2].timestamp + timedelta(days=1)
    return start, end


def _create_strategy(session_factory, **overrides) -> int:
    fields = dict(
        name="DB-driven test strategy", strategy_type="entry",
        sl_formula_type="fixed_percent", sl_fixed_value=0.01, target_formula_type="fixed_percent",
        target_fixed_value=0.01, capital_per_trade=1_000_000.0, max_vol_per_call=100_000,
        max_orders_at_a_time=100, exit_at_loss_count=100,
    )
    fields.update(overrides)
    with session_factory() as session:
        strategy = Strategy(**fields)
        session.add(strategy)
        session.commit()
        return strategy.id


def test_run_backtest_persists_trades_result_and_day_rollups(session_factory):
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(session_factory)

    result = run_backtest(session_factory, strategy_id, SYMBOL, "1min", start, end, session_name="test run")
    summary = result["summary"]
    assert summary["total_trades"] >= 1

    with session_factory() as session:
        run = LibBacktestRuns.get_run(session, result["run_id"])
        master = LibBacktestRuns.get_run_master(session, result["run_master_id"])
        trades = LibBacktestRuns.get_trades_for_run(session, result["run_id"])
        run_result = LibBacktestRuns.get_run_result(session, result["run_id"])
        day_results = LibBacktestRuns.get_day_results_for_run(session, result["run_id"])

    assert run.status == "completed"
    assert run.strategy_id == strategy_id
    assert master.status == "completed"
    assert master.run_count == 1
    assert master.best_run_id == result["run_id"]

    assert len(trades) == summary["total_trades"]
    assert all(t.margin_used is not None for t in trades)
    assert all(t.instrument_id == trades[0].instrument_id for t in trades)

    assert run_result.total_trades == summary["total_trades"]
    assert float(run_result.total_net_pnl) == summary["total_net_pnl"]
    assert float(run_result.pre_run_capital) == 1_000_000.0

    assert sum(d.trade_count for d in day_results) == summary["total_trades"]


def test_run_backtest_defaults_source_to_strategy_name(session_factory):
    """2026-09-15: "all backtests should be recorded. you can set the
    source of the backtest as RS1/Strategy-abc/RS2 etc." — no explicit
    source given -> defaults to "Strategy-<name>"."""
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(session_factory, name="My Cool Strategy")

    result = run_backtest(session_factory, strategy_id, SYMBOL, "1min", start, end)
    with session_factory() as session:
        master = LibBacktestRuns.get_run_master(session, result["run_master_id"])
    assert master.source == "Strategy-My Cool Strategy"


def test_run_backtest_accepts_an_explicit_source(session_factory):
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(session_factory)

    result = run_backtest(session_factory, strategy_id, SYMBOL, "1min", start, end, source="RS1")
    with session_factory() as session:
        master = LibBacktestRuns.get_run_master(session, result["run_master_id"])
    assert master.source == "RS1"


def test_run_backtest_uses_strategy_columns_not_hardcoded_defaults(session_factory):
    """The whole point of the DB-driven runner: change the Strategy row,
    not the script, and the run picks up the new sizing/order-management
    parameters — here, a tiny max_vol_per_call that should visibly cap
    every trade's quantity well below what the default EngineConfig would
    produce."""
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(session_factory, max_vol_per_call=7, first_order_quantity=None)

    result = run_backtest(session_factory, strategy_id, SYMBOL, "1min", start, end)
    with session_factory() as session:
        trades = LibBacktestRuns.get_trades_for_run(session, result["run_id"])
    assert trades, "expected at least one trade"
    assert all(t.quantity <= 7 for t in trades)


def test_run_backtest_first_order_quantity_override_from_db(session_factory):
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(session_factory, first_order_quantity=1)

    result = run_backtest(session_factory, strategy_id, SYMBOL, "1min", start, end)
    with session_factory() as session:
        trades = LibBacktestRuns.get_trades_for_run(session, result["run_id"])
    assert trades[0].quantity == 1


def test_run_backtest_can_attach_to_an_existing_run_master(session_factory):
    """Two runs (e.g. 1min vs 3min) sharing one session — the second call
    passes run_master_id instead of creating a new one, and the master's
    own run_count/best_run_id end up reflecting BOTH runs, not just the
    latest."""
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(session_factory)

    first = run_backtest(session_factory, strategy_id, SYMBOL, "1min", start, end, session_name="comparison")
    second = run_backtest(session_factory, strategy_id, SYMBOL, "1min", start, end,
                           run_master_id=first["run_master_id"])

    assert second["run_master_id"] == first["run_master_id"]
    assert second["run_id"] != first["run_id"]

    with session_factory() as session:
        master = LibBacktestRuns.get_run_master(session, first["run_master_id"])
        runs = LibBacktestRuns.get_runs_for_master(session, first["run_master_id"])
    assert master.run_count == 2
    assert {r.id for r in runs} == {first["run_id"], second["run_id"]}
    assert master.best_run_id in (first["run_id"], second["run_id"])


def test_run_backtest_unknown_run_master_id_raises_before_creating_any_run_row(session_factory):
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(session_factory)

    with pytest.raises(ValueError):
        run_backtest(session_factory, strategy_id, SYMBOL, "1min", start, end, run_master_id=999999)

    with session_factory() as session:
        runs = LibBacktestRuns.get_runs_for_master(session, 999999)
    assert runs == []


def test_run_backtest_missing_strategy_raises_before_creating_any_run_row(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id="1333", previous_close=100.0,
        ))
        session.commit()

    with pytest.raises(ValueError):
        run_backtest(session_factory, 999999, SYMBOL, "1min", _ts(9, 15), _ts(9, 15) + timedelta(days=1))

    with session_factory() as session:
        masters = LibBacktestRuns.get_all_run_masters(session)
    assert masters == []
