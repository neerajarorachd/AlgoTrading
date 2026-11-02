"""ALGO-37: the Watch grid's signal filter data -- today's signals, patterns
and strategy hits per instrument and timeframe, whole day or last 10 candles."""
from datetime import datetime, timedelta, timezone

from api.routes_watch_events import count_events

_IST = timedelta(hours=5, minutes=30)


def _register(client, symbol="RELIANCE"):
    return client.post("/api/symbols", json={"symbol": symbol, "exchange": "NSE", "segment": "EQUITY"}).get_json()["id"]


def _today_at(minutes_after_open):
    """A UTC time today (IST date) at 09:15 IST + N minutes."""
    today = (datetime.now(timezone.utc) + _IST).date()
    return datetime(today.year, today.month, today.day, 3, 45, tzinfo=timezone.utc) + timedelta(minutes=minutes_after_open)


def _act(iid, tf, minute, activity, activity_type="candle_pattern"):
    return {"instrument_id": iid, "timeframe": tf, "ts": _today_at(minute), "activity_type": activity_type,
            "activity": activity, "intensity": None, "open_price": 1, "high_price": 1, "low_price": 1, "close_price": 1}


def _candle(iid, tf, minute):
    return {"instrument_id": iid, "timeframe": tf, "ts": _today_at(minute)}


def test_counts_today_by_kind_and_timeframe(app, client):
    a = _register(client, "RELIANCE")
    b = _register(client, "TCS")
    engine = app.extensions["activity_engine"]
    engine._buffer += [
        _act(a, "1min", 0, "hammer"), _act(a, "1min", 5, "hammer"),
        _act(a, "3min", 6, "macd_departure_bull", "pattern"),
        _act(b, "5min", 10, "swing_high", "structure"),
        # yesterday: not counted
        {**_act(a, "1min", 0, "doji"), "ts": _today_at(0) - timedelta(days=1)},
    ]
    body = client.get("/api/watch-events").get_json()
    assert body["window"] == "day"
    got = body["instruments"]
    assert got[str(a)]["1min"]["signals"] == {"hammer": 2}
    assert got[str(a)]["3min"]["patterns"] == {"macd_departure_bull": 1}
    assert got[str(b)]["5min"]["signals"] == {"swing_high": 1}
    assert "doji" not in got[str(a)]["1min"]["signals"]


def test_last_10_candles_uses_each_timeframes_latest_candle(app, client):
    a = _register(client)
    engine = app.extensions["activity_engine"]
    # latest 3-min candle at minute 60 -> the last 10 candles start at minute 33
    engine._indicator_buffer += [_candle(a, "3min", 60)]
    engine._buffer += [_act(a, "3min", 30, "doji"), _act(a, "3min", 33, "hammer"), _act(a, "3min", 60, "hammer")]
    day = client.get("/api/watch-events?window=day").get_json()["instruments"][str(a)]["3min"]["signals"]
    last = client.get("/api/watch-events?window=last10").get_json()["instruments"][str(a)]["3min"]["signals"]
    assert day == {"doji": 1, "hammer": 2}
    assert last == {"hammer": 2}


def test_rejects_an_unknown_window(client):
    assert client.get("/api/watch-events?window=week").status_code == 400


def test_unregistered_instruments_are_left_out(app, client):
    a = _register(client)
    app.extensions["activity_engine"]._buffer.append(_act(a + 999, "1min", 0, "hammer"))
    assert str(a + 999) not in client.get("/api/watch-events").get_json()["instruments"]


def test_count_events_skips_duplicates_and_maps_strategy_rows():
    rows = [_act(1, "1min", 0, "7", "strategy"), _act(1, "1min", 0, "7", "strategy"), _act(1, "1min", 1, "doji")]
    out = count_events(rows, {})
    assert out[1]["1min"] == {"signals": {"doji": 1}, "patterns": {}, "strategies": {"7": 1}}
