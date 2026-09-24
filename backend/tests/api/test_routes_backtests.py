import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from brokers.models import Candle  # noqa: E402
from db.models import CandleIndicators, SubscribedSymbol  # noqa: E402
from db.ops import LibCandlesHistorical  # noqa: E402

SYMBOL = "RELIANCE"
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _ts(hour, minute, day=16):
    return datetime(2026, 6, day, hour, minute, tzinfo=timezone.utc) - _IST_OFFSET


def _zigzag_highs(checkpoints):
    """Same helper as test_run_backtest.py's own — reproduced here to
    avoid a cross-test-file import for one small helper (this project's
    own established convention, see test_order_backtest.py's docstring)."""
    highs = []
    for (i0, h0), (i1, h1) in zip(checkpoints, checkpoints[1:]):
        for j in range(i0, i1):
            highs.append(h0 + (h1 - h0) * (j - i0) / (i1 - i0))
    highs.append(checkpoints[-1][1])
    return highs


def _seed_double_top_candles(session_factory, symbol=SYMBOL):
    highs = _zigzag_highs([(0, 90.0), (10, 100.3), (20, 95.0), (30, 100.2), (40, 92.0)])
    candles = [
        (symbol, "NSE_EQ", Candle(
            symbol=symbol, timeframe="1min", timestamp=_ts(9, 15) + timedelta(minutes=i),
            open=h - 0.5, high=h, low=h - 1.0, close=h - 0.5, volume=100,
        ))
        for i, h in enumerate(highs)
    ]
    LibCandlesHistorical.persist_bulk(session_factory, candles)
    return candles[0][2].timestamp, candles[-1][2].timestamp + timedelta(days=1)


def _register_symbol(client, symbol=SYMBOL):
    with client.application.extensions["db_session_factory"]() as session:
        row = SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
            security_id=f"SEC-{symbol}", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _create_strategy(client, **overrides):
    fields = dict(
        name="DB-driven route test", strategy_type="entry",
        sl_formula_type="fixed_percent", sl_fixed_value=0.01,
        target_formula_type="fixed_percent", target_fixed_value=0.01,
        capital_per_trade=1_000_000.0, max_vol_per_call=100_000,
        max_orders_at_a_time=100, exit_at_loss_count=100,
    )
    fields.update(overrides)
    return client.post("/api/strategies", json=fields).get_json()["id"]


def test_create_run_for_single_instrument_persists_and_returns_summary(client, session_factory):
    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)

    resp = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id, "session_name": "route test",
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert len(body["runs"]) == 1
    run = body["runs"][0]
    assert run["instrument_id"] == instrument_id
    assert run["summary"]["total_trades"] >= 1

    master_resp = client.get(f"/api/backtests/runs/{body['run_master_id']}")
    assert master_resp.status_code == 200
    master_body = master_resp.get_json()
    assert master_body["run_count"] == 1
    assert master_body["runs"][0]["result"]["total_trades"] == run["summary"]["total_trades"]
    assert master_body["runs"][0]["symbol"] == SYMBOL


def test_create_run_requires_scope_when_no_run_master_id(client, session_factory):
    strategy_id = _create_strategy(client)
    resp = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min", "start": "2025-03-01", "end": "2025-03-31",
    })
    assert resp.status_code == 400


def test_create_run_unknown_strategy_returns_404(client):
    resp = client.post("/api/backtests/runs", json={
        "strategy_id": 999999, "timeframe": "1min", "start": "2025-03-01", "end": "2025-03-31",
        "instrument_id": 1,
    })
    assert resp.status_code == 404


def test_create_run_for_watchlist_creates_one_run_per_member(client, session_factory):
    reliance_id = _register_symbol(client, "RELIANCE")
    tcs_id = _register_symbol(client, "TCS")
    start, end = _seed_double_top_candles(session_factory, "RELIANCE")
    _seed_double_top_candles(session_factory, "TCS")
    strategy_id = _create_strategy(client)

    watchlist = client.post("/api/watchlists", json={
        "name": "WL", "instrument_ids": [reliance_id, tcs_id],
    }).get_json()

    resp = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "watchlist_id": watchlist["id"],
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert len(body["runs"]) == 2
    assert {r["instrument_id"] for r in body["runs"]} == {reliance_id, tcs_id}

    master_body = client.get(f"/api/backtests/runs/{body['run_master_id']}").get_json()
    assert master_body["run_count"] == 2


def test_create_run_attaches_to_an_existing_run_master(client, session_factory):
    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)

    first = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id,
    }).get_json()

    second = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "run_master_id": first["run_master_id"],
    })
    assert second.status_code == 201
    assert second.get_json()["run_master_id"] == first["run_master_id"]

    master_body = client.get(f"/api/backtests/runs/{first['run_master_id']}").get_json()
    assert master_body["run_count"] == 2


def test_create_run_unknown_run_master_id_returns_404(client):
    strategy_id = _create_strategy(client)
    resp = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min", "start": "2025-03-01", "end": "2025-03-31",
        "run_master_id": 999999,
    })
    assert resp.status_code == 404


def test_get_run_trades_and_day_results(client, session_factory):
    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)

    created = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id,
    }).get_json()
    run_master_id = created["run_master_id"]
    run_id = created["runs"][0]["run_id"]

    trades_resp = client.get(f"/api/backtests/runs/{run_master_id}/runs/{run_id}/trades")
    assert trades_resp.status_code == 200
    trades = trades_resp.get_json()
    assert len(trades) == created["runs"][0]["summary"]["total_trades"]
    assert all("margin_used" in t for t in trades)

    day_resp = client.get(f"/api/backtests/runs/{run_master_id}/runs/{run_id}/day-results")
    assert day_resp.status_code == 200
    assert sum(d["trade_count"] for d in day_resp.get_json()) == len(trades)


def test_list_run_masters(client, session_factory):
    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)
    client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id, "session_name": "listed session",
    })

    resp = client.get("/api/backtests/runs")
    assert resp.status_code == 200
    names = [m["name"] for m in resp.get_json()]
    assert "listed session" in names


def test_get_unknown_run_master_is_404(client):
    assert client.get("/api/backtests/runs/9999").status_code == 404


def test_create_occurrence_run_persists_a_real_run(client, session_factory):
    """2026-09-15: "I think occurance backtest is not added to the btrun" —
    confirmed real gap, this is the fix. Also confirms "you can set the
    source of the backtest as RS1/Strategy-abc/RS2 etc." """
    from db.models import PatternOutcome

    instrument_id = _register_symbol(client)
    with session_factory() as session:
        session.add_all([
            PatternOutcome(
                instrument_id=instrument_id, timeframe="1min", pattern="hammer", activity_type="candle_pattern",
                direction="bull", detected_ts=_ts(9, 15 + i), entry_price=100.0,
                pct_change_20=0.02 if i < 3 else -0.01, window_candles=30,
            )
            for i in range(4)
        ])
        session.commit()

    resp = client.post("/api/backtests/occurrence-runs", json={
        "instrument_id": instrument_id, "timeframe": "1min", "pattern": "hammer",
        "start": "2026-06-16", "end": "2026-06-17", "source": "RS2",
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["summary"]["count"] == 4
    # JSON has no integer object keys -- checkpoints' int keys (5/10/15/20/30)
    # come back as strings once serialized.
    assert body["summary"]["checkpoints"]["20"]["matched"] == 3

    master_resp = client.get(f"/api/backtests/runs/{body['run_master_id']}")
    assert master_resp.status_code == 200
    master_body = master_resp.get_json()
    assert master_body["mode"] == "occurrence_count"
    assert master_body["source"] == "RS2"


def test_create_occurrence_run_requires_all_fields(client):
    resp = client.post("/api/backtests/occurrence-runs", json={"instrument_id": 1, "timeframe": "1min"})
    assert resp.status_code == 400


def test_trade_indicator_snapshot_fields_are_none_when_no_indicators_were_loaded(client, session_factory):
    """run_backtest.py's simulate() DOES load real CandleIndicators now, but
    this test's seeded candles have no matching CandleIndicators rows at
    all -- the None-until-ready convention, exercised through the route."""
    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)

    created = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id,
    }).get_json()
    run_master_id, run_id = created["run_master_id"], created["runs"][0]["run_id"]

    trades = client.get(f"/api/backtests/runs/{run_master_id}/runs/{run_id}/trades").get_json()
    assert len(trades) >= 1
    t = trades[0]
    assert t["entry_rsi"] is None
    assert t["entry_rsi_state"] is None
    assert t["exit_macd_state"] is None


def test_trade_indicator_trend_fields_serialize_as_plain_strings(client, session_factory):
    """entry_rsi_trend/etc are TrendCode-coded columns (decoded to plain
    strings by the ORM) -- _serialize_trade must pass them through as-is,
    not run them through _num()'s float() conversion (a real bug caught
    before shipping: field.endswith("_state") alone missed "_trend")."""
    from db.models import BacktestTrade

    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)
    created = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id,
    }).get_json()
    run_master_id, run_id = created["run_master_id"], created["runs"][0]["run_id"]

    with session_factory() as session:
        session.add(BacktestTrade(
            run_id=run_id, instrument_id=instrument_id, timeframe="1min",
            pattern="hammer", direction="bull",
            entry_ts=_ts(10, 0), entry_price=100.0, stop_loss=99.0, target=102.0,
            exit_ts=_ts(10, 5), exit_price=102.0, exit_reason="target_hit",
            quantity=10, gross_pnl=20.0, expenses=1.0, net_pnl=19.0,
            entry_rsi_trend="increasing", exit_macd_trend="decreasing",
        ))
        session.commit()

    trades = client.get(f"/api/backtests/runs/{run_master_id}/runs/{run_id}/trades").get_json()
    hand_inserted = next(t for t in trades if t["pattern"] == "hammer")
    assert hand_inserted["entry_rsi_trend"] == "increasing"
    assert hand_inserted["exit_macd_trend"] == "decreasing"
    assert hand_inserted["entry_macd_trend"] is None


def test_trade_execution_path_returns_padded_candles_around_entry_and_exit(client, session_factory):
    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)

    created = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id,
    }).get_json()
    run_master_id, run_id = created["run_master_id"], created["runs"][0]["run_id"]

    trades = client.get(f"/api/backtests/runs/{run_master_id}/runs/{run_id}/trades").get_json()
    trade_id = trades[0]["id"]

    resp = client.get(f"/api/backtests/runs/{run_master_id}/runs/{run_id}/trades/{trade_id}/path?padding=3")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["trade"]["id"] == trade_id
    candles = body["candles"]
    assert len(candles) >= 1
    ts_list = [c["ts"] for c in candles]
    assert ts_list == sorted(ts_list)
    # the path must cover the trade's own entry and exit timestamps
    assert trades[0]["entry_ts"] in ts_list
    assert trades[0]["exit_ts"] in ts_list


def test_trade_execution_path_padding_zero_returns_exactly_entry_to_exit(client, session_factory):
    """The sparkline cell (TradePathSparkline.jsx) asks for padding=0 --
    no context candles, exactly the entry_ts..exit_ts window."""
    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)

    created = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id,
    }).get_json()
    run_master_id, run_id = created["run_master_id"], created["runs"][0]["run_id"]

    trades = client.get(f"/api/backtests/runs/{run_master_id}/runs/{run_id}/trades").get_json()
    trade = trades[0]

    resp = client.get(f"/api/backtests/runs/{run_master_id}/runs/{run_id}/trades/{trade['id']}/path?padding=0")
    assert resp.status_code == 200
    ts_list = [c["ts"] for c in resp.get_json()["candles"]]
    assert ts_list[0] == trade["entry_ts"]
    assert ts_list[-1] == trade["exit_ts"]


def test_run_day_chart_returns_the_whole_days_candles_with_vwap_and_bb(client, session_factory):
    """"I want full day chart, not the trade chart... also wants BB and
    vwap." -- unlike /trades/<id>/path (one trade's entry->exit window),
    this returns every candle for the given calendar date, joined with
    whatever CandleIndicators has for vwap/bb_upper/middle/lower."""
    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)

    created = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id,
    }).get_json()
    run_master_id, run_id = created["run_master_id"], created["runs"][0]["run_id"]

    with session_factory() as session:
        session.add(CandleIndicators(
            instrument_id=instrument_id, timeframe="1min", ts=_ts(9, 15),
            vwap=99.5, bb_upper=103.0, bb_middle=100.0, bb_lower=97.0,
        ))
        session.commit()

    resp = client.get(f"/api/backtests/runs/{run_master_id}/runs/{run_id}/day-chart?date=2026-06-16")
    assert resp.status_code == 200
    candles = resp.get_json()["candles"]
    assert len(candles) >= 2
    # candles are ts-ascending (get_range's own ordering) -- the seeded
    # data's very first candle (index 0) is the one with CandleIndicators
    # attached above; comparing round-tripped ISO strings directly here
    # (rather than against a freshly-built datetime's isoformat()) avoids a
    # naive-vs-aware mismatch after the SQLite round trip.
    first, second = candles[0], candles[1]
    assert first["vwap"] == 99.5
    assert first["bb_upper"] == 103.0
    assert first["bb_middle"] == 100.0
    assert first["bb_lower"] == 97.0
    # a candle with no matching CandleIndicators row -- None, not a crash
    assert second["vwap"] is None


def test_run_day_chart_requires_date(client, session_factory):
    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)
    created = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id,
    }).get_json()
    run_master_id, run_id = created["run_master_id"], created["runs"][0]["run_id"]

    resp = client.get(f"/api/backtests/runs/{run_master_id}/runs/{run_id}/day-chart")
    assert resp.status_code == 400


def test_run_day_chart_unknown_run_is_404(client):
    resp = client.get("/api/backtests/runs/1/runs/99999/day-chart?date=2026-06-16")
    assert resp.status_code == 404


def test_trade_execution_path_unknown_trade_is_404(client, session_factory):
    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)
    created = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id,
    }).get_json()
    run_master_id, run_id = created["run_master_id"], created["runs"][0]["run_id"]

    resp = client.get(f"/api/backtests/runs/{run_master_id}/runs/{run_id}/trades/999999/path")
    assert resp.status_code == 404


def test_trade_execution_path_trade_from_a_different_run_is_404(client, session_factory):
    instrument_id = _register_symbol(client)
    start, end = _seed_double_top_candles(session_factory)
    strategy_id = _create_strategy(client)

    first = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id,
    }).get_json()
    second = client.post("/api/backtests/runs", json={
        "strategy_id": strategy_id, "timeframe": "1min",
        "start": start.date().isoformat(), "end": end.date().isoformat(),
        "instrument_id": instrument_id,
    }).get_json()

    first_run_id = first["runs"][0]["run_id"]
    second_run_id = second["runs"][0]["run_id"]
    first_trade_id = client.get(
        f"/api/backtests/runs/{first['run_master_id']}/runs/{first_run_id}/trades"
    ).get_json()[0]["id"]

    # first trade's id, but addressed through the SECOND run -- must 404, not leak across runs
    resp = client.get(
        f"/api/backtests/runs/{second['run_master_id']}/runs/{second_run_id}/trades/{first_trade_id}/path"
    )
    assert resp.status_code == 404
