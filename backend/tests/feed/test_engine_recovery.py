"""Engine today-store + 15-min flush + restart recovery (2026-10-08,
live_pattern_store_and_grid_window_plan). The central guarantee: a session
that crashes mid-day and recovers ends up with exactly the same patterns and
indicator rows as one that never stopped."""
import random
from datetime import datetime, time, timedelta, timezone

import pytest

import feed.bootstrap as bootstrap
from activity_engine import ActivityEngine
from brokers.models import Candle
from db.models import CandleIndicators, InstrumentActivity, SubscribedSymbol
from db.ops import LibCandles, LibCandlesHistorical
from feed.candle_aggregator import CandleAggregator
from feed.engine_recovery import prepare_instrument, today_start_utc
from feed.gap_fill import backfill_missing_candles

SYMBOL, SEG = "RELIANCE", "NSE_EQ"
IST = timedelta(hours=5, minutes=30)


@pytest.fixture(autouse=True)
def _clear_scan_watermark_cache():
    """gap_fill keeps a module-level "scanned through" time per instrument;
    a value left by another test (or this one, set in the future) would make
    a backfill skip or start late."""
    import feed.gap_fill as gap_fill
    gap_fill._last_scanned_through.clear()
    yield
    gap_fill._last_scanned_through.clear()


class NoHistoryBroker:
    """The warm-up history is already stored; the broker has nothing new."""
    def __init__(self, candles=()):
        self.candles = list(candles)

    def get_historical_data(self, symbol, security_id, exchange_segment, timeframe, from_date, to_date):
        return list(self.candles)


def _register(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
                                     security_id="2885", previous_close=100.0))
        session.commit()


def _session_1min(day_open_utc, minutes, price, rng):
    out = []
    for i in range(minutes):
        o = price
        price = round(max(1.0, price + rng.gauss(0, 0.4)), 2)
        h = round(max(o, price) + rng.random() * 0.3, 2)
        l = round(min(o, price) - rng.random() * 0.3, 2)
        out.append(Candle(symbol=SYMBOL, timeframe="1min", timestamp=day_open_utc + timedelta(minutes=i),
                          open=o, high=h, low=l, close=price, volume=rng.randint(100, 5000)))
    return out, price


def _market_open(day_start_utc):
    return day_start_utc + timedelta(hours=9, minutes=15)


@pytest.fixture
def day():
    """Prior sessions (stored as history) + today's first 120 minutes, all
    rolled up to 1/3/5-min in live order."""
    rng = random.Random(7)
    now = datetime.now(timezone.utc)
    today0 = today_start_utc(now)
    history, price = [], 100.0
    for back in (3, 2, 1):
        candles, price = _session_1min(_market_open(today0 - timedelta(days=back)), 375, price, rng)
        history += candles
    today_1min, _ = _session_1min(_market_open(today0), 120, price, rng)
    today_closed = []
    agg = CandleAggregator(on_candle_closed=lambda s, g, c: today_closed.append(c))
    for c in today_1min:
        agg.ingest_historical_1min(SYMBOL, SEG, c)
    return {"now": _market_open(today0) + timedelta(minutes=125), "history": history,
            "today_1min": today_1min, "today_closed": today_closed}


def _store(session_factory, day):
    LibCandlesHistorical.persist_bulk(session_factory, [(SYMBOL, SEG, c) for c in day["history"]])
    LibCandles.persist_bulk(session_factory, [(SYMBOL, SEG, c) for c in day["today_closed"]])


def _warm(engine, history):
    agg = CandleAggregator(on_candle_closed=engine.warm_up)
    for c in history:
        agg.ingest_historical_1min(SYMBOL, SEG, c)
    agg.flush_all(as_of=history[-1].timestamp + timedelta(minutes=1))


def _acts(rows):
    return sorted((r["timeframe"], r["ts"].replace(tzinfo=None), r["activity"]) for r in rows)


def _inds(rows):  # at storage precision: Numeric(9,4) oscillators, Numeric(9,2) price levels
    return sorted((r["timeframe"], r["ts"].replace(tzinfo=None),
                   None if r["rsi"] is None else round(float(r["rsi"]), 4),
                   None if r["ema50"] is None else round(float(r["ema50"]), 2)) for r in rows)


def test_flush_keeps_todays_rows_in_memory_and_saves_only_the_new_tail(session_factory, day):
    _register(session_factory)
    engine = ActivityEngine(session_factory)
    for c in day["today_closed"][:200]:
        engine.on_candle_closed(SYMBOL, SEG, c)
    in_memory = len(engine._buffer)
    assert in_memory > 0 and engine.buffered_count() == in_memory

    assert engine.flush() == in_memory
    assert engine.buffered_count() == 0
    assert len(engine._buffer) == in_memory  # still serving today from memory

    for c in day["today_closed"][200:]:
        engine.on_candle_closed(SYMBOL, SEG, c)
    new = engine.buffered_count()
    assert engine.flush() == new
    assert engine.flush() == 0
    with session_factory() as session:
        assert session.query(InstrumentActivity).count() == len(engine._buffer)
        assert session.query(CandleIndicators).count() == len(engine._indicator_buffer)


def test_flush_drops_saved_rows_from_earlier_days_from_memory(session_factory, day):
    _register(session_factory)
    engine = ActivityEngine(session_factory)
    for c in day["history"][-375:]:  # yesterday
        engine.on_candle_closed(SYMBOL, SEG, c)
    assert engine.buffered_count() > 0
    engine.flush()
    assert engine._buffer == [] and engine._indicator_buffer == []
    with session_factory() as session:
        assert session.query(InstrumentActivity).count() > 0  # but they're in the DB


def test_warm_up_builds_state_without_recording_anything(session_factory, day):
    _register(session_factory)
    warmed, continuous = ActivityEngine(session_factory), ActivityEngine(session_factory)
    _warm(warmed, day["history"])
    assert warmed._buffer == [] and warmed._indicator_buffer == []
    assert warmed.has_state(SYMBOL, SEG)

    _warm(continuous, day["history"])
    for c in day["today_closed"]:
        warmed.on_candle_closed(SYMBOL, SEG, c)
    # a warmed engine's first recorded candle already has every long-period
    # indicator (EMA50/MA50 need 50 candles) -- no blank start to the day
    first = next(r for r in warmed._indicator_buffer if r["timeframe"] == "5min")
    assert first["ema50"] is not None and first["ma50"] is not None


def test_crash_and_recovery_matches_an_uninterrupted_session(session_factory, day):
    _register(session_factory)
    _store(session_factory, day)
    split = day["today_closed"][0].timestamp + timedelta(minutes=60)

    # reference: never stopped
    reference = ActivityEngine(session_factory)
    _warm(reference, day["history"])
    for c in day["today_closed"]:
        reference.on_candle_closed(SYMBOL, SEG, c)

    # crashed: ran the first hour, flushed (the 15-min timer), then died
    crashed = ActivityEngine(session_factory)
    _warm(crashed, day["history"])
    for c in day["today_closed"]:
        if c.timestamp < split:
            crashed.on_candle_closed(SYMBOL, SEG, c)
    crashed.flush()

    recovered = ActivityEngine(session_factory)
    summary = prepare_instrument(SYMBOL, SEG, "2885", NoHistoryBroker(), session_factory, recovered, now=day["now"])
    assert not summary["skipped"]
    assert summary["loaded_activities"] > 0 and summary["replayed_warm"] > 0 and summary["replayed_recorded"] > 0

    assert _acts(recovered._buffer) == _acts(reference._buffer)
    assert _inds(recovered._indicator_buffer) == _inds(reference._indicator_buffer)
    # only the part after the last save is unsaved -- nothing gets written twice
    assert recovered.buffered_count() == len(reference._buffer) - summary["loaded_activities"]


def test_recovery_is_skipped_when_the_engine_already_has_state(session_factory, day):
    _register(session_factory)
    _store(session_factory, day)
    engine = ActivityEngine(session_factory)
    engine.on_candle_closed(SYMBOL, SEG, day["today_closed"][0])
    summary = prepare_instrument(SYMBOL, SEG, "2885", NoHistoryBroker(), session_factory, engine, now=day["now"])
    assert summary["skipped"]


def test_stock_added_mid_session_gets_the_whole_day(session_factory, day, monkeypatch):
    """No candles stored for today yet: recovery warms up on history, then
    the backfill pushes 09:15..now through the engine -- patterns for the
    whole day, not only from the moment the stock was added."""
    import feed.gap_fill as gap_fill
    monkeypatch.setattr(gap_fill, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: day["now"])}))
    monkeypatch.setattr(gap_fill.ws_live, "broadcast_backfill_status", lambda *a, **k: None)
    _register(session_factory)
    LibCandlesHistorical.persist_bulk(session_factory, [(SYMBOL, SEG, c) for c in day["history"]])

    engine = ActivityEngine(session_factory)
    broker = NoHistoryBroker(day["today_1min"])
    prepare_instrument(SYMBOL, SEG, "2885", broker, session_factory, engine, now=day["now"])
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)
    backfill_missing_candles(SYMBOL, SEG, "2885", broker, session_factory, aggregator, engine)

    reference = ActivityEngine(session_factory)
    _warm(reference, day["history"])
    for c in day["today_closed"]:
        reference.on_candle_closed(SYMBOL, SEG, c)
    first_1min = min(r["ts"] for r in engine._indicator_buffer if r["timeframe"] == "1min")
    assert first_1min == day["today_1min"][0].timestamp  # starts at 09:15
    assert _acts(engine._buffer) == _acts([r for r in reference._buffer])


def test_periodic_flush_timer_starts_once_per_engine(session_factory, monkeypatch):
    started = []

    class FakeTimer:
        def __init__(self, interval, fn):
            started.append(interval)
            self.daemon = False

        def start(self):
            pass

    monkeypatch.setattr(bootstrap.threading, "Timer", FakeTimer)
    engine = ActivityEngine(session_factory)
    bootstrap._schedule_periodic_flush(engine)
    bootstrap._schedule_periodic_flush(engine)  # start_capture runs again next day
    assert started == [bootstrap.PERIODIC_FLUSH_SECONDS]
