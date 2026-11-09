from datetime import datetime, timedelta, timezone

import pytest

import feed.gap_fill as gap_fill
from brokers.models import Candle
from db.models import CandleToday, SubscribedSymbol
from feed.candle_aggregator import CandleAggregator
from feed.gap_fill import _todays_market_open, backfill_missing_candles, scan_for_gaps

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"
SECURITY_ID = "1333"


@pytest.fixture(autouse=True)
def _clear_scan_watermark_cache():
    """_last_scanned_through/_in_progress are module-level, in-memory,
    process-lifetime state (see backfill_missing_candles) — reset them around
    every test so one test's calls can't leak into another's expectations."""
    gap_fill._last_scanned_through.clear()
    gap_fill._in_progress.clear()
    yield
    gap_fill._last_scanned_through.clear()
    gap_fill._in_progress.clear()


class FakeRestBroker:
    def __init__(self, candles):
        self.candles = candles
        self.calls = []

    def get_historical_data(self, symbol, security_id, exchange_segment, timeframe, from_date, to_date):
        self.calls.append((symbol, security_id, exchange_segment, timeframe, from_date, to_date))
        return self.candles


class BroadcastSpy:
    def __init__(self, monkeypatch):
        self.events = []
        monkeypatch.setattr(
            "feed.gap_fill.ws_live.broadcast_backfill_status",
            lambda symbol, exchange_segment, status, message: self.events.append((symbol, status, message)),
        )


def test_backfill_fetches_from_market_open_when_no_existing_candles(session_factory, monkeypatch):
    spy = BroadcastSpy(monkeypatch)
    candles = [Candle(symbol=SYMBOL, timeframe="1min", timestamp=_todays_market_open(),
                       open=100, high=101, low=99, close=100.5, volume=10)]
    rest_broker = FakeRestBroker(candles)
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    assert len(rest_broker.calls) == 1
    _, _, _, timeframe, from_date, _ = rest_broker.calls[0]
    assert timeframe == "1min"
    assert from_date == _todays_market_open()
    assert [e[1] for e in spy.events] == ["started", "done"]
    assert aggregator.get_last_closed_1min(SYMBOL, SEG).close == 100.5


def test_backfill_persists_1min_and_rollup_candles_via_one_bulk_write(session_factory, monkeypatch):
    # three consecutive 1-min candles spanning one 3-min boundary — closing the
    # third should also emit (and persist) one 3-min rollup candle
    spy = BroadcastSpy(monkeypatch)
    base = _todays_market_open()
    candles = [
        Candle(symbol=SYMBOL, timeframe="1min", timestamp=base + timedelta(minutes=i),
               open=100 + i, high=101 + i, low=99 + i, close=100.5 + i, volume=10)
        for i in range(4)  # the 4th candle's arrival closes the first 3-min bucket
    ]
    rest_broker = FakeRestBroker(candles)
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    assert [e[1] for e in spy.events] == ["started", "done"]
    with session_factory() as session:
        one_min_rows = session.query(CandleToday).filter_by(symbol=SYMBOL, timeframe="1min").all()
        three_min_rows = session.query(CandleToday).filter_by(symbol=SYMBOL, timeframe="3min").all()
    assert len(one_min_rows) == 4
    assert len(three_min_rows) == 1
    assert float(three_min_rows[0].open_price) == 100.0
    assert float(three_min_rows[0].close_price) == 102.5  # close of the 3rd (last) member candle


def test_backfill_skips_when_already_in_progress_elsewhere(session_factory, monkeypatch):
    # simulates the periodic scanner's tick landing on a symbol whose startup
    # backfill (or another scan cycle) hasn't finished yet — the second caller
    # must not duplicate the work against an already-throttled connection
    spy = BroadcastSpy(monkeypatch)
    gap_fill._in_progress.add((SYMBOL, SEG))
    rest_broker = FakeRestBroker([])
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    assert rest_broker.calls == []
    assert spy.events == []


def test_backfill_clears_in_progress_flag_after_completing(session_factory, monkeypatch):
    spy = BroadcastSpy(monkeypatch)
    rest_broker = FakeRestBroker([])
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    assert (SYMBOL, SEG) not in gap_fill._in_progress
    assert [e[1] for e in spy.events] == ["started", "done"]


def test_backfill_broadcasts_failed_on_broker_error(session_factory, monkeypatch):
    spy = BroadcastSpy(monkeypatch)

    class FailingBroker:
        def get_historical_data(self, *a, **kw):
            raise RuntimeError("Dhan API down")

    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, FailingBroker(), session_factory, aggregator)

    statuses = [e[1] for e in spy.events]
    assert statuses == ["started", "failed"]
    assert "Dhan API down" in spy.events[-1][2]


def test_backfill_never_raises_even_if_session_factory_is_broken(monkeypatch):
    """The whole function must be exception-safe end to end — it runs on an
    unsupervised background thread via spawn_backfill()."""
    spy = BroadcastSpy(monkeypatch)

    def broken_session_factory():
        raise RuntimeError("DB unavailable")

    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, FakeRestBroker([]), broken_session_factory, aggregator)

    assert spy.events[-1][1] == "failed"


def _add_subscribed_symbol(session_factory, symbol, security_id, active=True):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id=security_id, previous_close=100.0, active=active,
        ))
        session.commit()


def test_scan_for_gaps_backfills_every_active_symbol(session_factory, monkeypatch):
    spy = BroadcastSpy(monkeypatch)
    _add_subscribed_symbol(session_factory, "RELIANCE", "1333")
    _add_subscribed_symbol(session_factory, "TCS", "11536")
    _add_subscribed_symbol(session_factory, "DELISTED", "9999", active=False)

    candles = [Candle(symbol="x", timeframe="1min", timestamp=_todays_market_open(),
                       open=100, high=101, low=99, close=100.5, volume=10)]
    rest_broker = FakeRestBroker(candles)
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    scan_for_gaps(rest_broker, session_factory, aggregator, now=_todays_market_open() + timedelta(hours=1))

    called_symbols = {call[0] for call in rest_broker.calls}
    assert called_symbols == {"RELIANCE", "TCS"}  # inactive symbol skipped
    assert [e[1] for e in spy.events] == ["started", "done", "started", "done"]


def test_scan_for_gaps_skips_entirely_outside_market_hours(session_factory, monkeypatch):
    spy = BroadcastSpy(monkeypatch)
    _add_subscribed_symbol(session_factory, "RELIANCE", "1333")
    rest_broker = FakeRestBroker([])
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    outside_hours = _todays_market_open() - timedelta(hours=2)
    scan_for_gaps(rest_broker, session_factory, aggregator, now=outside_hours)

    assert rest_broker.calls == []
    assert spy.events == []


def test_backfill_ignores_a_stale_prior_day_candle_and_starts_from_todays_open(session_factory, monkeypatch):
    # candles_today has no EOD archiver yet — a previous day's last candle can
    # still be sitting in the table. It must not be mistaken for "today's last
    # known candle," or the backfill range would be wrong (or missed entirely).
    spy = BroadcastSpy(monkeypatch)
    yesterdays_close = _todays_market_open() - timedelta(hours=20)
    with session_factory() as session:
        session.add(CandleToday(
            symbol=SYMBOL, exchange_segment=SEG, timeframe="1min", ts=yesterdays_close,
            open_price=100, high_price=101, low_price=99, close_price=100.5, volume=10,
        ))
        session.commit()

    rest_broker = FakeRestBroker([])
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    assert len(rest_broker.calls) == 1
    from_date = rest_broker.calls[0][4]
    assert from_date == _todays_market_open()  # not yesterdays_close
    assert [e[1] for e in spy.events] == ["started", "done"]


def test_backfill_discards_a_stale_watermark_from_a_previous_day(session_factory, monkeypatch):
    spy = BroadcastSpy(monkeypatch)
    stale_watermark = _todays_market_open() - timedelta(hours=20)  # yesterday
    gap_fill._last_scanned_through[(SYMBOL, SEG)] = stale_watermark
    rest_broker = FakeRestBroker([])
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    assert len(rest_broker.calls) == 1
    from_date = rest_broker.calls[0][4]
    assert from_date == _todays_market_open()  # not the stale watermark
    assert [e[1] for e in spy.events] == ["started", "done"]


def _store_minutes(session_factory, minutes):
    with session_factory() as session:
        for ts in minutes:
            session.add(CandleToday(
                symbol=SYMBOL, exchange_segment=SEG, timeframe="1min", ts=ts,
                open_price=100, high_price=101, low_price=99, close_price=100.5, volume=10,
            ))
        session.commit()


def _closed_minutes_today():
    now_minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    open_ = _todays_market_open()
    last = min(now_minute, open_ + timedelta(minutes=375))
    return [open_ + timedelta(minutes=i) for i in range(int((last - open_).total_seconds() // 60))]


def test_backfill_fetches_from_the_first_missing_minute(session_factory, monkeypatch):
    # ALGO-39: 09:15-09:45 all stored -> the first missing minute is 09:46
    spy = BroadcastSpy(monkeypatch)
    open_ = _todays_market_open()
    _store_minutes(session_factory, [open_ + timedelta(minutes=i) for i in range(31)])

    rest_broker = FakeRestBroker([])
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    assert len(rest_broker.calls) == 1
    assert rest_broker.calls[0][4] == open_ + timedelta(minutes=31)
    assert [e[1] for e in spy.events] == ["started", "done"]


def test_backfill_fills_holes_behind_the_last_candle_in_the_same_call(session_factory, monkeypatch):
    # ALGO-39 (found live 2026-11-02: BHEL 11:04 / RELIANCE 11:07 missing
    # after a restart, never re-fetched): one stored minute at 09:45 means
    # 09:15-09:44 are holes. One call (Dhan returns the whole day) fills them
    # AND the minutes ahead; the ahead part goes through the aggregator, the
    # holes are saved directly; never the still-forming minute.
    spy = BroadcastSpy(monkeypatch)
    open_ = _todays_market_open()
    last_ts = open_ + timedelta(minutes=30)
    _store_minutes(session_factory, [last_ts])

    now_minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    whole_day = [
        Candle(symbol=SYMBOL, timeframe="1min", timestamp=open_ + timedelta(minutes=i),
               open=100, high=101, low=99, close=100.5, volume=10)
        for i in range(int((now_minute - open_).total_seconds() // 60) + 1)  # includes the forming minute
    ]
    ingested = []
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)
    original = aggregator.ingest_historical_1min
    monkeypatch.setattr(aggregator, "ingest_historical_1min",
                        lambda sym, seg, c, on_candle_closed=None: (ingested.append(c.timestamp), original(sym, seg, c, on_candle_closed)))
    broker = FakeRestBroker(whole_day)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, broker, session_factory, aggregator)

    assert len(broker.calls) == 1 and broker.calls[0][4] == open_
    assert ingested and min(ingested) == last_ts + timedelta(minutes=1)
    assert max(ingested) < now_minute  # never the still-forming minute
    with session_factory() as session:
        stored = {r.ts.replace(tzinfo=timezone.utc) for r in session.query(CandleToday).filter_by(symbol=SYMBOL, timeframe="1min")}
    assert all(open_ + timedelta(minutes=i) in stored for i in range(30))  # the holes
    holes_and_ahead = 30 + len(ingested)
    assert spy.events[-1] == (SYMBOL, "done", f"Backfilled {holes_and_ahead} candles")


def test_backfill_rebuilds_the_3_and_5_min_candles_of_a_filled_hole(session_factory, monkeypatch):
    BroadcastSpy(monkeypatch)
    open_ = _todays_market_open()
    minutes = [open_ + timedelta(minutes=i) for i in range(11)]
    _store_minutes(session_factory, [m for m in minutes if m != open_ + timedelta(minutes=1)])  # 09:16 missing
    with session_factory() as session:  # the 3-min candle the live feed built without 09:16
        session.add(CandleToday(symbol=SYMBOL, exchange_segment=SEG, timeframe="3min", ts=open_,
                                open_price=100, high_price=101, low_price=99, close_price=100.5, volume=20))
        session.commit()
    hole = Candle(symbol=SYMBOL, timeframe="1min", timestamp=open_ + timedelta(minutes=1),
                  open=100, high=150, low=99, close=100.5, volume=10)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, FakeRestBroker([hole]), session_factory,
                             CandleAggregator(on_candle_closed=lambda *a: None))

    with session_factory() as session:
        three = session.query(CandleToday).filter_by(symbol=SYMBOL, timeframe="3min", ts=open_).one()
        five = session.query(CandleToday).filter_by(symbol=SYMBOL, timeframe="5min", ts=open_).one()
    assert float(three.high_price) == 150 and three.volume == 30
    assert float(five.high_price) == 150 and five.volume == 50


def test_backfill_skips_broadcast_when_already_caught_up(session_factory, monkeypatch):
    spy = BroadcastSpy(monkeypatch)
    _store_minutes(session_factory, _closed_minutes_today())

    rest_broker = FakeRestBroker([])
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    assert rest_broker.calls == []
    assert spy.events == []


def test_backfill_does_not_reask_for_minutes_the_broker_does_not_have(session_factory, monkeypatch):
    # An illiquid symbol (no trades in a minute) or Dhan's missing 15:15-15:29:
    # once a minute is 10+ minutes old and the broker didn't return it, the
    # 5-minute scanner must not ask for it again.
    BroadcastSpy(monkeypatch)
    rest_broker = FakeRestBroker([])  # broker never has anything to return
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)
    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    now_minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    settled = now_minute - timedelta(minutes=gap_fill._ABSENT_AFTER_MINUTES)
    assert len(rest_broker.calls) >= 1
    if len(rest_broker.calls) == 2:  # only the newest, not yet settled minutes are asked again
        assert rest_broker.calls[1][4] >= settled


def test_backfill_never_asks_on_a_closed_day(session_factory, monkeypatch):
    BroadcastSpy(monkeypatch)
    monkeypatch.setattr(gap_fill.market_calendar, "is_trading_day", lambda d: False)
    rest_broker = FakeRestBroker([])

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory,
                             CandleAggregator(on_candle_closed=lambda *a: None))

    assert rest_broker.calls == []
