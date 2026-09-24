from datetime import datetime

from brokers.models import Candle


def _register_symbol(client, symbol):
    return client.post("/api/symbols", json={"symbol": symbol, "exchange": "NSE", "segment": "EQUITY"}).get_json()["id"]


def test_backfill_fetches_and_persists(client, fake_broker):
    instrument_id = _register_symbol(client, "RELIANCE")
    fake_broker.historical_candles = [
        Candle(symbol="RELIANCE", timeframe="1day", timestamp=datetime(2026, 1, 1), open=100, high=101, low=99, close=100.5, volume=10),
    ]

    resp = client.post("/api/historical-data/backfill", json={
        "instrument_id": instrument_id, "timeframe": "1day",
        "start_date": "2026-01-01", "end_date": "2026-01-05",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["already_covered"] is False
    assert body["fetched"] == 1
    # symbol registration itself spawns its own background 1-min gap-fill
    # backfill (feed/gap_fill.py) on the SAME fake broker method — filter to
    # this test's own "1day" call rather than asserting total call count.
    daily_calls = [c for c in fake_broker.historical_calls if c[3] == "1day"]
    assert len(daily_calls) == 1


def test_backfill_missing_fields_is_400(client):
    resp = client.post("/api/historical-data/backfill", json={"timeframe": "1day"})
    assert resp.status_code == 400


def test_backfill_unknown_instrument_is_404(client):
    resp = client.post("/api/historical-data/backfill", json={
        "instrument_id": 9999, "timeframe": "1day", "start_date": "2026-01-01", "end_date": "2026-01-05",
    })
    assert resp.status_code == 404


def test_coverage_returns_null_when_no_data(client):
    instrument_id = _register_symbol(client, "RELIANCE")
    resp = client.get(f"/api/historical-data/coverage?instrument_id={instrument_id}&timeframe=1day")
    assert resp.status_code == 200
    assert resp.get_json()["last_ts"] is None


def test_coverage_reflects_a_prior_backfill(client, fake_broker):
    instrument_id = _register_symbol(client, "RELIANCE")
    fake_broker.historical_candles = [
        Candle(symbol="RELIANCE", timeframe="1day", timestamp=datetime(2026, 1, 3), open=100, high=101, low=99, close=100.5, volume=10),
    ]
    client.post("/api/historical-data/backfill", json={
        "instrument_id": instrument_id, "timeframe": "1day",
        "start_date": "2026-01-01", "end_date": "2026-01-05",
    })

    resp = client.get(f"/api/historical-data/coverage?instrument_id={instrument_id}&timeframe=1day")
    assert resp.get_json()["last_ts"].startswith("2026-01-03")


def test_coverage_missing_params_is_400(client):
    resp = client.get("/api/historical-data/coverage")
    assert resp.status_code == 400
