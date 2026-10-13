"""Past days' indicator rows and patterns stored for the chart (2026-10-08,
step 4, feed/chart_history_fill.py): a reference engine run straight
through the same history is the expected result."""
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

from activity_engine import ActivityEngine
from db.models import CandleIndicators, InstrumentActivity
from db.ops import LibCandlesHistorical
from feed.candle_aggregator import CandleAggregator
from feed.chart_history_fill import fill_instrument
from feed.engine_recovery import today_start_utc
from tests.feed.test_engine_recovery import SEG, SYMBOL, _market_open, _register, _session_1min

IST = timedelta(hours=5, minutes=30)


def _history(now):
    rng = random.Random(11)
    today0 = today_start_utc(now)
    candles, price = [], 100.0
    for back in (4, 3, 2, 1):
        day, price = _session_1min(_market_open(today0 - timedelta(days=back)), 375, price, rng)
        candles += day
    return candles, today0


def _store(session_factory, candles):
    LibCandlesHistorical.persist_bulk(session_factory, [(SYMBOL, SEG, c) for c in candles])


def _ist_day(ts):
    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return (ts + IST).date()


def _reference(session_factory, candles, today0):
    """(timeframe, ts) of every indicator row and (timeframe, ts, activity)
    of every pattern a straight-through engine records."""
    engine = ActivityEngine(session_factory)
    agg = CandleAggregator(on_candle_closed=engine.on_candle_closed)
    for c in candles:
        agg.ingest_historical_1min(SYMBOL, SEG, c)
    agg.flush_all(as_of=today0)
    inds = {(r["timeframe"], r["ts"].replace(tzinfo=None)) for r in engine._indicator_buffer}
    acts = {(r["timeframe"], r["ts"].replace(tzinfo=None), r["activity"]) for r in engine._buffer}
    return inds, acts


def _stored(session_factory):
    with session_factory() as session:
        inds = {(r.timeframe, r.ts.replace(tzinfo=None)) for r in session.query(CandleIndicators).all()}
        acts = {(r.timeframe, r.ts.replace(tzinfo=None), r.activity) for r in session.query(InstrumentActivity).all()}
    return inds, acts


def test_fill_stores_every_past_day_like_a_straight_run(session_factory):
    _register(session_factory)
    now = datetime.now(timezone.utc)
    candles, today0 = _history(now)
    _store(session_factory, candles)

    summary = fill_instrument(SYMBOL, SEG, session_factory, now=now)

    ref_inds, ref_acts = _reference(session_factory, candles, today0)
    got_inds, got_acts = _stored(session_factory)
    assert got_inds == ref_inds
    assert got_acts == ref_acts
    assert len(summary["days_filled"]["1min"]) == 4
    # nothing for today
    assert all(ts < today0.replace(tzinfo=None) for _, ts in got_inds)


def test_fill_skips_days_already_stored_and_is_safe_to_rerun(session_factory):
    _register(session_factory)
    now = datetime.now(timezone.utc)
    candles, _ = _history(now)
    _store(session_factory, candles)

    fill_instrument(SYMBOL, SEG, session_factory, now=now)
    first = _stored(session_factory)
    again = fill_instrument(SYMBOL, SEG, session_factory, now=now)

    assert again["days_filled"] == {}
    assert _stored(session_factory) == first


def test_fill_completes_a_part_day(session_factory):
    _register(session_factory)
    now = datetime.now(timezone.utc)
    candles, today0 = _history(now)
    _store(session_factory, candles)
    fill_instrument(SYMBOL, SEG, session_factory, now=now)
    full = _stored(session_factory)

    # lose the afternoon of the most recent past day, as after a mid-session restart
    last_day = _ist_day(candles[-1].timestamp)
    cutoff = candles[-1].timestamp - timedelta(hours=3)
    with session_factory() as session:
        for model in (CandleIndicators, InstrumentActivity):
            for row in session.query(model).all():
                ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=timezone.utc)
                if _ist_day(ts) == last_day and ts >= cutoff:
                    session.delete(row)
        session.commit()

    summary = fill_instrument(SYMBOL, SEG, session_factory, now=now)
    assert last_day.isoformat() in summary["days_filled"]["1min"]
    assert _stored(session_factory) == full


def test_fill_does_nothing_for_an_unregistered_stock(session_factory):
    now = datetime.now(timezone.utc)
    candles, _ = _history(now)
    _store(session_factory, candles)
    assert fill_instrument(SYMBOL, SEG, session_factory, now=now)["days_filled"] == {}
    assert _stored(session_factory) == (set(), set())


def test_one_row_per_candle_per_timeframe(session_factory):
    _register(session_factory)
    now = datetime.now(timezone.utc)
    candles, _ = _history(now)
    _store(session_factory, candles)
    fill_instrument(SYMBOL, SEG, session_factory, now=now)
    with session_factory() as session:
        per_tf = Counter(r.timeframe for r in session.query(CandleIndicators).all())
    assert per_tf == {"1min": 4 * 375, "3min": 4 * 125, "5min": 4 * 75}


def test_fill_covers_the_last_20_sessions_only(session_factory):
    """ALGO-3: the chart shows the previous 20 sessions; older history only warms up."""
    _register(session_factory)
    now = datetime.now(timezone.utc)
    rng = random.Random(5)
    today0 = today_start_utc(now)
    candles, price = [], 100.0
    for back in range(25, 0, -1):
        day, price = _session_1min(_market_open(today0 - timedelta(days=back)), 60, price, rng)
        candles += day
    _store(session_factory, candles)

    summary = fill_instrument(SYMBOL, SEG, session_factory, now=now)

    expected = sorted({_ist_day(c.timestamp).isoformat() for c in candles})[-20:]
    assert summary["days_filled"]["1min"] == expected
    with session_factory() as session:
        stored_days = {_ist_day(r.ts) for r in session.query(CandleIndicators).filter_by(timeframe="1min")}
    assert sorted(d.isoformat() for d in stored_days) == expected
