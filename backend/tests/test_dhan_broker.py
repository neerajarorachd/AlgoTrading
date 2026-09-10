"""
Test suite for DhanBroker.

IMPORTANT — what these tests do and don't prove:
These tests mock the HTTP transport (`requests.Session`) and the WebSocket
client, so they verify that DhanBroker builds correct requests, parses
responses correctly, maps errors correctly, and correctly implements the
BaseBroker interface. They do NOT verify that Dhan's real API actually
behaves the way these mocks assume — that can only be confirmed against
Dhan's live/sandbox API once network access is available.
"""
import json
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, "/home/claude/trading_system/backend")

from brokers.base_broker import BaseBroker
from brokers.dhan_broker import DhanBroker
from brokers.models import (
    BrokerAPIError,
    BrokerConnectionError,
    ExchangeSegment,
    OrderRequest,
    OrderStatus,
    OrderType,
    ProductType,
    TransactionType,
)


def make_response(status_code=200, json_body=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or json.dumps(json_body or {})
    resp.json.return_value = json_body if json_body is not None else {}
    return resp


class FakeClock:
    """A controllable monotonic clock: sleep() advances it, matching real time.sleep's
    effect on time.monotonic() — needed so DhanBroker's request-pacing throttle sees a
    consistent, test-controlled notion of elapsed time instead of racing the real clock."""

    def __init__(self, start: float = 100.0):
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class TestDhanBrokerInterfaceCompliance(unittest.TestCase):
    def test_implements_all_abstract_methods(self):
        """DhanBroker must implement every abstract method on BaseBroker — instantiation
        would fail with TypeError if any were missing."""
        broker = DhanBroker(client_id="c1", access_token="t1")
        self.assertIsInstance(broker, BaseBroker)


class TestDhanBrokerConnection(unittest.TestCase):
    def test_connect_success(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"availabelBalance": 50000})
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        broker.connect()

        self.assertTrue(broker._connected)
        called_url = session.request.call_args.args[1]
        self.assertIn("/fundlimit", called_url)
        called_headers = session.request.call_args.kwargs["headers"]
        self.assertEqual(called_headers["client-id"], "c1")
        self.assertEqual(called_headers["access-token"], "t1")

    def test_connect_auth_failure_raises_broker_api_error(self):
        session = MagicMock()
        session.request.return_value = make_response(401, {"errorMessage": "Invalid token"})
        broker = DhanBroker(client_id="c1", access_token="bad-token", session=session)

        with self.assertRaises(BrokerAPIError) as ctx:
            broker.connect()
        self.assertEqual(ctx.exception.status_code, 401)

    def test_connect_network_failure_raises_broker_connection_error(self):
        import requests
        session = MagicMock()
        session.request.side_effect = requests.ConnectionError("DNS failure")
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        with self.assertRaises(BrokerConnectionError):
            broker.connect()


class TestDhanBrokerRateLimit(unittest.TestCase):
    """Dhan's standard 429 backoff: retry after 1s, then 2s, then give up."""

    def test_retries_once_after_1s_then_succeeds(self):
        session = MagicMock()
        session.request.side_effect = [
            make_response(429, {"data": {"805": "Too many requests"}}),
            make_response(200, {"availabelBalance": 50000}),
        ]
        clock = FakeClock()
        broker = DhanBroker(client_id="c1", access_token="t1", session=session,
                             sleep=clock.sleep, clock=clock.clock)

        broker.connect()

        self.assertTrue(broker._connected)
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(clock.sleeps, [1.0])

    def test_retries_1s_then_2s_then_succeeds(self):
        session = MagicMock()
        session.request.side_effect = [
            make_response(429, {}),
            make_response(429, {}),
            make_response(200, {"availabelBalance": 50000}),
        ]
        clock = FakeClock()
        broker = DhanBroker(client_id="c1", access_token="t1", session=session,
                             sleep=clock.sleep, clock=clock.clock)

        broker.connect()

        self.assertEqual(session.request.call_count, 3)
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_gives_up_after_1s_and_2s_retries_and_raises(self):
        session = MagicMock()
        session.request.side_effect = [
            make_response(429, {}),
            make_response(429, {}),
            make_response(429, {"data": {"805": "Too many requests"}}),
        ]
        clock = FakeClock()
        broker = DhanBroker(client_id="c1", access_token="t1", session=session,
                             sleep=clock.sleep, clock=clock.clock)

        with self.assertRaises(BrokerAPIError) as ctx:
            broker.connect()

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(session.request.call_count, 3)
        self.assertEqual(clock.sleeps, [1.0, 2.0])  # exactly Dhan's standard two-step backoff, no more

    def test_non_429_error_is_not_retried(self):
        session = MagicMock()
        session.request.return_value = make_response(401, {"errorMessage": "Invalid token"})
        clock = FakeClock()
        broker = DhanBroker(client_id="c1", access_token="bad-token", session=session,
                             sleep=clock.sleep, clock=clock.clock)

        with self.assertRaises(BrokerAPIError):
            broker.connect()

        self.assertEqual(session.request.call_count, 1)
        self.assertEqual(clock.sleeps, [])


class TestDhanBrokerRequestThrottle(unittest.TestCase):
    """Proactive pacing between REST calls — the queue-like behavior that keeps
    a burst of calls (e.g. hydrating several registered symbols) from tripping
    Dhan's rate limit in the first place, rather than only reacting after a 429."""

    def test_back_to_back_calls_are_spaced_by_min_interval(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"availabelBalance": 50000})
        clock = FakeClock()
        broker = DhanBroker(client_id="c1", access_token="t1", session=session,
                             sleep=clock.sleep, clock=clock.clock)

        broker.connect()  # first call: no prior request, nothing to wait for
        broker.connect()  # second call, immediately after: must wait out the gap

        self.assertEqual(clock.sleeps, [1.0])

    def test_no_wait_once_enough_time_has_already_passed(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"availabelBalance": 50000})
        clock = FakeClock()
        broker = DhanBroker(client_id="c1", access_token="t1", session=session,
                             sleep=clock.sleep, clock=clock.clock)

        broker.connect()
        clock.now += 1.5  # simulate other work happening between calls
        broker.connect()

        self.assertEqual(clock.sleeps, [])

    def test_three_back_to_back_calls_each_wait_the_remaining_gap(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"availabelBalance": 50000})
        clock = FakeClock()
        broker = DhanBroker(client_id="c1", access_token="t1", session=session,
                             sleep=clock.sleep, clock=clock.clock)

        broker.connect()
        broker.connect()
        broker.connect()

        self.assertEqual(clock.sleeps, [1.0, 1.0])
        self.assertEqual(session.request.call_count, 3)


class TestDhanBrokerHistoricalData(unittest.TestCase):
    def test_get_historical_intraday_parses_parallel_arrays_into_candles(self):
        session = MagicMock()
        ts1, ts2 = 1725600000, 1725600060
        session.request.return_value = make_response(200, {
            "open": [100.0, 101.0], "high": [102.0, 103.0],
            "low": [99.5, 100.5], "close": [101.0, 102.5],
            "volume": [1000, 1500], "timestamp": [ts1, ts2],
        })
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        candles = broker.get_historical_data(
            symbol="RELIANCE", security_id="1333", exchange_segment="NSE_EQ",
            timeframe="1min", from_date=datetime(2026, 9, 1), to_date=datetime(2026, 9, 5),
        )

        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].open, 100.0)
        self.assertEqual(candles[1].close, 102.5)
        self.assertEqual(candles[0].symbol, "RELIANCE")
        self.assertEqual(candles[0].timeframe, "1min")

        # verify the request went to the intraday endpoint with the right interval param
        called_url = session.request.call_args.args[1]
        called_payload = json.loads(session.request.call_args.kwargs["data"])
        self.assertIn("/charts/intraday", called_url)
        self.assertEqual(called_payload["interval"], "1")

    def test_get_historical_daily_uses_historical_endpoint(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {
            "open": [100.0], "high": [102.0], "low": [99.0], "close": [101.0],
            "volume": [10000], "timestamp": [1725600000],
        })
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        candles = broker.get_historical_data(
            symbol="RELIANCE", security_id="1333", exchange_segment="NSE_EQ",
            timeframe="1day", from_date=datetime(2026, 1, 1), to_date=datetime(2026, 9, 5),
        )
        called_url = session.request.call_args.args[1]
        self.assertIn("/charts/historical", called_url)
        self.assertEqual(len(candles), 1)

    def test_empty_historical_response_returns_empty_list(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {})
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        candles = broker.get_historical_data(
            symbol="RELIANCE", security_id="1333", exchange_segment="NSE_EQ",
            timeframe="5min", from_date=datetime(2026, 9, 1), to_date=datetime(2026, 9, 5),
        )
        self.assertEqual(candles, [])


class TestDhanBrokerQuote(unittest.TestCase):
    def test_get_quote_parses_response(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {
            "data": {"NSE_EQ": {"1333": {
                "last_price": 2456.30, "volume": 50000,
                "ohlc": {"open": 2440.0, "high": 2460.0, "low": 2435.0, "close": 2450.0},
                "depth": {
                    "buy": [{"quantity": 100, "orders": 2, "price": 2456.20}],
                    "sell": [{"quantity": 75, "orders": 3, "price": 2456.40}],
                },
            }}}
        })
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        quote = broker.get_quote(symbol="RELIANCE", security_id="1333", exchange_segment="NSE_EQ")

        self.assertEqual(quote.ltp, 2456.30)
        self.assertEqual(quote.high, 2460.0)
        self.assertEqual(quote.symbol, "RELIANCE")
        self.assertEqual(quote.bid_depth[0].quantity, 100)
        self.assertEqual(quote.bid_depth[0].price, 2456.20)
        self.assertEqual(quote.ask_depth[0].orders, 3)


class TestDhanBrokerOrders(unittest.TestCase):
    def _sample_order(self, **overrides):
        defaults = dict(
            symbol="RELIANCE", security_id="1333", exchange_segment=ExchangeSegment.NSE_EQ,
            transaction_type=TransactionType.BUY, quantity=10, order_type=OrderType.MARKET,
            product_type=ProductType.INTRADAY, price=0.0,
        )
        defaults.update(overrides)
        return OrderRequest(**defaults)

    def test_place_normal_order_success(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {
            "orderId": "112111182198", "orderStatus": "TRADED",
            "filledQty": 10, "averageTradedPrice": 2456.30,
        })
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        result = broker.place_order(self._sample_order())

        self.assertEqual(result.order_id, "112111182198")
        self.assertEqual(result.status, OrderStatus.FILLED)
        self.assertEqual(result.filled_quantity, 10)
        called_url = session.request.call_args.args[1]
        self.assertTrue(called_url.endswith("/orders"))

    def test_place_order_rejected_raises_broker_api_error(self):
        session = MagicMock()
        session.request.return_value = make_response(400, {"errorMessage": "Insufficient funds"})
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        with self.assertRaises(BrokerAPIError):
            broker.place_order(self._sample_order())

    def test_place_super_order_requires_sl_and_target(self):
        session = MagicMock()
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        order = self._sample_order(is_super_order=True)   # no SL/target set
        with self.assertRaises(ValueError):
            broker.place_super_order(order)

    def test_place_super_order_sends_sl_and_target_legs(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {
            "orderId": "9988776655", "orderStatus": "PENDING", "filledQty": 0,
        })
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        order = self._sample_order(
            is_super_order=True, order_type=OrderType.LIMIT, price=2450.0,
            stop_loss_price=2430.0, target_price=2480.0, trailing_jump=2.0,
        )
        result = broker.place_super_order(order)

        called_url = session.request.call_args.args[1]
        called_payload = json.loads(session.request.call_args.kwargs["data"])
        self.assertIn("/super/orders", called_url)
        self.assertEqual(called_payload["stopLossPrice"], 2430.0)
        self.assertEqual(called_payload["targetPrice"], 2480.0)
        self.assertEqual(called_payload["trailingJump"], 2.0)
        self.assertEqual(result.status, OrderStatus.PENDING)

    def test_modify_order(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {
            "orderStatus": "OPEN", "tradingSymbol": "RELIANCE", "quantity": 10, "filledQty": 0,
        })
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        result = broker.modify_order("112111182198", price=2445.0)

        self.assertEqual(result.status, OrderStatus.OPEN)
        called_url = session.request.call_args.args[1]
        called_method = session.request.call_args.args[0]
        self.assertEqual(called_method, "PUT")
        self.assertIn("112111182198", called_url)

    def test_cancel_order(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"tradingSymbol": "RELIANCE", "quantity": 10})
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        result = broker.cancel_order("112111182198")

        self.assertEqual(result.status, OrderStatus.CANCELLED)
        called_method = session.request.call_args.args[0]
        self.assertEqual(called_method, "DELETE")

    def test_get_order_status_unknown_maps_gracefully(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"orderStatus": "SOME_NEW_STATUS_WE_DONT_KNOW"})
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        result = broker.get_order_status("112111182198")
        self.assertEqual(result.status, OrderStatus.UNKNOWN)   # doesn't crash on unmapped status


class TestDhanBrokerPortfolio(unittest.TestCase):
    def test_get_positions_parses_list(self):
        session = MagicMock()
        session.request.return_value = make_response(200, [{
            "tradingSymbol": "RELIANCE", "securityId": "1333", "exchangeSegment": "NSE_EQ",
            "productType": "INTRADAY", "netQty": 10, "costPrice": 2450.0,
            "lastTradedPrice": 2460.0, "unrealizedProfit": 100.0, "realizedProfit": 0.0,
        }])
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        positions = broker.get_positions()

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].quantity, 10)
        self.assertEqual(positions[0].unrealized_pnl, 100.0)

    def test_get_holdings_parses_list(self):
        session = MagicMock()
        session.request.return_value = make_response(200, [{
            "tradingSymbol": "TCS", "securityId": "11536", "totalQty": 5,
            "avgCostPrice": 3500.0, "lastTradedPrice": 3550.0,
        }])
        broker = DhanBroker(client_id="c1", access_token="t1", session=session)

        holdings = broker.get_holdings()

        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0].symbol, "TCS")
        self.assertEqual(holdings[0].quantity, 5)


class FakeWSClient:
    """Injected in place of the real websocket-client-based wrapper, so feed
    tests don't need that package installed and don't touch the network."""
    def __init__(self, on_tick):
        self.on_tick = on_tick
        self.connected_url = None
        self.sent_messages = []
        self.closed = False

    def connect(self, url):
        self.connected_url = url

    def send(self, message):
        self.sent_messages.append(json.loads(message))

    def close(self):
        self.closed = True

    def simulate_incoming_tick(self, tick: dict):
        self.on_tick(tick)


class TestDhanBrokerFeed(unittest.TestCase):
    def test_subscribe_feed_sends_correct_subscribe_message(self):
        received_ticks = []
        fake_ws_holder = {}

        def factory(on_tick):
            ws = FakeWSClient(on_tick)
            fake_ws_holder["ws"] = ws
            return ws

        broker = DhanBroker(client_id="c1", access_token="t1", ws_client_factory=factory)

        broker.subscribe_feed(
            instruments=[{"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE"}],
            on_tick=lambda tick: received_ticks.append(tick),
        )

        ws = fake_ws_holder["ws"]
        self.assertIsNotNone(ws.connected_url)
        self.assertEqual(len(ws.sent_messages), 1)
        self.assertEqual(ws.sent_messages[0]["RequestCode"], 15)
        self.assertEqual(ws.sent_messages[0]["InstrumentList"][0]["SecurityId"], "1333")

        # simulate a tick arriving from the broker and confirm our callback fires
        ws.simulate_incoming_tick({"SecurityId": "1333", "LTP": 2456.30})
        self.assertEqual(len(received_ticks), 1)
        self.assertEqual(received_ticks[0]["LTP"], 2456.30)

    def test_unsubscribe_feed_sends_correct_message(self):
        fake_ws_holder = {}

        def factory(on_tick):
            ws = FakeWSClient(on_tick)
            fake_ws_holder["ws"] = ws
            return ws

        broker = DhanBroker(client_id="c1", access_token="t1", ws_client_factory=factory)
        broker.subscribe_feed(
            instruments=[{"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE"}],
            on_tick=lambda tick: None,
        )
        broker.unsubscribe_feed(instruments=[{"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE"}])

        ws = fake_ws_holder["ws"]
        self.assertEqual(len(ws.sent_messages), 2)
        self.assertEqual(ws.sent_messages[1]["RequestCode"], 16)

    def test_unsubscribe_before_subscribe_is_a_no_op(self):
        broker = DhanBroker(client_id="c1", access_token="t1")
        # should not raise even though no ws connection exists yet
        broker.unsubscribe_feed(instruments=[{"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
