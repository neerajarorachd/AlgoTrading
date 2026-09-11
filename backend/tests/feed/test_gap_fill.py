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
    """_last_scanned_through is module-level, in-memory, process-lifetime state
    (see backfill_missing_candles) — reset it around every test so one test's
    calls can't leak into another's expectations."""
    gap_fill._last_scanned_through.clear()
    yield
    gap_fill._last_scanned_through.clear()


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


def test_backfill_does_not_reask_for_an_already_checked_empty_range(session_factory, monkeypatch):
    # An illiquid symbol (or any broker response with no candles for a range)
    # must not be re-fetched every cycle just because no CandleToday rows ever
    # got written for it — the watermark should advance regardless. Seed the
    # watermark directly (rather than relying on two real calls landing in
    # different wall-clock minutes, which the minute-truncated `now()` can't
    # guarantee inside a single fast test) to keep this deterministic.
    spy = BroadcastSpy(monkeypatch)
    already_checked_through = _todays_market_open() + timedelta(minutes=10)
    gap_fill._last_scanned_through[(SYMBOL, SEG)] = already_checked_through
    rest_broker = FakeRestBroker([])  # broker never has anything to return
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    backfill_missing_candles(SYMBOL, SEG, SECURITY_ID, rest_broker, session_factory, aggregator)

    assert len(rest_broker.calls) == 1
    from_date = rest_broker.calls[0][4]
    assert from_date == already_checked_through  # not market open again
    assert [e[1] for e in spy.events] == ["started", "done"]
    # the watermark itself must have advanced past the checked point
    assert gap_fill._last_scanned_through[(SYMBOL, SEG)] > already_checked_through


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
