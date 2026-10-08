"""replay_feed.py -- the TEMPORARY replay "live" feed. Driven synchronously
here (prepare + emit_next_batch), never via start()'s background thread."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from db.models import CandleHistorical, CandleToday, SubscribedSymbol
from replay_feed import ReplayFeed

REPLAY_DATE = date(2026, 9, 15)
TODAY = date(2026, 10, 7)
SHIFT = TODAY - REPLAY_DATE
OPEN_UTC = datetime(2026, 9, 15, 3, 45, tzinfo=timezone.utc)  # 09:15 IST


def _day_file(tmp_path, minutes=20):
    def candles(base):
        return [[int((OPEN_UTC + timedelta(minutes=i)).timestamp()), base + i, base + i + 1, base + i - 1, base + i + 0.5, 1000 + i]
                for i in range(minutes)]
    data = {"date": REPLAY_DATE.isoformat(), "symbols": {
        sym: {"security_id": sid, "exchange": "NSE", "segment": "EQUITY", "exchange_segment": "NSE_EQ",
              "previous_session": "2026-09-11", "previous_close": base - 5, "previous_high": base + 3,
              "previous_low": base - 9, "candles": candles(base)}
        for sym, sid, base in (("RELIANCE", "2885", 1260.0), ("TCS", "11536", 2200.0))
    }}
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(data))
    return path


def _replay(app, db_engine, session_factory, tmp_path, ticks, minutes=20, batch=15):
    return ReplayFeed(
        _day_file(tmp_path, minutes), db_engine, session_factory,
        app.extensions["candle_aggregator"], app.extensions["activity_engine"],
        batch_minutes=batch, interval_sec=0, today=TODAY, broadcast_tick=ticks.append,
    )


def test_refuses_any_database_but_sqlite(tmp_path):
    class FakeEngine:
        class dialect:
            name = "mssql"
    with pytest.raises(RuntimeError, match="refuses"):
        ReplayFeed(_day_file(tmp_path), FakeEngine(), None, None, None)


def test_prepare_registers_symbols_and_the_previous_sessions_daily_candle(app, db_engine, session_factory, tmp_path):
    replay = _replay(app, db_engine, session_factory, tmp_path, [])
    replay.prepare()
    with session_factory() as session:
        syms = {s.symbol: s for s in session.query(SubscribedSymbol)}
        assert set(syms) == {"RELIANCE", "TCS"}
        assert float(syms["RELIANCE"].previous_close) == 1255.0
        daily = session.query(CandleHistorical).filter_by(symbol="RELIANCE", timeframe="1day").one()
        # previous session shifted by the same offset as the replayed day, so
        # pivot points (latest 1day before "today") read it
        assert daily.ts.date() == date(2026, 9, 11) + SHIFT
        assert float(daily.high_price) == 1263.0 and float(daily.low_price) == 1251.0


def test_each_batch_plays_the_next_minutes_shifted_to_today(app, db_engine, session_factory, tmp_path):
    ticks = []
    replay = _replay(app, db_engine, session_factory, tmp_path, ticks)
    replay.prepare()

    assert replay.emit_next_batch() == 15
    with session_factory() as session:
        one_min = session.query(CandleToday).filter_by(symbol="RELIANCE", timeframe="1min").order_by(CandleToday.ts).all()
        three_min = session.query(CandleToday).filter_by(symbol="RELIANCE", timeframe="3min").count()
    assert len(one_min) == 15
    assert one_min[0].ts.date() == TODAY  # shifted to today, so "today" screens treat it as live
    assert three_min == 4  # 5th bucket still open until its next minute arrives -- same as live
    assert len(ticks) == 30  # one tick per symbol per minute
    reliance_ticks = [t for t in ticks if t["symbol"] == "RELIANCE"]
    assert reliance_ticks[-1]["ltp"] == 1260.0 + 14 + 0.5
    assert reliance_ticks[-1]["previous_close"] == 1255.0
    assert replay.status()["sim_time"] == "09:30"

    assert replay.emit_next_batch() == 5  # the rest of the 20-minute session
    assert replay.emit_next_batch() == 0  # session over
    assert replay.status()["minutes_played"] == 20


def test_rerunning_the_replay_starts_from_a_clean_session(app, db_engine, session_factory, tmp_path):
    first = _replay(app, db_engine, session_factory, tmp_path, [])
    first.prepare()
    first.emit_next_batch()
    again = _replay(app, db_engine, session_factory, tmp_path, [])
    again.prepare()
    with session_factory() as session:
        assert session.query(CandleToday).filter_by(symbol="RELIANCE").count() == 0


def test_feed_status_reports_replay_mode(app, client, db_engine, session_factory, tmp_path):
    replay = _replay(app, db_engine, session_factory, tmp_path, [])
    app.extensions["replay_feed"] = replay
    body = client.get("/api/feed/status").get_json()
    assert body["mode"] == "replay"
    assert body["replay_date"] == "2026-09-15"
    assert body["shown_as_date"] == TODAY.isoformat()
    assert body["minutes_total"] == 20


def test_each_new_pattern_or_signal_is_broadcast_as_an_activity_event(app, db_engine, session_factory, tmp_path):
    from api.ws_live import socketio
    ws = socketio.test_client(app)
    ws.emit("subscribe_ticks", {"rooms": ["NSE:RELIANCE", "NSE:TCS"]})
    ws.get_received()  # drop anything from the handshake

    replay = _replay(app, db_engine, session_factory, tmp_path, [], minutes=60, batch=60)
    replay.prepare()
    replay.emit_next_batch()

    activities = [m["args"][0] for m in ws.get_received() if m["name"] == "activity"]
    assert activities, "60 minutes of candles through the real engine should fire at least one pattern/signal"
    first = activities[0]
    assert set(first) >= {"symbol", "timeframe", "ts", "activity_type", "activity", "direction"}
    assert first["ts"].startswith(TODAY.isoformat())
    # the same detections are flushed to the DB right after the batch (replay only)
    from db.models import InstrumentActivity
    with session_factory() as session:
        assert session.query(InstrumentActivity).count() == len(activities)
