"""chart_cache.py: past sessions served from memory must match the DB answer
exactly, and any past-day write must drop the stale copy (2026-11-09)."""
from datetime import datetime, timedelta, timezone

import pytest

import chart_cache
from brokers.models import Candle
from db.models import CandleHistorical, CandleIndicators, CandleToday, InstrumentActivity, SubscribedSymbol
from db.ops import LibActivities, LibCandleIndicators, LibCandles, LibCandlesHistorical

_IST_OFFSET = timedelta(hours=5, minutes=30)
Q = "symbol=RELIANCE&exchange_segment=NSE_EQ"


def _today():
    return (datetime.now(timezone.utc) + _IST_OFFSET).date()


def _ts(days_ago, minute):
    """09:15 IST + `minute` on the IST day `days_ago` back, as aware UTC."""
    d = _today() - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, 3, 45, tzinfo=timezone.utc) + timedelta(minutes=minute)


def _iso(ts):
    return ts.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


@pytest.fixture
def seeded(session_factory):
    """6 past sessions (10 one-minute candles each, with indicators and a
    marker on each) plus 3 candles today."""
    with session_factory() as s:
        inst = SubscribedSymbol(symbol="RELIANCE", exchange="NSE", segment="EQUITY", exchange_segment="NSE_EQ",
                                security_id="2885", previous_close=100.0)
        s.add(inst)
        s.flush()
        for days_ago in range(6, 0, -1):
            for m in range(10):
                ts = _ts(days_ago, m)
                close = 100 + days_ago + m / 10
                s.add(CandleHistorical(symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1min", ts=ts,
                                       open_price=close - 1, high_price=close + 1, low_price=close - 2,
                                       close_price=close, volume=10 + m))
                s.add(CandleIndicators(instrument_id=inst.id, timeframe="1min", ts=ts, rsi=50 + m, vwap=close))
            s.add(InstrumentActivity(instrument_id=inst.id, timeframe="1min", ts=_ts(days_ago, 4),
                                     activity_type="candle_pattern", activity="double_bottom",
                                     open_price=1, high_price=1, low_price=1, close_price=1))
        for m in range(3):
            s.add(CandleToday(symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1min", ts=_ts(0, m),
                              open_price=200, high_price=201, low_price=199, close_price=200 + m, volume=5))
        s.commit()
        return inst.id


@pytest.fixture
def cache(app, session_factory):
    c = chart_cache.PastChartCache(session_factory, sessions=4)
    app.extensions["chart_cache"] = c
    chart_cache.install(c)
    yield c
    chart_cache.install(None)


def _both(app, client, cache, url):
    """(answer from the DB, answer with the cache built)."""
    app.extensions["chart_cache"] = None
    from_db = client.get(url).get_json()
    app.extensions["chart_cache"] = cache
    for tf in chart_cache.TIMEFRAMES:
        assert cache.build("RELIANCE", "NSE_EQ", tf)
    return from_db, client.get(url).get_json()


@pytest.mark.parametrize("url", [
    f"/api/candles?{Q}&timeframe=1min&days=3",
    f"/api/candles?{Q}&timeframe=1min&days=5",   # = all cached sessions
    f"/api/candles?{Q}&timeframe=1min&days=7",   # more than cached -> DB
    f"/api/candles?{Q}&timeframe=3min&days=3",   # rolled up from 1-min
    f"/api/candles?{Q}&timeframe=5min&days=21",
    f"/api/candles?{Q}&timeframe=1min&days=1",
])
def test_candles_match_the_db(app, client, seeded, cache, url):
    from_db, cached = _both(app, client, cache, url)
    assert cached == from_db
    assert cached["candles"]  # not trivially empty


@pytest.mark.parametrize("days_ago,days", [(2, 2), (3, 1), (4, 3), (5, 1), (6, 2)])
def test_scroll_back_matches_the_db(app, client, seeded, cache, days_ago, days):
    before = _iso(_ts(days_ago, 0))
    url = f"/api/candles?{Q}&timeframe=1min&days={days}&before={before}"
    from_db, cached = _both(app, client, cache, url)
    assert cached == from_db


@pytest.mark.parametrize("path", ["indicators", "markers"])
def test_past_indicators_and_markers_match_the_db(app, client, seeded, cache, path):
    url = f"/api/candles/{path}?{Q}&timeframe=1min&from={_iso(_ts(4, 0))}&to={_iso(_ts(0, -556))}"
    from_db, cached = _both(app, client, cache, url)
    assert cached == from_db
    assert cached[path]


def test_past_rows_come_from_memory_not_the_db(app, client, seeded, cache, monkeypatch):
    for tf in chart_cache.TIMEFRAMES:
        cache.build("RELIANCE", "NSE_EQ", tf)

    def boom(*a, **k):
        raise AssertionError("read the DB")
    monkeypatch.setattr(LibCandlesHistorical, "get_range", boom)
    monkeypatch.setattr(LibCandleIndicators, "get_for_instrument_range", boom)
    body = client.get(f"/api/candles?{Q}&timeframe=1min&days=4").get_json()
    assert len(body["candles"]) == 3 * 10 + 3 and body["has_more"] is True
    client.get(f"/api/candles/indicators?{Q}&timeframe=1min&from={_iso(_ts(3, 0))}&to={_iso(_ts(1, 9))}")


def test_today_is_never_served_from_memory(seeded, cache):
    cache.build("RELIANCE", "NSE_EQ", "1min")
    assert cache.indicator_rows(seeded, "1min", _ts(0, 0), _ts(0, 5)) is None
    assert cache.indicator_rows(seeded, "1min", _ts(1, 0), None) is None


def _candle(ts):
    return Candle(symbol="RELIANCE", timeframe="1min", timestamp=ts, open=1, high=1, low=1, close=1, volume=1)


def test_past_day_candle_write_drops_the_copy(session_factory, seeded, cache):
    cache.build("RELIANCE", "NSE_EQ", "1min")
    LibCandlesHistorical.persist_bulk(session_factory, [("RELIANCE", "NSE_EQ", _candle(_ts(2, 30)))])
    assert cache.sessions_before("RELIANCE", "NSE_EQ", "1min", _ts(0, -555), 2) is None


@pytest.mark.parametrize("write", [
    lambda sf, iid: LibCandleIndicators.persist_bulk(sf, [{"instrument_id": iid, "timeframe": "1min", "ts": _ts(1, 40), "rsi": 1}]),
    lambda sf, iid: LibActivities.persist_one(sf, {"instrument_id": iid, "timeframe": "1min", "ts": _ts(1, 41),
                                                   "activity_type": "candle_pattern", "activity": "doji",
                                                   "open_price": 1, "high_price": 1, "low_price": 1, "close_price": 1}),
    lambda sf, iid: LibActivities.delete_for_instrument_since(sf, iid, _ts(2, 0)),
])
def test_past_day_indicator_or_marker_write_drops_the_copy(session_factory, seeded, cache, write):
    cache.build("RELIANCE", "NSE_EQ", "1min")
    write(session_factory, seeded)
    assert cache.marker_rows(seeded, "1min", _ts(3, 0), _ts(1, 9)) is None


def test_todays_writes_keep_the_copy(session_factory, seeded, cache):
    cache.build("RELIANCE", "NSE_EQ", "1min")
    LibCandles.persist_one(session_factory, "RELIANCE", "NSE_EQ", _candle(_ts(0, 5)))
    LibActivities.delete_for_instrument_since(session_factory, seeded, _ts(0, 0))
    assert cache.sessions_before("RELIANCE", "NSE_EQ", "1min", _ts(0, -555), 2) is not None


def test_build_racing_a_past_write_is_dropped(session_factory, seeded, cache, monkeypatch):
    real = LibCandleIndicators.get_for_instrument_range

    def write_meanwhile(*a, **k):
        cache.invalidate_symbol("RELIANCE", "NSE_EQ")
        return real(*a, **k)
    monkeypatch.setattr(LibCandleIndicators, "get_for_instrument_range", write_meanwhile)
    assert cache.build("RELIANCE", "NSE_EQ", "1min") is False
    assert cache.sessions_before("RELIANCE", "NSE_EQ", "1min", _ts(0, -555), 2) is None


def test_an_entry_from_yesterday_is_not_served(seeded, cache, monkeypatch):
    cache.build("RELIANCE", "NSE_EQ", "1min")
    monkeypatch.setattr(chart_cache, "ist_today", lambda: _today() + timedelta(days=1))
    assert cache.sessions_before("RELIANCE", "NSE_EQ", "1min", _ts(0, -555), 2) is None
