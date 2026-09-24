from datetime import datetime, timedelta, timezone

from db.models import BacktestRun, BacktestRunMaster, BacktestRunResult, PatternOutcome, SubscribedSymbol
from db.ops import LibBacktestRuns
from occurrence_backtest import record_occurrence_backtest

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"


def _dt(d):
    return datetime(2026, 1, d, 9, 15, tzinfo=timezone.utc)


def _register(session_factory) -> int:
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="1333", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _outcome(instrument_id, ts, pct20):
    return PatternOutcome(
        instrument_id=instrument_id, timeframe="1min", pattern="hammer", activity_type="candle_pattern",
        direction="bull", detected_ts=ts, entry_price=100.0, pct_change_20=pct20, window_candles=30,
    )


def test_record_occurrence_backtest_persists_a_real_run(session_factory):
    """Closes a real gap found live 2026-09-15: the Occurrence Backtest
    page never created a BacktestRunMaster/BacktestRun at all, so nothing
    was ever recorded. This is the actual persistence path."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([_outcome(instrument_id, _dt(1 + i), 0.02 if i < 7 else -0.01) for i in range(10)])
        session.commit()

    result = record_occurrence_backtest(
        session_factory, instrument_id, "1min", "hammer",
        _dt(1) - timedelta(days=1), _dt(20),
    )
    assert result["summary"]["count"] == 10
    assert result["summary"]["checkpoints"][20]["matched"] == 7

    with session_factory() as session:
        master = session.get(BacktestRunMaster, result["run_master_id"])
        assert master.mode == "occurrence_count"
        assert master.source == "occurrence_backtest"  # default
        assert master.status == "completed"
        assert master.instrument_id == instrument_id

        run = session.get(BacktestRun, result["run_id"])
        assert run.pattern == "hammer"
        assert run.strategy_id is None
        assert run.status == "completed"

        run_result = session.query(BacktestRunResult).filter_by(run_id=run.id).one()
        assert run_result.total_trades == 10
        assert run_result.wins == 7
        assert run_result.losses == 3
        assert float(run_result.win_ratio) == 0.7
        assert float(run_result.total_net_pnl) == 0  # no P&L concept for occurrence counting


def test_record_occurrence_backtest_custom_source(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add(_outcome(instrument_id, _dt(1), 0.02))
        session.commit()

    result = record_occurrence_backtest(
        session_factory, instrument_id, "1min", "hammer",
        _dt(1) - timedelta(days=1), _dt(5), source="RS1",
    )
    with session_factory() as session:
        master = session.get(BacktestRunMaster, result["run_master_id"])
        assert master.source == "RS1"


def test_record_occurrence_backtest_chains_into_an_existing_session(session_factory):
    """One "Run" click covering several patterns should land under ONE
    session, not one each — same run_master_id convention as
    run_backtest.py's own."""
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([_outcome(instrument_id, _dt(1 + i), 0.02) for i in range(3)])
        session.commit()

    first = record_occurrence_backtest(
        session_factory, instrument_id, "1min", "hammer",
        _dt(1) - timedelta(days=1), _dt(5), session_name="multi-pattern run",
    )
    second = record_occurrence_backtest(
        session_factory, instrument_id, "1min", "bullish_engulfing",
        _dt(1) - timedelta(days=1), _dt(5), run_master_id=first["run_master_id"],
    )
    assert second["run_master_id"] == first["run_master_id"]
    assert second["run_id"] != first["run_id"]

    with session_factory() as session:
        master = session.get(BacktestRunMaster, first["run_master_id"])
        assert master.run_count == 2
        assert master.name == "multi-pattern run"
        runs = session.query(BacktestRun).filter_by(run_master_id=first["run_master_id"]).all()
    assert {r.pattern for r in runs} == {"hammer", "bullish_engulfing"}


def test_record_occurrence_backtest_no_data_yet(session_factory):
    """No PatternOutcome rows at all for this pattern — still records a
    real run (total_trades=0), doesn't error out."""
    instrument_id = _register(session_factory)
    result = record_occurrence_backtest(
        session_factory, instrument_id, "1min", "hammer",
        _dt(1) - timedelta(days=1), _dt(5),
    )
    assert result["summary"] is None
    with session_factory() as session:
        run_result = session.query(BacktestRunResult).filter_by(run_id=result["run_id"]).one()
        assert run_result.total_trades == 0
        assert run_result.wins == 0
