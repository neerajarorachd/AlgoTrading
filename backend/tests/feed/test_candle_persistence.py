from datetime import datetime, timezone

import feed.candle_persistence as candle_persistence
from brokers.models import Candle
from db.models import CandleToday
from feed.candle_aggregator import CandleAggregator
from feed.candle_persistence import persist_candle, persist_candles_bulk

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"


def _ts(minute, second=5):
    return datetime(2026, 9, 10, 9, minute, second, tzinfo=timezone.utc)


def test_finalized_candle_persists_to_candles_today(session_factory):
    agg = CandleAggregator(on_candle_closed=lambda sym, seg, c: persist_candle(session_factory, sym, seg, c))

    agg.on_tick(SYMBOL, SEG, 100.0, 10, _ts(15))
    agg.on_tick(SYMBOL, SEG, 101.0, 5, _ts(16))  # finalizes the :15 1-min candle

    with session_factory() as session:
        row = session.query(CandleToday).filter_by(symbol=SYMBOL, timeframe="1min").one()
        assert float(row.open_price) == 100.0
        assert float(row.close_price) == 100.0
        assert row.volume == 10


def test_persist_candle_upserts_on_reconnect_replay(session_factory):
    agg = CandleAggregator(on_candle_closed=lambda sym, seg, c: persist_candle(session_factory, sym, seg, c))
    agg.on_tick(SYMBOL, SEG, 100.0, 10, _ts(15))
    agg.on_tick(SYMBOL, SEG, 101.0, 5, _ts(16))

    # simulate a reconnect re-finalizing the same boundary candle with updated data
    from brokers.models import Candle
    persist_candle(session_factory, SYMBOL, SEG, Candle(
        symbol=SYMBOL, timeframe="1min", timestamp=_ts(15, second=0),
        open=100.0, high=103.0, low=99.0, close=102.0, volume=25,
    ))

    with session_factory() as session:
        rows = session.query(CandleToday).filter_by(symbol=SYMBOL, timeframe="1min").all()
        assert len(rows) == 1  # updated in place, not duplicated
        assert float(rows[0].high_price) == 103.0
        assert rows[0].volume == 25


def _blind_insert_upsert(session, symbol, exchange_segment, candle):
    """Stand-in for _upsert that always INSERTs, never checking for an
    existing row first — reproduces what a real race looks like (two writers'
    SELECT both ran before either INSERT), deterministically instead of
    depending on real thread timing."""
    session.add(CandleToday(
        symbol=symbol, exchange_segment=exchange_segment,
        timeframe=candle.timeframe, ts=candle.timestamp,
        open_price=candle.open, high_price=candle.high,
        low_price=candle.low, close_price=candle.close, volume=candle.volume,
    ))


def test_persist_candle_recovers_from_a_concurrent_insert_race(session_factory, monkeypatch):
    """A live tick finalizing a boundary candle and a concurrent backfill/
    rescan writing the same candle can both see "no row yet" via their own
    session and both try to INSERT — the unique constraint catches the
    second writer, which must back off instead of raising."""
    candle = Candle(symbol=SYMBOL, timeframe="1min", timestamp=_ts(15, second=0),
                     open=100.0, high=101.0, low=99.0, close=100.5, volume=10)
    persist_candle(session_factory, SYMBOL, SEG, candle)  # the "other writer" gets there first

    monkeypatch.setattr(candle_persistence, "_upsert", _blind_insert_upsert)
    persist_candle(session_factory, SYMBOL, SEG, candle)  # must not raise

    with session_factory() as session:
        rows = session.query(CandleToday).filter_by(symbol=SYMBOL, timeframe="1min").all()
    assert len(rows) == 1  # the racing insert was dropped, not duplicated


def test_persist_candles_bulk_recovers_from_a_concurrent_insert_race(session_factory, monkeypatch):
    """Same race as above, but for the batched backfill path — one candle's
    conflict must not fail the rest of the batch."""
    conflicting = Candle(symbol=SYMBOL, timeframe="1min", timestamp=_ts(15, second=0),
                          open=100.0, high=101.0, low=99.0, close=100.5, volume=10)
    clean = Candle(symbol=SYMBOL, timeframe="1min", timestamp=_ts(16, second=0),
                   open=100.5, high=102.0, low=100.0, close=101.5, volume=8)
    persist_candle(session_factory, SYMBOL, SEG, conflicting)  # the "other writer" gets there first

    monkeypatch.setattr(candle_persistence, "_upsert", _blind_insert_upsert)
    persist_candles_bulk(session_factory, [(SYMBOL, SEG, conflicting), (SYMBOL, SEG, clean)])  # must not raise

    with session_factory() as session:
        rows = session.query(CandleToday).filter_by(symbol=SYMBOL, timeframe="1min").order_by(CandleToday.ts).all()
    assert len(rows) == 2  # the conflicting candle stayed as-is; the clean one still got written
    assert float(rows[0].open_price) == 100.0  # untouched, not overwritten by the racing insert
    assert rows[1].ts.replace(tzinfo=timezone.utc) == _ts(16, second=0)
