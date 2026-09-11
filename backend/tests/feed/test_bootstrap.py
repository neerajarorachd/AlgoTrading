import threading
import time
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from brokers.models import BrokerAPIError, Quote
from db.models import Base, SubscribedSymbol
from db.session import build_session_factory
from feed.bootstrap import _hydrate
from feed.candle_aggregator import CandleAggregator
from market_feed import MarketFeed


class FakeBroker:
    def __init__(self, quote: Quote, fail_for_security_ids=frozenset()):
        self.quote = quote
        self.fail_for_security_ids = fail_for_security_ids
        self.subscribed_calls = []

    def get_quote(self, symbol, security_id, exchange_segment):
        if security_id in self.fail_for_security_ids:
            raise BrokerAPIError(f"simulated failure for {security_id}", status_code=429)
        return self.quote

    def subscribe_feed(self, instruments, on_tick):
        self.subscribed_calls.append(instruments)

    def unsubscribe_feed(self, instruments):
        pass


def test_hydrate_subscribes_active_rows_with_fresh_previous_close(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol="RELIANCE", exchange="NSE", segment="EQUITY",
            exchange_segment="NSE_EQ", security_id="2885", previous_close=1200.0,  # stale
        ))
        session.commit()

    fresh_quote = Quote(
        symbol="RELIANCE", ltp=1274.0, open=1278.0, high=1285.0, low=1266.0,
        close=1274.0, volume=9296079, timestamp=datetime.now(timezone.utc),
    )
    broker = FakeBroker(fresh_quote)
    market_feed = MarketFeed(broker, on_tick=lambda payload: None)

    _hydrate(broker, market_feed, session_factory)

    assert len(broker.subscribed_calls) == 1
    instrument = broker.subscribed_calls[0][0]
    assert instrument["security_id"] == "2885"
    assert instrument["previous_close"] == 1274.0  # refreshed from the live quote, not the stale DB value

    with session_factory() as session:
        row = session.query(SubscribedSymbol).filter_by(symbol="RELIANCE").one()
        assert float(row.previous_close) == 1274.0


def test_hydrate_skips_inactive_rows(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol="TCS", exchange="NSE", segment="EQUITY",
            exchange_segment="NSE_EQ", security_id="11536", active=False,
        ))
        session.commit()

    broker = FakeBroker(Quote(symbol="TCS", ltp=1, open=1, high=1, low=1, close=1,
                               volume=0, timestamp=datetime.now(timezone.utc)))
    market_feed = MarketFeed(broker, on_tick=lambda payload: None)

    _hydrate(broker, market_feed, session_factory)

    assert broker.subscribed_calls == []


def test_hydrate_skips_a_row_whose_broker_call_fails_and_continues(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol="RATE-LIMITED", exchange="NSE", segment="EQUITY",
            exchange_segment="NSE_EQ", security_id="111",
        ))
        session.add(SubscribedSymbol(
            symbol="RELIANCE", exchange="NSE", segment="EQUITY",
            exchange_segment="NSE_EQ", security_id="2885",
        ))
        session.commit()

    quote = Quote(symbol="RELIANCE", ltp=1274.0, open=1278.0, high=1285.0, low=1266.0,
                   close=1274.0, volume=9296079, timestamp=datetime.now(timezone.utc))
    broker = FakeBroker(quote, fail_for_security_ids={"111"})
    market_feed = MarketFeed(broker, on_tick=lambda payload: None)

    _hydrate(broker, market_feed, session_factory)  # must not raise

    # the failing row was skipped, but the healthy one still got subscribed
    assert len(broker.subscribed_calls) == 1
    assert broker.subscribed_calls[0][0]["security_id"] == "2885"


def test_hydrate_with_no_active_rows_does_nothing(session_factory):
    broker = FakeBroker(Quote(symbol="X", ltp=1, open=1, high=1, low=1, close=1,
                               volume=0, timestamp=datetime.now(timezone.utc)))
    market_feed = MarketFeed(broker, on_tick=lambda payload: None)

    _hydrate(broker, market_feed, session_factory)

    assert broker.subscribed_calls == []


class FakeBrokerWithHistory(FakeBroker):
    """Extends FakeBroker with get_historical_data, tracking which thread each
    call ran on — used to prove hydration's backfill is sequential (one
    background thread total) rather than one thread per symbol, which was
    starving the DB connection pool under a dozen-plus registered symbols."""

    def __init__(self, quote: Quote):
        super().__init__(quote)
        self.history_calls = []
        self.thread_idents = set()

    def get_historical_data(self, symbol, security_id, exchange_segment, timeframe, from_date, to_date):
        self.history_calls.append(symbol)
        self.thread_idents.add(threading.get_ident())
        return []


def test_hydrate_backfills_every_symbol_on_one_sequential_background_thread():
    # a real cross-thread-safe engine, not the shared session_factory fixture —
    # plain in-memory SQLite is pinned to a single thread (SingletonThreadPool),
    # which this test's background worker thread would otherwise hit; real
    # production uses SQL Server, which has no such restriction.
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        for symbol, security_id in [("RELIANCE", "2885"), ("TCS", "11536"), ("BHEL", "438")]:
            session.add(SubscribedSymbol(
                symbol=symbol, exchange="NSE", segment="EQUITY",
                exchange_segment="NSE_EQ", security_id=security_id,
            ))
        session.commit()

    quote = Quote(symbol="x", ltp=100, open=100, high=100, low=100, close=100,
                   volume=0, timestamp=datetime.now(timezone.utc))
    broker = FakeBrokerWithHistory(quote)
    market_feed = MarketFeed(broker, on_tick=lambda payload: None)
    aggregator = CandleAggregator(on_candle_closed=lambda *a: None)

    _hydrate(broker, market_feed, session_factory, aggregator=aggregator)

    deadline = time.monotonic() + 2.0
    while len(broker.history_calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)

    assert sorted(broker.history_calls) == ["BHEL", "RELIANCE", "TCS"]
    # every get_historical_data call landed on the same background thread,
    # and it's not the test's own thread — proves it's one sequential worker,
    # not spawn_backfill's one-thread-per-symbol
    assert len(broker.thread_idents) == 1
    assert threading.get_ident() not in broker.thread_idents
