from datetime import datetime, timezone

from feed.candle_aggregator import CandleAggregator

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"


def _ts(minute, second=5):
    return datetime(2026, 9, 10, 9, minute, second, tzinfo=timezone.utc)


def _collector():
    events = []

    def on_candle_closed(symbol, exchange_segment, candle):
        events.append((symbol, exchange_segment, candle))

    return events, on_candle_closed


def test_single_instrument_1min_boundary():
    events, cb = _collector()
    agg = CandleAggregator(on_candle_closed=cb)

    agg.on_tick(SYMBOL, SEG, 100.0, 10, _ts(15))
    assert events == []  # first tick just opens the forming candle

    agg.on_tick(SYMBOL, SEG, 101.0, 3, _ts(15, second=40))
    assert events == []  # still same minute, updates forming candle in place

    agg.on_tick(SYMBOL, SEG, 99.0, 5, _ts(16))
    assert len(events) == 1
    symbol, seg, candle = events[0]
    assert (symbol, seg, candle.timeframe) == (SYMBOL, SEG, "1min")
    assert candle.timestamp == _ts(15, second=0)
    # the :16 tick (99.0) opens the *next* forming candle — it must not leak into
    # the :15 candle that just finalized
    assert (candle.open, candle.high, candle.low, candle.close) == (100.0, 101.0, 100.0, 101.0)
    assert candle.volume == 13


def test_3min_rollup_emitted_on_bucket_change():
    events, cb = _collector()
    agg = CandleAggregator(on_candle_closed=cb)

    ticks = [(15, 100.0, 10), (16, 101.0, 5), (17, 99.0, 7), (18, 102.0, 3), (19, 103.0, 2)]
    for minute, ltp, vol in ticks:
        agg.on_tick(SYMBOL, SEG, ltp, vol, _ts(minute))

    threemin = [c for _, _, c in events if c.timeframe == "3min"]
    assert len(threemin) == 1
    candle = threemin[0]
    assert candle.timestamp == _ts(15, second=0)
    assert (candle.open, candle.high, candle.low, candle.close) == (100.0, 101.0, 99.0, 99.0)
    assert candle.volume == 22  # 10 + 5 + 7, from the :15/:16/:17 1-min candles only

    onemin = [c for _, _, c in events if c.timeframe == "1min"]
    assert len(onemin) == 4  # :15, :16, :17, :18 finalized; :19 still forming


def test_5min_rollup_emitted_on_bucket_change():
    events, cb = _collector()
    agg = CandleAggregator(on_candle_closed=cb)

    for minute in range(10, 17):  # crosses the :10-:14 -> :15 5-min boundary
        agg.on_tick(SYMBOL, SEG, float(100 + minute), 1, _ts(minute))

    fivemin = [c for _, _, c in events if c.timeframe == "5min"]
    assert len(fivemin) == 1
    candle = fivemin[0]
    assert candle.timestamp == _ts(10, second=0)
    assert candle.volume == 5  # :10..:14 -> five 1-min candles


def test_out_of_order_tick_is_dropped_not_crashed():
    events, cb = _collector()
    agg = CandleAggregator(on_candle_closed=cb)

    agg.on_tick(SYMBOL, SEG, 100.0, 1, _ts(20))
    agg.on_tick(SYMBOL, SEG, 90.0, 1, _ts(19))  # earlier bucket than the forming one
    assert agg.dropped_late_ticks == 1

    agg.on_tick(SYMBOL, SEG, 101.0, 1, _ts(21))
    assert len(events) == 1
    candle = events[0][2]
    # the dropped :19 tick must not have polluted the :20 forming candle's OHLC
    assert (candle.open, candle.high, candle.low, candle.close) == (100.0, 100.0, 100.0, 100.0)


def test_late_subscribe_mid_bucket_emits_correct_partial_rollup():
    events, cb = _collector()
    agg = CandleAggregator(on_candle_closed=cb)

    # first tick ever for this instrument arrives mid 3-min-bucket (:17, not :15)
    agg.on_tick(SYMBOL, SEG, 100.0, 4, _ts(17))
    agg.on_tick(SYMBOL, SEG, 105.0, 2, _ts(18))  # finalizes :17 1min; :18 is still the same 3min bucket's first entry
    agg.on_tick(SYMBOL, SEG, 106.0, 1, _ts(19))  # finalizes :18 1min -> 3min bucket advances 15->18, emits the partial

    threemin = [c for _, _, c in events if c.timeframe == "3min"]
    assert len(threemin) == 1
    candle = threemin[0]
    # the partial bucket only ever contained the single :17 1-min candle
    assert candle.timestamp == _ts(17, second=0)
    assert (candle.open, candle.high, candle.low, candle.close) == (100.0, 100.0, 100.0, 100.0)
    assert candle.volume == 4


def test_flush_all_force_closes_forming_candles_at_every_timeframe():
    events, cb = _collector()
    agg = CandleAggregator(on_candle_closed=cb)

    agg.on_tick(SYMBOL, SEG, 100.0, 1, _ts(15))
    agg.on_tick(SYMBOL, SEG, 101.0, 1, _ts(16))  # finalizes :15 1min, opens :16
    assert events == [] or all(c.timeframe == "1min" for _, _, c in events)

    agg.flush_all(as_of=_ts(17))

    onemin = [c for _, _, c in events if c.timeframe == "1min"]
    threemin = [c for _, _, c in events if c.timeframe == "3min"]
    assert len(onemin) == 2  # :15 (from the boundary crossing) and :16 (force-closed by flush)
    assert len(threemin) == 1  # the still-partial :15 3-min bucket, force-closed too
    assert threemin[0].volume == 2


def test_get_last_closed_1min_is_none_before_any_candle_closes():
    agg = CandleAggregator(on_candle_closed=lambda *a: None)

    assert agg.get_last_closed_1min(SYMBOL, SEG) is None

    agg.on_tick(SYMBOL, SEG, 100.0, 1, _ts(15))
    assert agg.get_last_closed_1min(SYMBOL, SEG) is None  # still forming, not closed yet


def test_get_last_closed_1min_updates_as_candles_finalize():
    agg = CandleAggregator(on_candle_closed=lambda *a: None)

    agg.on_tick(SYMBOL, SEG, 100.0, 1, _ts(15))
    agg.on_tick(SYMBOL, SEG, 105.0, 1, _ts(16))  # finalizes :15

    last = agg.get_last_closed_1min(SYMBOL, SEG)
    assert last is not None
    assert last.timestamp == _ts(15, second=0)
    assert last.close == 100.0

    agg.on_tick(SYMBOL, SEG, 110.0, 1, _ts(17))  # finalizes :16
    last = agg.get_last_closed_1min(SYMBOL, SEG)
    assert last.timestamp == _ts(16, second=0)
    assert last.close == 105.0


def test_ingest_historical_1min_produces_correct_rollups_and_persistence_events():
    from brokers.models import Candle

    events, cb = _collector()
    agg = CandleAggregator(on_candle_closed=cb)

    for minute, close in [(15, 100.0), (16, 101.0), (17, 99.0)]:
        agg.ingest_historical_1min(SYMBOL, SEG, Candle(
            symbol=SYMBOL, timeframe="1min", timestamp=_ts(minute, second=0),
            open=close, high=close, low=close, close=close, volume=10,
        ))

    onemin = [c for _, _, c in events if c.timeframe == "1min"]
    assert len(onemin) == 3
    assert [c.timestamp for c in onemin] == [_ts(15, 0), _ts(16, 0), _ts(17, 0)]

    # 3-min bucket :15-:17 is still partial (no later candle triggered its emit) —
    # matches live behavior exactly, no special-casing for historical data
    threemin = [c for _, _, c in events if c.timeframe == "3min"]
    assert threemin == []


def test_ingest_historical_1min_updates_last_closed_cache():
    from brokers.models import Candle

    agg = CandleAggregator(on_candle_closed=lambda *a: None)
    agg.ingest_historical_1min(SYMBOL, SEG, Candle(
        symbol=SYMBOL, timeframe="1min", timestamp=_ts(15, second=0),
        open=100.0, high=101.0, low=99.0, close=100.5, volume=10,
    ))

    last = agg.get_last_closed_1min(SYMBOL, SEG)
    assert last is not None
    assert last.close == 100.5


def test_backfilled_history_then_live_ticks_continue_seamlessly():
    """A live tick arriving right after backfill must correctly detect whether it's
    still in the same 1-min bucket as the last backfilled candle, or starts a new one."""
    from brokers.models import Candle

    events, cb = _collector()
    agg = CandleAggregator(on_candle_closed=cb)

    agg.ingest_historical_1min(SYMBOL, SEG, Candle(
        symbol=SYMBOL, timeframe="1min", timestamp=_ts(15, second=0),
        open=100.0, high=100.0, low=100.0, close=100.0, volume=10,
    ))

    # a live tick in the next minute must finalize a *new* forming candle, not
    # collide with the backfilled one
    agg.on_tick(SYMBOL, SEG, 102.0, 5, _ts(16))
    assert agg._forming[(SYMBOL, SEG, "1min")].open == 102.0

    agg.on_tick(SYMBOL, SEG, 103.0, 5, _ts(17))  # finalizes the live :16 candle
    onemin = [c for _, _, c in events if c.timeframe == "1min"]
    # both the backfilled :15 candle and the live-tick-driven :16 candle closed
    assert [c.timestamp for c in onemin] == [_ts(15, second=0), _ts(16, second=0)]
    assert onemin[-1].close == 102.0
