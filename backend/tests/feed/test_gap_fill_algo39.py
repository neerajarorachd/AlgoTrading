"""ALGO-39: every missing candle range fetched in one call, weekends and NSE
holidays skipped, and a stock's day re-run through the engine after a hole
behind its last candle is filled (user's choice, 2026-11-02)."""
from datetime import date, datetime, time, timedelta, timezone

import market_calendar
from activity_engine import ActivityEngine
from db.models import CandleHistorical, InstrumentActivity
from feed.engine_recovery import fill_history_holes, rebuild_today, today_start_utc
from tests.feed.test_engine_recovery import (  # noqa: F401 -- `day` is a fixture
    SEG, SYMBOL, NoHistoryBroker, _acts, _inds, _register, _store, _warm, day,
)


class RecordingBroker:
    def __init__(self, candles=()):
        self.candles = list(candles)
        self.calls = []

    def get_historical_data(self, symbol, security_id, exchange_segment, timeframe, from_date, to_date):
        self.calls.append((from_date, to_date))
        return list(self.candles)


# ---- market_calendar ----

def test_weekends_and_listed_nse_holidays_are_closed():
    assert not market_calendar.is_trading_day(date(2026, 11, 7))   # Saturday
    assert not market_calendar.is_trading_day(date(2026, 11, 9))   # Diwali-Balipratipada
    assert market_calendar.is_trading_day(date(2026, 11, 10))
    assert not market_calendar.is_trading_day(date(2027, 1, 2))    # a 2027 weekend (file lists them)
    assert market_calendar.trading_days(date(2026, 11, 6), date(2026, 11, 10)) == [date(2026, 11, 6), date(2026, 11, 10)]


def test_a_session_has_375_minutes_from_0915_ist():
    mins = market_calendar.session_minutes(date(2026, 11, 2))
    assert len(mins) == 375
    assert mins[0] == datetime(2026, 11, 2, 3, 45, tzinfo=timezone.utc)
    assert mins[-1] == datetime(2026, 11, 2, 9, 59, tzinfo=timezone.utc)
    until = datetime(2026, 11, 2, 3, 50, 30, tzinfo=timezone.utc)  # 09:20:30 IST -> 09:15..09:19 closed
    stored = [datetime(2026, 11, 2, 3, 46, tzinfo=timezone.utc)]
    assert market_calendar.missing_minutes(date(2026, 11, 2), stored, until) == [
        datetime(2026, 11, 2, 3, m, tzinfo=timezone.utc) for m in (45, 47, 48, 49)]


# ---- earlier days ----

def _a_trading_history_day(day_fx):
    days = sorted({market_calendar.ist_date(c.timestamp) for c in day_fx["history"]})
    return next(d for d in days if market_calendar.is_trading_day(d))


def test_holes_in_earlier_days_are_fetched_in_one_call_and_only_once(session_factory, day):
    d = _a_trading_history_day(day)
    hole = {m for m in market_calendar.session_minutes(d)[60:180]}  # 10:15-12:14 IST missing
    kept = [c for c in day["history"] if c.timestamp not in hole]
    from db.ops import LibCandlesHistorical
    LibCandlesHistorical.persist_bulk(session_factory, [(SYMBOL, SEG, c) for c in kept])
    start = datetime.combine(d, time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    broker = RecordingBroker(day["history"])

    saved = fill_history_holes(session_factory, broker, SYMBOL, "2885", SEG, start, end)

    assert len(broker.calls) == 1 and broker.calls[0][0].date() == d
    assert saved == len(hole)
    with session_factory() as session:
        have = {r.ts.replace(tzinfo=timezone.utc) for r in session.query(CandleHistorical).filter_by(symbol=SYMBOL, timeframe="1min")}
    assert hole <= have
    # checked once per process: no second call
    fill_history_holes(session_factory, broker, SYMBOL, "2885", SEG, start, end)
    assert len(broker.calls) == 1


def test_a_closed_day_is_never_requested(session_factory, day, monkeypatch):
    d = _a_trading_history_day(day)
    monkeypatch.setattr(market_calendar, "is_trading_day", lambda x: False)
    start = datetime.combine(d, time(), tzinfo=timezone.utc)
    broker = RecordingBroker(day["history"])
    assert fill_history_holes(session_factory, broker, SYMBOL, "2885", SEG, start, start + timedelta(days=1)) == 0
    assert broker.calls == []


# ---- today's day re-run ----

def test_rerun_after_a_filled_hole_matches_a_session_that_never_missed_it(session_factory, day):
    """The live engine skipped one minute (a restart); gap fill saved it later.
    After rebuild_today its patterns and indicators equal an engine that saw
    every candle, and the DB holds each activity once."""
    _register(session_factory)
    _store(session_factory, day)  # the DB already has every candle (hole filled)
    skipped = day["today_closed"][40]

    live = ActivityEngine(session_factory)
    _warm(live, day["history"])
    for c in day["today_closed"]:
        if c is not skipped:
            live.on_candle_closed(SYMBOL, SEG, c)
    live.flush()

    reference = ActivityEngine(session_factory)
    _warm(reference, day["history"])
    for c in day["today_closed"]:
        reference.on_candle_closed(SYMBOL, SEG, c)

    summary = rebuild_today(SYMBOL, SEG, "2885", NoHistoryBroker(), session_factory, live, now=day["now"])
    assert not summary.get("skipped") and summary["replayed_recorded"] > 0

    assert _acts(live._buffer) == _acts(reference._buffer)
    assert _inds(live._indicator_buffer) == _inds(reference._indicator_buffer)
    live.flush()
    with session_factory() as session:
        stored = session.query(InstrumentActivity).filter(InstrumentActivity.ts >= today_start_utc(day["now"]).replace(tzinfo=None)).count()
    assert stored == len(reference._buffer)


def test_today_is_never_treated_as_an_earlier_day(session_factory, day):
    """Found live 2026-11-02: the startup check counted today (from 00:00 UTC)
    as an earlier day -- an extra call per stock and today's minutes saved as
    history."""
    today = market_calendar.ist_date(datetime.now(timezone.utc))
    start = datetime.combine(today, time(), tzinfo=timezone.utc)  # how ensure_warmup_history ends its window
    broker = RecordingBroker(day["today_1min"])
    assert fill_history_holes(session_factory, broker, SYMBOL, "2885", SEG, start - timedelta(days=1), start + timedelta(hours=6)) >= 0
    assert all(c[0].date() < today for c in broker.calls)


def test_the_rebuild_does_not_hold_the_live_engine_lock(session_factory, day, monkeypatch):
    """The scratch rebuild runs off the live lock; only the swap takes it.
    Another thread must be able to take the live lock while the scratch
    engine is warming up."""
    import threading
    import feed.engine_recovery as er
    _register(session_factory)
    _store(session_factory, day)
    live = ActivityEngine(session_factory)
    _warm(live, day["history"])
    seen = []
    real_prepare = er.prepare_instrument

    def spy_prepare(*a, **k):
        t = threading.Thread(target=lambda: seen.append(live._lock.acquire(timeout=2) and (live._lock.release() or True)))
        t.start(); t.join()
        return real_prepare(*a, **k)
    monkeypatch.setattr(er, "prepare_instrument", spy_prepare)
    rebuild_today(SYMBOL, SEG, "2885", NoHistoryBroker(), session_factory, live, now=day["now"])
    assert seen == [True]
