from depth_metrics import calculate_depth_metrics
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


def test_market_feed_emits_depth_once_both_sides_and_ltp_known():
    broker = FakeBroker()
    depth_events = []
    feed = MarketFeed(broker, on_tick=lambda t: None, on_depth=depth_events.append)
    feed.subscribe({"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE",
                    "previous_close": 2828.4})

    buy_levels = [{"price": 2854.0, "quantity": 100, "orders": 3}, {"price": 2853.5, "quantity": 50, "orders": 2}]
    sell_levels = [{"price": 2855.0, "quantity": 80, "orders": 4}, {"price": 2855.5, "quantity": 60, "orders": 1}]

    # a depth packet before any LTP tick has to be dropped — no ltp to join it against
    broker.callback({"type": "Full Market Depth", "exchange_segment": "NSE_EQ",
                      "security_id": "1333", "side": "buy", "levels": buy_levels})
    assert depth_events == []

    broker.callback({"SecurityId": "1333", "LTP": 2854.65})  # seeds _last_ltp
    broker.callback({"type": "Full Market Depth", "exchange_segment": "NSE_EQ",
                      "security_id": "1333", "side": "buy", "levels": buy_levels})
    assert depth_events == []  # only one side so far

    broker.callback({"type": "Full Market Depth", "exchange_segment": "NSE_EQ",
                      "security_id": "1333", "side": "sell", "levels": sell_levels})

    assert len(depth_events) == 1
    event = depth_events[0]
    assert event["type"] == "depth"
    assert event["symbol"] == "RELIANCE"
    expected_metrics = calculate_depth_metrics(2854.65, buy_levels, sell_levels)
    for field, value in expected_metrics.items():
        assert event[field] == value


def test_market_feed_seeds_ltp_at_subscribe_time_for_cold_start_depth():
    broker = FakeBroker()
    depth_events = []
    feed = MarketFeed(broker, on_tick=lambda t: None, on_depth=depth_events.append)
    feed.subscribe({"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE",
                    "previous_close": 2828.4, "ltp": 2854.65})

    buy_levels = [{"price": 2854.0, "quantity": 100, "orders": 3}]
    sell_levels = [{"price": 2855.0, "quantity": 80, "orders": 4}]
    broker.callback({"type": "Full Market Depth", "exchange_segment": "NSE_EQ",
                      "security_id": "1333", "side": "buy", "levels": buy_levels})
    broker.callback({"type": "Full Market Depth", "exchange_segment": "NSE_EQ",
                      "security_id": "1333", "side": "sell", "levels": sell_levels})

    # no LTP tick was ever sent, but depth still resolved from the subscribe-time seed
    assert len(depth_events) == 1


def test_market_feed_computes_volume_delta_from_cumulative_totals():
    broker = FakeBroker()
    candle_ticks = []
    feed = MarketFeed(broker, on_tick=lambda t: None,
                       on_candle_tick=lambda instrument, tick: candle_ticks.append((instrument["symbol"], tick)))
    feed.subscribe({"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE",
                    "previous_close": 2828.4})

    broker.callback({"SecurityId": "1333", "LTP": 2850.0, "volume": 1000})
    broker.callback({"SecurityId": "1333", "LTP": 2851.0, "volume": 1500})

    # first observation has no prior baseline to diff against — treated as 0 delta,
    # not the full cumulative total, so a mid-day subscribe doesn't attribute the
    # whole pre-subscription day volume to a single tick
    assert candle_ticks[0][1]["volume"] == 0
    assert candle_ticks[1][1]["volume"] == 500   # 1500 - 1000


def test_market_feed_clamps_volume_delta_to_zero_on_counter_reset():
    broker = FakeBroker()
    candle_ticks = []
    feed = MarketFeed(broker, on_tick=lambda t: None,
                       on_candle_tick=lambda instrument, tick: candle_ticks.append(tick))
    feed.subscribe({"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE",
                    "previous_close": 2828.4})

    broker.callback({"SecurityId": "1333", "LTP": 2850.0, "volume": 5000})
    broker.callback({"SecurityId": "1333", "LTP": 2851.0, "volume": 100})  # counter reset (e.g. new session)

    assert candle_ticks[1]["volume"] == 0  # never a negative delta


def test_market_feed_tick_only_packets_contribute_zero_volume():
    broker = FakeBroker()
    candle_ticks = []
    feed = MarketFeed(broker, on_tick=lambda t: None,
                       on_candle_tick=lambda instrument, tick: candle_ticks.append(tick))
    feed.subscribe({"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE",
                    "previous_close": 2828.4})

    broker.callback({"SecurityId": "1333", "LTP": 2850.0})  # ticker packet: no "volume" key at all

    assert candle_ticks[0]["volume"] == 0