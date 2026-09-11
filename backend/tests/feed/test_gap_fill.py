from datetime import datetime, timedelta, timezone

from brokers.models import Candle
from db.models import CandleToday
from feed.candle_aggregator import CandleAggregator
from feed.gap_fill import _todays_market_open, backfill_missing_candles

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"
SECURITY_ID = "1333"


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


def test_backfill_fetches_from_last_known_candle_not_market_open(session_factory, monkeypatch):
    spy = BroadcastSpy(monkeypatch)
    last_ts = _todays_market_open() + timedelta(minutes=30)
    with session_factory() as session:
        session.add(CandleToday(
            symbol=SYMBOL, exchange_segment=SEG, timeframe="1min", ts=last_ts,
            open_price=100, high_price=101, low_price=99, close_price=100.5, volume=10,
        ))
        session.commit()

    rest_broker = FakeRestBroker([])
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    assert len(rest_broker.calls) == 1
    from_date = rest_broker.calls[0][4]
    assert from_date == last_ts.replace(tzinfo=timezone.utc)
    assert [e[1] for e in spy.events] == ["started", "done"]


def test_backfill_skips_broadcast_when_already_caught_up(session_factory, monkeypatch):
    spy = BroadcastSpy(monkeypatch)
    with session_factory() as session:
        session.add(CandleToday(
            symbol=SYMBOL, exchange_segment=SEG, timeframe="1min",
            ts=datetime.now(timezone.utc).replace(second=0, microsecond=0),
            open_price=100, high_price=101, low_price=99, close_price=100.5, volume=10,
        ))
        session.commit()

    rest_broker = FakeRestBroker([])
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    assert rest_broker.calls == []
    assert spy.events == []


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
