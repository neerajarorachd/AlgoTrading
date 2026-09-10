from datetime import datetime, timezone

from brokers.models import Quote
from db.models import SubscribedSymbol
from feed.bootstrap import _hydrate
from market_feed import MarketFeed


class FakeBroker:
    def __init__(self, quote: Quote):
        self.quote = quote
        self.subscribed_calls = []

    def get_quote(self, symbol, security_id, exchange_segment):
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


def test_hydrate_with_no_active_rows_does_nothing(session_factory):
    broker = FakeBroker(Quote(symbol="X", ltp=1, open=1, high=1, low=1, close=1,
                               volume=0, timestamp=datetime.now(timezone.utc)))
    market_feed = MarketFeed(broker, on_tick=lambda payload: None)

    _hydrate(broker, market_feed, session_factory)

    assert broker.subscribed_calls == []
