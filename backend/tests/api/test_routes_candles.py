from datetime import datetime, timezone

from db.models import CandleToday


def _seed_candle(session_factory, minute, close=100.0):
    with session_factory() as session:
        session.add(CandleToday(
            symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1min",
            ts=datetime(2026, 9, 10, 9, minute, tzinfo=timezone.utc),
            open_price=close - 1, high_price=close + 1, low_price=close - 2,
            close_price=close, volume=100,
        ))
        session.commit()


def test_get_candles_returns_plain_floats_not_decimal(client, session_factory):
    _seed_candle(session_factory, minute=15, close=2854.65)

    resp = client.get("/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 1
    assert isinstance(body[0]["close"], float)
    assert body[0]["close"] == 2854.65
    assert body[0]["ts"] == "2026-09-10T09:15:00Z"


def test_get_candles_filters_by_timeframe_and_range(client, session_factory):
    _seed_candle(session_factory, minute=15, close=100.0)
    _seed_candle(session_factory, minute=16, close=101.0)
    _seed_candle(session_factory, minute=17, close=102.0)

    resp = client.get(
        "/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min"
        "&from=2026-09-10T09:16:00Z&to=2026-09-10T09:17:00Z"
    )
    body = resp.get_json()
    assert [row["close"] for row in body] == [101.0, 102.0]


def test_get_candles_requires_symbol_and_exchange_segment(client):
    resp = client.get("/api/candles?symbol=RELIANCE")
    assert resp.status_code == 400


def test_get_candles_rejects_invalid_timeframe(client):
    resp = client.get("/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=10min")
    assert resp.status_code == 400
