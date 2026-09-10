"""
Test suite for UpstoxBroker — same caveat as the others: verifies wrapper
logic against mocked HTTP responses, not real Upstox API behavior. The GTT
(super order) schema in particular should be treated as unverified.
"""
import json
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, "/home/claude/trading_system/backend")

from brokers.base_broker import BaseBroker
from brokers.upstox_broker import UpstoxBroker
from brokers.models import (
    BrokerAPIError,
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


class TestUpstoxBrokerInterfaceCompliance(unittest.TestCase):
    def test_implements_all_abstract_methods(self):
        broker = UpstoxBroker(access_token="t1")
        self.assertIsInstance(broker, BaseBroker)


class TestUpstoxBrokerConnection(unittest.TestCase):
    def test_connect_success(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": {"user_id": "AB1234"}})
        broker = UpstoxBroker(access_token="t1", session=session)

        broker.connect()

        self.assertTrue(broker._connected)
        headers = session.request.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer t1")

    def test_connect_auth_failure(self):
        session = MagicMock()
        session.request.return_value = make_response(401, {"status": "error"})
        broker = UpstoxBroker(access_token="bad", session=session)

        with self.assertRaises(BrokerAPIError):
            broker.connect()


class TestUpstoxBrokerHistoricalData(unittest.TestCase):
    def test_get_historical_data_parses_candles(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {
            "data": {"candles": [
                ["2026-09-05T09:15:00+05:30", 100.0, 102.0, 99.0, 101.0, 1000, 0],
                ["2026-09-05T09:16:00+05:30", 101.0, 103.0, 100.5, 102.5, 1500, 0],
            ]}
        })
        broker = UpstoxBroker(access_token="t1", session=session)

        candles = broker.get_historical_data(
            symbol="RELIANCE", security_id="NSE_EQ|INE002A01018", exchange_segment="NSE_EQ",
            timeframe="1min", from_date=datetime(2026, 9, 1), to_date=datetime(2026, 9, 5),
        )

        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].open, 100.0)
        called_url = session.request.call_args.args[1]
        self.assertIn("/historical-candle/NSE_EQ|INE002A01018/1minute/", called_url)


class TestUpstoxBrokerOrders(unittest.TestCase):
    def _sample_order(self, **overrides):
        defaults = dict(
            symbol="RELIANCE", security_id="NSE_EQ|INE002A01018", exchange_segment=ExchangeSegment.NSE_EQ,
            transaction_type=TransactionType.BUY, quantity=10, order_type=OrderType.MARKET,
            product_type=ProductType.INTRADAY, price=0.0,
        )
        defaults.update(overrides)
        return OrderRequest(**defaults)

    def test_place_order_success(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": {"order_id": "23092500012345"}})
        broker = UpstoxBroker(access_token="t1", session=session)

        result = broker.place_order(self._sample_order())

        self.assertEqual(result.order_id, "23092500012345")
        called_url = session.request.call_args.args[1]
        self.assertTrue(called_url.endswith("/order/place"))
        called_payload = session.request.call_args.kwargs["json"]
        self.assertEqual(called_payload["product"], "I")

    def test_place_super_order_requires_sl_and_target(self):
        broker = UpstoxBroker(access_token="t1", session=MagicMock())
        with self.assertRaises(ValueError):
            broker.place_super_order(self._sample_order())

    def test_place_super_order_builds_gtt_rules(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": {"gtt_order_id": "GTT998877"}})
        broker = UpstoxBroker(access_token="t1", session=session)

        order = self._sample_order(order_type=OrderType.LIMIT, price=2450.0,
                                    stop_loss_price=2430.0, target_price=2480.0)
        result = broker.place_super_order(order)

        called_url = session.request.call_args.args[1]
        called_payload = session.request.call_args.kwargs["json"]
        self.assertTrue(called_url.endswith("/order/gtt/place"))
        self.assertEqual(len(called_payload["rules"]), 3)
        strategies = [r["strategy"] for r in called_payload["rules"]]
        self.assertEqual(strategies, ["ENTRY", "TARGET", "STOPLOSS"])
        self.assertEqual(result.order_id, "GTT998877")

    def test_cancel_order(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": {}})
        broker = UpstoxBroker(access_token="t1", session=session)

        result = broker.cancel_order("23092500012345")
        self.assertEqual(result.status, OrderStatus.CANCELLED)
        called_method = session.request.call_args.args[0]
        self.assertEqual(called_method, "DELETE")

    def test_get_order_status_maps_lowercase_status(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": {
            "status": "complete", "trading_symbol": "RELIANCE",
            "quantity": 10, "filled_quantity": 10, "average_price": 2456.3,
        }})
        broker = UpstoxBroker(access_token="t1", session=session)

        result = broker.get_order_status("23092500012345")
        self.assertEqual(result.status, OrderStatus.FILLED)


class TestUpstoxBrokerPortfolio(unittest.TestCase):
    def test_get_positions(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": [{
            "trading_symbol": "RELIANCE", "instrument_token": "NSE_EQ|INE002A01018",
            "quantity": 10, "average_price": 2450.0, "last_price": 2460.0,
            "product": "I", "unrealised": 100.0, "realised": 0.0,
        }]})
        broker = UpstoxBroker(access_token="t1", session=session)

        positions = broker.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].quantity, 10)

    def test_get_holdings(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": [{
            "trading_symbol": "TCS", "instrument_token": "NSE_EQ|INE467B01029",
            "quantity": 5, "average_price": 3500.0, "last_price": 3550.0,
        }]})
        broker = UpstoxBroker(access_token="t1", session=session)

        holdings = broker.get_holdings()
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0].symbol, "TCS")


class FakeWSClient:
    def __init__(self, on_tick):
        self.on_tick = on_tick
        self.connected_url = None
        self.sent_messages = []

    def connect(self, url):
        self.connected_url = url

    def send(self, message):
        self.sent_messages.append(message)

    def close(self):
        pass


class TestUpstoxBrokerFeed(unittest.TestCase):
    def test_subscribe_feed_sends_instrument_keys(self):
        holder = {}

        def factory(on_tick):
            ws = FakeWSClient(on_tick)
            holder["ws"] = ws
            return ws

        broker = UpstoxBroker(access_token="t1", ws_client_factory=factory)
        broker.subscribe_feed(
            instruments=[{"security_id": "NSE_EQ|INE002A01018", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE"}],
            on_tick=lambda t: None,
        )
        ws = holder["ws"]
        self.assertEqual(ws.sent_messages[0]["data"]["instrumentKeys"], ["NSE_EQ|INE002A01018"])

    def test_subscribe_feed_without_factory_raises_explicit_error(self):
        broker = UpstoxBroker(access_token="t1")
        with self.assertRaises(NotImplementedError):
            broker.subscribe_feed(instruments=[{"security_id": "X", "exchange_segment": "NSE_EQ", "symbol": "X"}],
                                    on_tick=lambda t: None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
