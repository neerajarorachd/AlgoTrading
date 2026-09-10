from datetime import datetime, timezone

from db.models import CandleToday
from feed.candle_aggregator import CandleAggregator
from feed.candle_persistence import persist_candle

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
