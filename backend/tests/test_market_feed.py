from market_feed import MarketFeed


class FakeBroker:
    def __init__(self):
        self.connected = False
        self.subscribed = None
        self.callback = None

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def subscribe_feed(self, instruments, on_tick):
        self.subscribed = instruments
        self.callback = on_tick

    def unsubscribe_feed(self, instruments):
        self.subscribed = None


def test_market_feed_normalizes_a_broker_tick():
    broker = FakeBroker()
    received = []
    feed = MarketFeed(broker, received.append)
    instrument = {
        "security_id": "1333",
        "exchange_segment": "NSE_EQ",
        "symbol": "RELIANCE",
        "previous_close": 2828.4,
    }

    feed.start()
    feed.subscribe(instrument)
    broker.callback({"SecurityId": "1333", "ExchangeSegment": "NSE_EQ", "LTP": 2854.65,
                    "Timestamp": "2026-09-10T09:16:02Z"})

    assert broker.connected
    assert received[0]["symbol"] == "RELIANCE"
    assert received[0]["exchange"] == "NSE"
    assert received[0]["ltp"] == 2854.65
    assert received[0]["previous_close"] == 2828.4
    assert received[0]["absolute_change"] == 26.25
    assert received[0]["percentage_change"] == 0.93
    assert received[0]["direction"] == "up"


def test_market_feed_ignores_unknown_ticks_and_uses_registered_baseline():
    broker = FakeBroker()
    received = []
    feed = MarketFeed(broker, received.append)
    feed.subscribe({"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE",
                    "previous_close": 2828.4})

    broker.callback({"SecurityId": "9999", "LTP": 100.0, "PreviousClose": 90.0})
    broker.callback({"SecurityId": "1333", "LTP": 100.0})

    assert len(received) == 1
    assert received[0]["previous_close"] == 2828.4