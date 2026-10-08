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
from datetime import datetime, timezone
from unittest.mock import MagicMock

sys.path.insert(0, "/home/claude/trading_system/backend")

from brokers.base_broker import BaseBroker
import brokers.dhan_broker as dhan_broker_module
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


class TestDhanBrokerChartRequestThrottle(unittest.TestCase):
    """/charts/* (historical/intraday candles) gets its own, longer minimum
    interval — confirmed live (2026-09-14) that Dhan's effective rate limit
    for this endpoint is stricter than the rest of the REST API: a batch of
    get_historical_data calls spaced 1s apart (the general-call interval)
    failed 12/13 with a 400 "no data present" error, not a 429, while the
    same calls spaced 3s apart succeeded every time."""

    def _historical_response(self):
        return make_response(200, {
            "open": [100.0], "high": [102.0], "low": [99.0], "close": [101.0],
            "volume": [10000], "timestamp": [1725600000],
        })

    def test_back_to_back_chart_calls_are_spaced_by_the_longer_chart_interval(self):
        session = MagicMock()
        session.request.return_value = self._historical_response()
        clock = FakeClock()
        broker = DhanBroker(client_id="c1", access_token="t1", session=session,
                             sleep=clock.sleep, clock=clock.clock)

        broker.get_historical_data(symbol="RELIANCE", security_id="1333", exchange_segment="NSE_EQ",
                                    timeframe="1day", from_date=datetime(2026, 1, 1), to_date=datetime(2026, 9, 5))
        broker.get_historical_data(symbol="TCS", security_id="11536", exchange_segment="NSE_EQ",
                                    timeframe="1day", from_date=datetime(2026, 1, 1), to_date=datetime(2026, 9, 5))

        self.assertEqual(clock.sleeps, [3.0])  # not 1.0 — the general-call interval

    def test_chart_throttle_is_independent_of_the_general_call_throttle(self):
        """A chart call must not make an immediately-following general call
        (e.g. get_quote) wait out the chart interval — they're separate
        queues, since nothing about Dhan's stricter chart-endpoint limit
        should slow down unrelated quote/order calls."""
        session = MagicMock()
        session.request.return_value = self._historical_response()
        clock = FakeClock()
        broker = DhanBroker(client_id="c1", access_token="t1", session=session,
                             sleep=clock.sleep, clock=clock.clock)

        broker.get_historical_data(symbol="RELIANCE", security_id="1333", exchange_segment="NSE_EQ",
                                    timeframe="1day", from_date=datetime(2026, 1, 1), to_date=datetime(2026, 9, 5))
        session.request.return_value = make_response(200, {"availabelBalance": 50000})
        broker.connect()  # first general call ever — nothing to wait for, regardless of the chart call above

        self.assertEqual(clock.sleeps, [])


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
        self.assertEqual(ws.sent_messages[0]["RequestCode"], 21)  # Subscribe Full (includes depth)
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

    def test_disconnect_clears_state_even_when_ws_close_raises(self):
        # The real-world scenario this guards: a market-hours session
        # scheduler calls disconnect() from a thread other than the one
        # running run_forever(), where ws.close() can raise (documented,
        # pre-existing risk). State must still be cleared so the NEXT
        # connect doesn't reuse a dead websocket object.
        class RaisingWSClient(FakeWSClient):
            def close(self):
                raise RuntimeError("simulated cross-thread close failure")

        fake_ws_holder = {}

        def factory(on_tick):
            ws = RaisingWSClient(on_tick)
            fake_ws_holder["ws"] = ws
            return ws

        broker = DhanBroker(client_id="c1", access_token="t1", ws_client_factory=factory)
        broker.subscribe_feed(
            instruments=[{"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE"}],
            on_tick=lambda tick: None,
        )

        broker.disconnect()  # must not raise, even though ws.close() does

        self.assertIsNone(broker._ws)
        self.assertFalse(broker._connected)

    def test_disconnect_is_a_no_op_when_never_connected(self):
        broker = DhanBroker(client_id="c1", access_token="t1")
        broker.disconnect()  # should not raise
        self.assertIsNone(broker._ws)
        self.assertFalse(broker._connected)


class TestDhanBrokerFeedSelfHealing(unittest.TestCase):
    """Added 2026-10-06: the live feed went silently dead for a whole session
    because a dropped connection was never reopened or re-subscribed."""

    RELIANCE = {"security_id": "1333", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE"}
    TCS = {"security_id": "11536", "exchange_segment": "NSE_EQ", "symbol": "TCS"}

    def _broker(self, client_cls=FakeWSClient):
        holder = {}

        def factory(on_tick):
            holder["ws"] = client_cls(on_tick)
            return holder["ws"]
        return DhanBroker(client_id="c1", access_token="t1", ws_client_factory=factory), holder

    def test_reconnect_resubscribes_every_tracked_instrument(self):
        broker, holder = self._broker()
        broker.subscribe_feed([self.RELIANCE], on_tick=lambda t: None)
        broker.subscribe_feed([self.TCS], on_tick=lambda t: None)
        ws = holder["ws"]
        ws.sent_messages.clear()

        broker._on_ws_close("simulated drop")
        broker._on_ws_open()  # what websocket-client calls once it has reconnected

        self.assertEqual(len(ws.sent_messages), 1)
        self.assertEqual(ws.sent_messages[0]["RequestCode"], 21)
        self.assertEqual({i["SecurityId"] for i in ws.sent_messages[0]["InstrumentList"]}, {"1333", "11536"})

    def test_unsubscribed_instruments_are_not_restored_on_reconnect(self):
        broker, holder = self._broker()
        broker.subscribe_feed([self.RELIANCE, self.TCS], on_tick=lambda t: None)
        broker.unsubscribe_feed([self.TCS])
        holder["ws"].sent_messages.clear()

        broker._on_ws_open()

        self.assertEqual([i["SecurityId"] for i in holder["ws"].sent_messages[0]["InstrumentList"]], ["1333"])

    def test_subscribe_while_socket_is_down_is_tracked_not_raised(self):
        class DeadWSClient(FakeWSClient):
            def send(self, message):
                raise ConnectionError("socket is reconnecting")

        broker, holder = self._broker(DeadWSClient)
        broker.subscribe_feed([self.RELIANCE], on_tick=lambda t: None)  # must not raise

        holder["ws"].__class__ = FakeWSClient  # link comes back
        broker._on_ws_open()
        self.assertEqual(holder["ws"].sent_messages[0]["InstrumentList"][0]["SecurityId"], "1333")

    def test_subscriptions_are_chunked_to_100_per_message(self):
        broker, holder = self._broker()
        many = [{"security_id": str(i), "exchange_segment": "NSE_EQ", "symbol": f"S{i}"} for i in range(250)]
        broker.subscribe_feed(many, on_tick=lambda t: None)
        self.assertEqual([m["InstrumentCount"] for m in holder["ws"].sent_messages], [100, 100, 50])

    def test_deliberate_disconnect_forgets_subscriptions(self):
        # The next session's connect must not re-subscribe on open ahead of
        # hydration's backfill-then-subscribe ordering.
        broker, _ = self._broker()
        broker.subscribe_feed([self.RELIANCE], on_tick=lambda t: None)
        broker.disconnect()
        self.assertEqual(broker.feed_status()["subscribed"], 0)
        self.assertEqual(broker.feed_status()["disconnects"], 0)  # a stop isn't a drop

    def test_feed_status_tracks_connect_drop_and_messages(self):
        broker, holder = self._broker()
        self.assertFalse(broker.feed_status()["connected"])

        broker.subscribe_feed([self.RELIANCE], on_tick=lambda t: None)
        broker._on_ws_open()
        broker._on_ws_message()
        status = broker.feed_status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["subscribed"], 1)
        self.assertIsNotNone(status["connected_at"])
        self.assertTrue(status["last_message_at"].endswith("Z"))

        broker._on_ws_close("simulated drop")
        self.assertFalse(broker.feed_status()["connected"])

        # counted on the re-open: a clean server close reconnects without
        # firing on_close/on_error at all (verified against the real library)
        broker._on_ws_open()
        status = broker.feed_status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["disconnects"], 1)

    def _watchdog_broker(self):
        class ReconnectableWS(FakeWSClient):
            forced = 0

            def force_reconnect(self):
                ReconnectableWS.forced += 1
        broker, holder = self._broker(ReconnectableWS)
        broker.subscribe_feed([self.RELIANCE], on_tick=lambda t: None)
        broker._on_ws_open()
        return broker, holder, ReconnectableWS

    def test_watchdog_never_fires_before_the_first_message_of_a_session(self):
        # 08:50 connect, 09:15 open: no data yet is expected, not a dead link
        from datetime import timedelta as _td
        broker, _, ws_cls = self._watchdog_broker()
        later = datetime.now(timezone.utc) + _td(minutes=30)
        self.assertFalse(broker._check_feed_stale(now=later))
        self.assertEqual(ws_cls.forced, 0)

    def test_watchdog_forces_a_reconnect_when_flowing_data_stops(self):
        from datetime import timedelta as _td
        broker, _, ws_cls = self._watchdog_broker()
        broker._on_ws_message()
        soon = datetime.now(timezone.utc) + _td(seconds=30)
        self.assertFalse(broker._check_feed_stale(now=soon))  # a short lull is fine
        late = datetime.now(timezone.utc) + _td(seconds=dhan_broker_module.FEED_STALE_RECONNECT_SEC + 5)
        self.assertTrue(broker._check_feed_stale(now=late))
        self.assertEqual(ws_cls.forced, 1)
        self.assertFalse(broker.feed_status()["connected"])

    def test_watchdog_stays_quiet_after_a_deliberate_stop(self):
        from datetime import timedelta as _td
        broker, _, ws_cls = self._watchdog_broker()
        broker._on_ws_message()
        broker.disconnect()
        late = datetime.now(timezone.utc) + _td(minutes=10)
        self.assertFalse(broker._check_feed_stale(now=late))
        self.assertEqual(ws_cls.forced, 0)

    def test_a_clean_drop_with_no_close_callback_still_counts(self):
        broker, _ = self._broker()
        broker.subscribe_feed([self.RELIANCE], on_tick=lambda t: None)
        broker._on_ws_open()
        broker._on_ws_open()  # reconnect with no on_close in between
        self.assertEqual(broker.feed_status()["disconnects"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
