from datetime import datetime, timedelta, timezone

import pytest

from db.models import CandleHistorical, CandleToday

_IST_OFFSET = timedelta(hours=5, minutes=30)


def _ist_today():
    return (datetime.now(timezone.utc) + _IST_OFFSET).date()


def _seed_candle(session_factory, minute, close=100.0, ts=None):
    # Defaults to a timestamp on TODAY's own IST date, not a fixed past
    # date -- the new live/stale date check in get_candles() means a
    # hardcoded past date would be (correctly) classified as stale, not
    # live, breaking tests that don't care about that distinction.
    with session_factory() as session:
        session.add(CandleToday(
            symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1min",
            ts=ts or datetime(_ist_today().year, _ist_today().month, _ist_today().day, 9, minute, tzinfo=timezone.utc),
            open_price=close - 1, high_price=close + 1, low_price=close - 2,
            close_price=close, volume=100,
        ))
        session.commit()


def _seed_historical_candle(session_factory, day, hour, minute, close=100.0):
    with session_factory() as session:
        session.add(CandleHistorical(
            symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1min",
            ts=datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc),
            open_price=close - 1, high_price=close + 1, low_price=close - 2,
            close_price=close, volume=100,
        ))
        session.commit()


def test_get_candles_returns_plain_floats_not_decimal(client, session_factory):
    today = _ist_today()
    _seed_candle(session_factory, minute=15, close=2854.65)

    resp = client.get("/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["live"] is True
    assert body["as_of_date"] is None
    assert len(body["candles"]) == 1
    assert isinstance(body["candles"][0]["close"], float)
    assert body["candles"][0]["close"] == 2854.65
    assert body["candles"][0]["ts"] == f"{today.isoformat()}T09:15:00Z"


def test_get_candles_todays_own_data_is_live(client, session_factory):
    _seed_candle(session_factory, minute=15, close=100.0)

    resp = client.get("/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min")
    body = resp.get_json()
    assert body["live"] is True
    assert body["as_of_date"] is None


def test_get_candles_stale_candles_today_rows_are_not_labeled_live(client, session_factory):
    # candles_today has rows, but none from today -- e.g. the daily flush
    # never ran and old rows just kept accumulating. Must NOT be reported as
    # live, and only the latest available day's rows should come back, not
    # the whole mixed multi-day history sitting in the table.
    _seed_candle(session_factory, minute=15, close=50.0, ts=datetime(2026, 9, 10, 9, 15, tzinfo=timezone.utc))
    _seed_candle(session_factory, minute=15, close=60.0, ts=datetime(2026, 9, 11, 9, 15, tzinfo=timezone.utc))

    resp = client.get("/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min")
    body = resp.get_json()
    assert body["live"] is False
    assert body["as_of_date"] == "2026-09-11"
    assert [row["close"] for row in body["candles"]] == [60.0]


def test_get_candles_prefers_richer_historical_day_over_sparse_stale_today_row(client, session_factory):
    # Real bug found live 2026-10-05: candles_today can hold a single
    # orphaned row on a chronologically NEWER date than anything else in
    # the table (e.g. one tick before a connection drop), while
    # candles_historical has a full, genuine trading session on an older
    # date. Recency alone picked the useless 1-candle day before this fix;
    # completeness (row count) must win instead.
    _seed_candle(session_factory, minute=29, close=99.0, ts=datetime(2026, 10, 1, 10, 29, tzinfo=timezone.utc))  # 1 lone row, newer date
    with session_factory() as session:
        for m in range(15, 20):  # 5 rows, a "fuller" (if still small) session on an OLDER date
            session.add(CandleHistorical(
                symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1min",
                ts=datetime(2026, 9, 17, 9, m, tzinfo=timezone.utc),
                open_price=49, high_price=51, low_price=48, close_price=50 + m, volume=100,
            ))
        session.commit()

    resp = client.get("/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min")
    body = resp.get_json()
    assert body["live"] is False
    assert body["as_of_date"] == "2026-09-17"
    assert len(body["candles"]) == 5


def test_get_candles_filters_by_timeframe_and_range(client, session_factory):
    _seed_candle(session_factory, minute=15, close=100.0, ts=datetime(2026, 9, 10, 9, 15, tzinfo=timezone.utc))
    _seed_candle(session_factory, minute=16, close=101.0, ts=datetime(2026, 9, 10, 9, 16, tzinfo=timezone.utc))
    _seed_candle(session_factory, minute=17, close=102.0, ts=datetime(2026, 9, 10, 9, 17, tzinfo=timezone.utc))

    resp = client.get(
        "/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min"
        "&from=2026-09-10T09:16:00Z&to=2026-09-10T09:17:00Z"
    )
    body = resp.get_json()
    assert [row["close"] for row in body["candles"]] == [101.0, 102.0]


def test_get_candles_falls_back_to_latest_historical_day_when_today_empty(client, session_factory):
    # candles_today has nothing (e.g. before market open / non-trading day)
    # but candles_historical has an earlier day's full session -- the
    # endpoint should return that instead of an empty array, flagged as not live.
    _seed_historical_candle(session_factory, day=17, hour=9, minute=15, close=50.0)
    _seed_historical_candle(session_factory, day=17, hour=9, minute=16, close=51.0)
    _seed_historical_candle(session_factory, day=16, hour=9, minute=15, close=40.0)  # older day, should be excluded

    resp = client.get("/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["live"] is False
    assert body["as_of_date"] == "2026-09-17"
    assert [row["close"] for row in body["candles"]] == [50.0, 51.0]


def test_get_candles_explicit_range_does_not_fall_back(client, session_factory):
    # a caller asking for a specific past range gets exactly that range back,
    # empty or not -- the fallback is only for "give me whatever's current"
    _seed_historical_candle(session_factory, day=17, hour=9, minute=15, close=50.0)

    resp = client.get(
        "/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min"
        "&from=2026-09-10T09:16:00Z&to=2026-09-10T09:17:00Z"
    )
    body = resp.get_json()
    assert body["live"] is True
    assert body["candles"] == []


def test_get_candles_empty_when_no_data_anywhere(client, session_factory):
    resp = client.get("/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min")
    body = resp.get_json()
    assert body == {"candles": [], "live": True, "as_of_date": None}


def test_get_candles_requires_symbol_and_exchange_segment(client):
    resp = client.get("/api/candles?symbol=RELIANCE")
    assert resp.status_code == 400


def _seed_instrument(session_factory, symbol="RELIANCE", exchange_segment="NSE_EQ", security_id="2885"):
    from db.models import SubscribedSymbol
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment=exchange_segment,
            security_id=security_id, previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _seed_indicator_row(session_factory, instrument_id, minute, ts=None, **values):
    from db.models import CandleIndicators
    with session_factory() as session:
        session.add(CandleIndicators(
            instrument_id=instrument_id, timeframe="1min",
            ts=ts or datetime(_ist_today().year, _ist_today().month, _ist_today().day, 9, minute, tzinfo=timezone.utc),
            **values,
        ))
        session.commit()


def test_get_candle_indicators_returns_plain_floats(client, session_factory):
    instrument_id = _seed_instrument(session_factory)
    _seed_indicator_row(session_factory, instrument_id, minute=15, rsi=55.25, vwap=2854.65, ema5=2850.1)

    resp = client.get("/api/candles/indicators?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min")
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["indicators"]) == 1
    row = body["indicators"][0]
    assert isinstance(row["rsi"], float)
    assert row["rsi"] == 55.25
    assert row["vwap"] == 2854.65
    assert row["ema5"] == 2850.1
    assert row["ema14"] is None  # never set on this row -- nullable warm-up columns stay null, not 0


def test_get_candle_indicators_defaults_to_today_only(client, session_factory):
    instrument_id = _seed_instrument(session_factory)
    _seed_indicator_row(session_factory, instrument_id, minute=15, ts=datetime(2026, 9, 1, 9, 15, tzinfo=timezone.utc), rsi=40.0)
    _seed_indicator_row(session_factory, instrument_id, minute=20, rsi=60.0)

    resp = client.get("/api/candles/indicators?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min")
    body = resp.get_json()
    assert [row["rsi"] for row in body["indicators"]] == [60.0]


def test_get_candle_indicators_unknown_symbol_returns_empty_list(client, session_factory):
    resp = client.get("/api/candles/indicators?symbol=NOPE&exchange_segment=NSE_EQ&timeframe=1min")
    assert resp.status_code == 200
    assert resp.get_json()["indicators"] == []


def test_get_candle_indicators_requires_symbol_and_exchange_segment(client):
    resp = client.get("/api/candles/indicators?symbol=RELIANCE")
    assert resp.status_code == 400


def test_get_candle_pivots_computes_classic_formula_from_prior_day(client, session_factory):
    with session_factory() as session:
        session.add(CandleHistorical(
            symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1day",
            ts=datetime(2026, 9, 30, 9, 15, tzinfo=timezone.utc),
            open_price=2820.0, high_price=2860.0, low_price=2820.0, close_price=2840.0, volume=0,
        ))
        session.commit()

    resp = client.get("/api/candles/pivots?symbol=RELIANCE&exchange_segment=NSE_EQ")
    assert resp.status_code == 200
    pivots = resp.get_json()["pivots"]
    expected_p = (2860.0 + 2820.0 + 2840.0) / 3
    assert pivots["p"] == pytest.approx(expected_p)
    assert pivots["r1"] == pytest.approx(2 * expected_p - 2820.0)
    assert pivots["s1"] == pytest.approx(2 * expected_p - 2860.0)


def test_get_candle_pivots_ignores_a_same_day_1day_candle(client, session_factory):
    # The "previous day" candle must be strictly before today -- a same-day
    # 1day row (e.g. an in-progress daily rollup) must not be mistaken for it.
    with session_factory() as session:
        session.add(CandleHistorical(
            symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1day",
            ts=datetime(_ist_today().year, _ist_today().month, _ist_today().day, 3, 45, tzinfo=timezone.utc),
            open_price=2820.0, high_price=2860.0, low_price=2820.0, close_price=2840.0, volume=0,
        ))
        session.commit()

    resp = client.get("/api/candles/pivots?symbol=RELIANCE&exchange_segment=NSE_EQ")
    assert resp.get_json()["pivots"] is None


def test_get_candle_pivots_null_when_no_prior_day_data(client):
    resp = client.get("/api/candles/pivots?symbol=RELIANCE&exchange_segment=NSE_EQ")
    assert resp.status_code == 200
    assert resp.get_json()["pivots"] is None


def test_get_candle_pivots_requires_symbol_and_exchange_segment(client):
    resp = client.get("/api/candles/pivots?symbol=RELIANCE")
    assert resp.status_code == 400


def _seed_activity(session_factory, instrument_id, activity, activity_type="candle_pattern", minute=15, ts=None):
    from db.models import InstrumentActivity
    with session_factory() as session:
        session.add(InstrumentActivity(
            instrument_id=instrument_id, timeframe="1min",
            ts=ts or datetime(_ist_today().year, _ist_today().month, _ist_today().day, 9, minute, tzinfo=timezone.utc),
            activity_type=activity_type, activity=activity,
            open_price=100.0, high_price=101.0, low_price=99.0, close_price=100.5,
        ))
        session.commit()


def test_get_candle_markers_classifies_direction(client, session_factory):
    instrument_id = _seed_instrument(session_factory)
    _seed_activity(session_factory, instrument_id, "double_bottom", minute=15)
    _seed_activity(session_factory, instrument_id, "double_top", minute=20)
    _seed_activity(session_factory, instrument_id, "doji", minute=25)  # no defined direction

    resp = client.get("/api/candles/markers?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min")
    assert resp.status_code == 200
    markers = resp.get_json()["markers"]
    assert [m["activity"] for m in markers] == ["double_bottom", "double_top", "doji"]
    assert [m["direction"] for m in markers] == ["bull", "bear", None]


def test_get_candle_markers_defaults_to_today_only(client, session_factory):
    instrument_id = _seed_instrument(session_factory)
    _seed_activity(session_factory, instrument_id, "double_bottom", ts=datetime(2026, 9, 1, 9, 15, tzinfo=timezone.utc))
    _seed_activity(session_factory, instrument_id, "double_top", minute=20)

    resp = client.get("/api/candles/markers?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min")
    markers = resp.get_json()["markers"]
    assert [m["activity"] for m in markers] == ["double_top"]


def test_get_candle_markers_unknown_symbol_returns_empty_list(client):
    resp = client.get("/api/candles/markers?symbol=NOPE&exchange_segment=NSE_EQ&timeframe=1min")
    assert resp.status_code == 200
    assert resp.get_json()["markers"] == []


def test_get_candles_live_returns_only_todays_session(client, session_factory):
    # Real bug found live 2026-10-06: candles_today held weeks of stale rows
    # plus a bogus epoch-zero candle, and the live branch returned them all.
    _seed_candle(session_factory, minute=15, close=50.0, ts=datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc))
    _seed_candle(session_factory, minute=15, close=60.0, ts=datetime(2026, 9, 11, 9, 15, tzinfo=timezone.utc))
    _seed_candle(session_factory, minute=20, close=70.0)  # today

    body = client.get("/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min").get_json()
    assert body["live"] is True
    assert [row["close"] for row in body["candles"]] == [70.0]


def _today_utc(hour, minute):
    t = _ist_today()
    return datetime(t.year, t.month, t.day, hour, minute, tzinfo=timezone.utc)


def test_get_candle_indicators_includes_the_live_engines_unflushed_rows(app, client, session_factory):
    # ActivityEngine only flushes to the DB once a day -- during market
    # hours today's values live in memory and must still be served.
    instrument_id = _seed_instrument(session_factory)
    _seed_indicator_row(session_factory, instrument_id, minute=15, ts=_today_utc(4, 0), rsi=40.0)
    app.extensions["activity_engine"]._indicator_buffer.append(
        {"instrument_id": instrument_id, "timeframe": "1min", "ts": _today_utc(4, 1), "rsi": 41.5, "ema5": 2850.25},
    )
    app.extensions["activity_engine"]._indicator_buffer.append(
        {"instrument_id": instrument_id, "timeframe": "3min", "ts": _today_utc(4, 3), "rsi": 99.0},  # other timeframe
    )

    rows = client.get("/api/candles/indicators?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min").get_json()["indicators"]
    assert [r["rsi"] for r in rows] == [40.0, 41.5]
    assert rows[1]["ema5"] == 2850.25
    assert rows[1]["vwap"] is None  # key absent from the in-memory dict -> null, not an error


def test_get_candle_markers_includes_unflushed_activities_without_duplicates(app, client, session_factory):
    instrument_id = _seed_instrument(session_factory)
    _seed_activity(session_factory, instrument_id, "double_bottom", ts=_today_utc(4, 0))
    engine = app.extensions["activity_engine"]
    engine._buffer.append({"instrument_id": instrument_id, "timeframe": "1min", "ts": _today_utc(4, 0),
                           "activity_type": "graph_formation", "activity": "double_bottom"})  # same as stored
    engine._buffer.append({"instrument_id": instrument_id, "timeframe": "1min", "ts": _today_utc(4, 5),
                           "activity_type": "indicator", "activity": "macd_bearish_cross"})

    markers = client.get("/api/candles/markers?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min").get_json()["markers"]
    assert [(m["activity"], m["direction"]) for m in markers] == [("double_bottom", "bull"), ("macd_bearish_cross", "bear")]


def test_get_candle_markers_honours_an_explicit_range(client, session_factory):
    # The chart passes the day it's actually showing when it fell back to a
    # previous session -- markers must follow, not stay pinned to today.
    instrument_id = _seed_instrument(session_factory)
    _seed_activity(session_factory, instrument_id, "double_top", ts=datetime(2026, 9, 1, 4, 0, tzinfo=timezone.utc))
    _seed_activity(session_factory, instrument_id, "double_bottom", minute=20)  # today

    url = "/api/candles/markers?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=1min&from=2026-09-01T03:45:00Z&to=2026-09-01T10:00:00Z"
    assert [m["activity"] for m in client.get(url).get_json()["markers"]] == ["double_top"]


def test_get_candles_rejects_invalid_timeframe(client):
    resp = client.get("/api/candles?symbol=RELIANCE&exchange_segment=NSE_EQ&timeframe=10min")
    assert resp.status_code == 400
