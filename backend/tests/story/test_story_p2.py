"""Market story P2: live story in ActivityEngine behind story_enabled (off by
default), saved with the flush, moved to the archive at EOD, read by the API."""
import math
from datetime import date, datetime, timedelta, timezone

from activity_engine import ActivityEngine
from brokers.models import Candle
from db.models_story import MarketStoryArchive, MarketStoryToday
from db.ops import LibSettings, LibStory
from db.session import session_scope
from eod_service import archive_story

DAY1, DAY2 = date(2026, 10, 28), date(2026, 10, 29)
KEY = ("X", "NSE_EQ", "5min")


def _engine(session_factory, on=True, today=DAY2):
    with session_scope(session_factory) as s:
        LibSettings.set_value(s, "swing_lookback", 2)
        if on:
            LibSettings.set_value(s, "story_enabled", 1)

    class E(ActivityEngine):
        def _lookup_instrument_id(self, sym, seg):
            return 1
    e = E(session_factory)
    if e.story is not None:
        e.story._today = lambda: today
    return e


def _candles(days=2, per_day=75, start=0):
    out = []
    for d in range(days):
        t0 = datetime(2026, 10, 28, 3, 45, tzinfo=timezone.utc) + timedelta(days=d)
        for i in range(start if d == days - 1 else 0, per_day):
            p = 100 + 3 * math.sin(i / 6) + 0.02 * i + d
            out.append(Candle("X", "5min", t0 + timedelta(minutes=5 * i), p, p + 0.4, p - 0.4, p + 0.1, 1000 + 7 * (i % 5)))
    return out


def _feed(e, candles):
    for c in candles:
        e.on_candle_closed("X", "NSE_EQ", c)


def _db_rows(session_factory, model=MarketStoryToday):
    with session_scope(session_factory) as s:
        return sorted((r.subject, r.start_ts, r.state, r.is_open, r.candles) for r in s.query(model).all())


def _mem_rows(e):
    day, runs = e.story.runs(KEY)
    return sorted((r.subject, r.start_ts.astimezone(timezone.utc).replace(tzinfo=None), r.state, r.is_open, r.candles)
                  for r in runs)


def test_off_by_default_nothing_recorded(session_factory):
    e = _engine(session_factory, on=False)
    assert e.story is None
    _feed(e, _candles())
    e.flush()
    assert _db_rows(session_factory) == []


def test_story_on_keeps_only_the_current_day(session_factory):
    e = _engine(session_factory)
    _feed(e, _candles())
    day, runs = e.story.runs(KEY)
    assert day == DAY2 and runs
    assert all((r.start_ts + timedelta(hours=5, minutes=30)).date() == DAY2 for r in runs)
    assert any(r.is_open for r in runs) and any(not r.is_open for r in runs)
    subjects = {r.subject for r in runs}
    assert {"bb_width", "price_vs_vwap", "macd"} <= subjects


def test_the_rest_of_the_engine_is_unchanged_when_the_story_is_on(session_factory):
    off = _engine(session_factory, on=False)
    _feed(off, _candles())
    on = _engine(session_factory, on=True)
    _feed(on, _candles())
    strip = lambda rows: [{k: v for k, v in r.items()} for r in rows]  # noqa: E731
    assert strip(off._buffer) == strip(on._buffer)
    assert strip(off._indicator_buffer) == strip(on._indicator_buffer)


def test_flush_saves_what_memory_holds_without_duplicates(session_factory):
    e = _engine(session_factory)
    cs = _candles()
    _feed(e, cs[:-30])
    e.flush()
    assert _db_rows(session_factory) == _mem_rows(e)
    e.flush()  # nothing new: same rows, no duplicates
    assert _db_rows(session_factory) == _mem_rows(e)
    _feed(e, cs[-30:])  # more candles: runs close, open runs change
    e.flush()
    assert _db_rows(session_factory) == _mem_rows(e)


def test_restart_rebuilds_the_day_and_rewrites_it_once(session_factory):
    cs = _candles()
    first = _engine(session_factory)
    _feed(first, cs[:-20])
    first.flush()
    second = _engine(session_factory)  # a restart: recovery replays the day
    _feed(second, cs)
    second.flush()
    assert _db_rows(session_factory) == _mem_rows(second)


def test_eod_moves_the_day_to_the_archive_and_it_is_never_written_again(session_factory):
    e = _engine(session_factory)
    _feed(e, _candles())
    e.flush()
    n_today = len(_db_rows(session_factory))
    moved = archive_story(session_factory, e, datetime(2026, 10, 29, 10, 1, tzinfo=timezone.utc))
    assert moved == n_today > 0
    assert _db_rows(session_factory) == []
    arch = _db_rows(session_factory, MarketStoryArchive)
    assert len(arch) == n_today and not any(r[3] for r in arch)  # archived as closed
    e.flush()  # a late flush must not write the archived day back
    assert _db_rows(session_factory) == []
    assert archive_story(session_factory, None, datetime.now(timezone.utc)) == 0


def test_a_new_engine_skips_days_already_archived(session_factory):
    e = _engine(session_factory)
    _feed(e, _candles())
    e.flush()
    LibStory.archive_through(session_factory, DAY2)
    again = _engine(session_factory)
    _feed(again, _candles())
    again.flush()
    assert again.story.archived_through == DAY2
    assert _db_rows(session_factory) == []


def test_old_history_is_not_fed_to_the_story(session_factory):
    e = _engine(session_factory, today=date(2026, 11, 20))
    _feed(e, _candles())
    assert e.story.trackers == {}


def test_story_api(client, app, session_factory):
    r = client.get("/api/story/now?timeframe=5min")
    assert r.status_code == 200 and r.get_json()["enabled"] in (False, True)
    e = _engine(session_factory)
    _feed(e, _candles())
    e.flush()
    app.extensions["activity_engine"] = e
    now = client.get("/api/story/now?timeframe=5min").get_json()
    assert now["enabled"] and now["stocks"][0]["symbol"] == "X" and "macd" in now["stocks"][0]["setup"]
    live = client.get("/api/story/X?timeframe=5min").get_json()
    assert live["source"] == "live" and live["day"] == "2026-10-29" and len(live["runs"]) == len(_mem_rows(e))
    assert client.get("/api/story/now?timeframe=2min").status_code == 400
    assert client.get("/api/story/X?date=29-10-2026").status_code == 400
