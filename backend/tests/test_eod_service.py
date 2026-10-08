from datetime import datetime, timezone

import eod_service
from db.models import Strategy, SubscribedSymbol
from eod_service import run_eod_cycle, strategies_for_today


def _add_strategy(session, id, name, strategy_type="entry", active=True):
    session.add(Strategy(id=id, name=name, strategy_type=strategy_type, active=active))


def _add_symbol(session_factory, symbol="RELIANCE", security_id="2885"):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY",
            exchange_segment="NSE_EQ", security_id=security_id, previous_close=100.0,
        ))
        session.commit()


def test_strategies_for_today_picks_only_todays_weekday_slot(session_factory):
    monday = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)  # weekday() == 0
    with session_factory() as session:
        _add_strategy(session, id=7, name="runs on monday")    # 7 % 7 == 0
        _add_strategy(session, id=8, name="runs on tuesday")   # 8 % 7 == 1
        _add_strategy(session, id=14, name="also monday")      # 14 % 7 == 0
        session.commit()

        result = strategies_for_today(session, today=monday)

    names = {s.name for s in result}
    assert names == {"runs on monday", "also monday"}


def test_strategies_for_today_excludes_recommendation_parent_rows(session_factory):
    monday = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
    with session_factory() as session:
        _add_strategy(session, id=7, name="real strategy", strategy_type="entry")
        _add_strategy(session, id=21, name="RS1 (parent)", strategy_type="recommendation_parent")  # 21 % 7 == 0, same slot
        session.commit()

        result = strategies_for_today(session, today=monday)

    names = {s.name for s in result}
    assert names == {"real strategy"}


def test_strategies_for_today_excludes_inactive_rows(session_factory):
    monday = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
    with session_factory() as session:
        _add_strategy(session, id=7, name="inactive", active=False)
        session.commit()

        result = strategies_for_today(session, today=monday)

    assert result == []


def test_run_eod_cycle_backfills_every_active_symbol(session_factory, monkeypatch):
    _add_symbol(session_factory, "RELIANCE", "2885")
    _add_symbol(session_factory, "TCS", "11536")
    backfill_calls = []
    monkeypatch.setattr(
        eod_service, "backfill_missing_candles",
        lambda symbol, exchange_segment, security_id, rest_broker, session_factory, aggregator:
            backfill_calls.append(symbol),
    )
    monkeypatch.setattr(eod_service, "run_backtest", lambda *a, **k: {"run_master_id": 1, "run_id": 1, "summary": {}, "run_time_seconds": 0})

    result = run_eod_cycle(session_factory, rest_broker=object(), aggregator=object())

    assert sorted(backfill_calls) == ["RELIANCE", "TCS"]
    assert result["symbols_backfilled"] == 2
    assert result["backfill_errors"] == []


def test_run_eod_cycle_one_bad_symbol_backfill_does_not_sink_the_rest(session_factory, monkeypatch):
    _add_symbol(session_factory, "BAD", "1")
    _add_symbol(session_factory, "GOOD", "2")

    def fake_backfill(symbol, exchange_segment, security_id, rest_broker, session_factory, aggregator):
        if symbol == "BAD":
            raise RuntimeError("simulated backfill failure")

    monkeypatch.setattr(eod_service, "backfill_missing_candles", fake_backfill)
    monkeypatch.setattr(eod_service, "run_backtest", lambda *a, **k: {"run_master_id": 1, "run_id": 1, "summary": {}, "run_time_seconds": 0})

    result = run_eod_cycle(session_factory, rest_broker=object(), aggregator=object())  # must not raise

    assert result["backfill_errors"] == ["BAD"]
    assert result["symbols_backfilled"] == 2


def test_run_eod_cycle_runs_a_backtest_per_strategy_symbol_timeframe(session_factory, monkeypatch):
    monday = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
    with session_factory() as session:
        _add_strategy(session, id=7, name="monday strategy")  # 7 % 7 == 0 == Monday
        session.commit()
    _add_symbol(session_factory, "RELIANCE", "2885")

    monkeypatch.setattr(eod_service, "backfill_missing_candles", lambda *a, **k: None)
    backtest_calls = []

    def fake_run_backtest(session_factory, strategy_id, symbol, timeframe, start, end, session_name=None, run_master_id=None, source=None):
        backtest_calls.append((strategy_id, symbol, timeframe))
        return {"run_master_id": 99, "run_id": len(backtest_calls), "summary": {}, "run_time_seconds": 0}

    monkeypatch.setattr(eod_service, "run_backtest", fake_run_backtest)

    result = run_eod_cycle(session_factory, rest_broker=object(), aggregator=object(), now=monday)

    assert sorted(backtest_calls) == [(7, "RELIANCE", "1min"), (7, "RELIANCE", "3min"), (7, "RELIANCE", "5min")]
    assert result["strategies_run_today"] == ["monday strategy"]
    assert result["backtests_completed"] == 3
    assert result["backtest_errors"] == []


def test_run_eod_cycle_one_bad_backtest_does_not_sink_the_rest(session_factory, monkeypatch):
    monday = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
    with session_factory() as session:
        _add_strategy(session, id=7, name="monday strategy")
        session.commit()
    _add_symbol(session_factory, "RELIANCE", "2885")

    monkeypatch.setattr(eod_service, "backfill_missing_candles", lambda *a, **k: None)

    def flaky_run_backtest(session_factory, strategy_id, symbol, timeframe, start, end, session_name=None, run_master_id=None, source=None):
        if timeframe == "3min":
            raise RuntimeError("simulated backtest failure")
        return {"run_master_id": 99, "run_id": 1, "summary": {}, "run_time_seconds": 0}

    monkeypatch.setattr(eod_service, "run_backtest", flaky_run_backtest)

    result = run_eod_cycle(session_factory, rest_broker=object(), aggregator=object(), now=monday)  # must not raise

    assert result["backtests_completed"] == 2  # 1min and 5min still ran
    assert result["backtest_errors"] == [(7, "RELIANCE", "3min")]
