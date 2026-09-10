"""
Test suite for ZerodhaBroker — same caveat as Dhan's tests: these verify our
wrapper logic against mocked HTTP responses, not real Kite Connect behavior.
"""
import json
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, "/home/claude/trading_system/backend")

from brokers.base_broker import BaseBroker
from brokers.zerodha_broker import ZerodhaBroker
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


class TestZerodhaBrokerInterfaceCompliance(unittest.TestCase):
    def test_implements_all_abstract_methods(self):
        broker = ZerodhaBroker(api_key="k1", access_token="t1")
        self.assertIsInstance(broker, BaseBroker)


class TestZerodhaBrokerConnection(unittest.TestCase):
    def test_connect_success(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": {"user_id": "AB1234"}})
        broker = ZerodhaBroker(api_key="k1", access_token="t1", session=session)

        broker.connect()

        self.assertTrue(broker._connected)
        headers = session.request.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "token k1:t1")

    def test_connect_auth_failure(self):
        session = MagicMock()
        session.request.return_value = make_response(403, {"error_type": "TokenException"})
        broker = ZerodhaBroker(api_key="k1", access_token="bad", session=session)

        with self.assertRaises(BrokerAPIError):
            broker.connect()


class TestZerodhaBrokerHistoricalData(unittest.TestCase):
    def test_get_historical_data_parses_ohlc_rows(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {
            "data": {"candles": [
                ["2026-09-05T09:15:00+0530", 100.0, 102.0, 99.0, 101.0, 1000],
                ["2026-09-05T09:16:00+0530", 101.0, 103.0, 100.5, 102.5, 1500],
            ]}
        })
        broker = ZerodhaBroker(api_key="k1", access_token="t1", session=session)

        candles = broker.get_historical_data(
            symbol="RELIANCE", security_id="738561", exchange_segment="NSE_EQ",
            timeframe="1min", from_date=datetime(2026, 9, 1), to_date=datetime(2026, 9, 5),
        )

        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].open, 100.0)
        self.assertEqual(candles[1].close, 102.5)
        called_url = session.request.call_args.args[1]
        self.assertIn("/instruments/historical/738561/minute", called_url)

    def test_uses_correct_interval_mapping_for_5min(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": {"candles": []}})
        broker = ZerodhaBroker(api_key="k1", access_token="t1", session=session)

        broker.get_historical_data(
            symbol="RELIANCE", security_id="738561", exchange_segment="NSE_EQ",
            timeframe="5min", from_date=datetime(2026, 9, 1), to_date=datetime(2026, 9, 5),
        )
        called_url = session.request.call_args.args[1]
        self.assertIn("/5minute", called_url)


class TestZerodhaBrokerOrders(unittest.TestCase):
    def _sample_order(self, **overrides):
        defaults = dict(
            symbol="RELIANCE", security_id="738561", exchange_segment=ExchangeSegment.NSE_EQ,
            transaction_type=TransactionType.BUY, quantity=10, order_type=OrderType.MARKET,
            product_type=ProductType.INTRADAY, price=0.0,
        )
        defaults.update(overrides)
        return OrderRequest(**defaults)

    def test_place_order_uses_regular_variety_endpoint(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": {"order_id": "230925000123"}})
        broker = ZerodhaBroker(api_key="k1", access_token="t1", session=session)

        result = broker.place_order(self._sample_order())

        self.assertEqual(result.order_id, "230925000123")
        called_url = session.request.call_args.args[1]
        self.assertTrue(called_url.endswith("/orders/regular"))
        # form-encoded, not JSON body, for Kite
        called_payload = session.request.call_args.kwargs["data"]
        self.assertEqual(called_payload["product"], "MIS")
        self.assertEqual(called_payload["transaction_type"], "BUY")

    def test_place_super_order_requires_sl_and_target(self):
        broker = ZerodhaBroker(api_key="k1", access_token="t1", session=MagicMock())
        with self.assertRaises(ValueError):
            broker.place_super_order(self._sample_order())

    def test_place_super_order_uses_bo_variety_with_squareoff_and_stoploss(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": {"order_id": "230925000456"}})
        broker = ZerodhaBroker(api_key="k1", access_token="t1", session=session)

        order = self._sample_order(order_type=OrderType.LIMIT, price=2450.0,
                                    stop_loss_price=2430.0, target_price=2480.0)
        result = broker.place_super_order(order)

        called_url = session.request.call_args.args[1]
        called_payload = session.request.call_args.kwargs["data"]
        self.assertTrue(called_url.endswith("/orders/bo"))
        self.assertEqual(called_payload["squareoff"], "30.0")
        self.assertEqual(called_payload["stoploss"], "20.0")
        self.assertEqual(result.order_id, "230925000456")

    def test_cancel_order(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": {"order_id": "230925000123"}})
        broker = ZerodhaBroker(api_key="k1", access_token="t1", session=session)

        result = broker.cancel_order("230925000123")
        self.assertEqual(result.status, OrderStatus.CANCELLED)
        called_method = session.request.call_args.args[0]
        self.assertEqual(called_method, "DELETE")

    def test_get_order_status_uses_last_history_entry(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": [
            {"status": "OPEN", "tradingsymbol": "RELIANCE", "quantity": 10, "filled_quantity": 0, "average_price": 0},
            {"status": "COMPLETE", "tradingsymbol": "RELIANCE", "quantity": 10, "filled_quantity": 10, "average_price": 2456.3},
        ]})
        broker = ZerodhaBroker(api_key="k1", access_token="t1", session=session)

        result = broker.get_order_status("230925000123")
        self.assertEqual(result.status, OrderStatus.FILLED)
        self.assertEqual(result.filled_quantity, 10)


class TestZerodhaBrokerPortfolio(unittest.TestCase):
    def test_get_positions_reads_net_positions(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": {"net": [{
            "tradingsymbol": "RELIANCE", "instrument_token": "738561", "quantity": 10,
            "average_price": 2450.0, "last_price": 2460.0, "product": "MIS",
            "unrealised": 100.0, "realised": 0.0,
        }]}})
        broker = ZerodhaBroker(api_key="k1", access_token="t1", session=session)

        positions = broker.get_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].quantity, 10)

    def test_get_holdings(self):
        session = MagicMock()
        session.request.return_value = make_response(200, {"data": [{
            "tradingsymbol": "TCS", "instrument_token": "2953217", "quantity": 5,
            "average_price": 3500.0, "last_price": 3550.0,
        }]})
        broker = ZerodhaBroker(api_key="k1", access_token="t1", session=session)

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
        self.sent_messages.append(json.loads(message) if isinstance(message, str) else message)

    def close(self):
        pass


class TestZerodhaBrokerFeed(unittest.TestCase):
    def test_subscribe_feed_sends_instrument_tokens(self):
        holder = {}

        def factory(on_tick):
            ws = FakeWSClient(on_tick)
            holder["ws"] = ws
            return ws

        broker = ZerodhaBroker(api_key="k1", access_token="t1", ws_client_factory=factory)
        broker.subscribe_feed(
            instruments=[{"security_id": "738561", "exchange_segment": "NSE_EQ", "symbol": "RELIANCE"}],
            on_tick=lambda t: None,
        )
        ws = holder["ws"]
        self.assertEqual(ws.sent_messages[0]["v"], [738561])

    def test_subscribe_feed_without_factory_raises_explicit_error(self):
        """No default JSON WS client for Zerodha — must inject a KiteTicker-based wrapper."""
        broker = ZerodhaBroker(api_key="k1", access_token="t1")
        with self.assertRaises(NotImplementedError):
            broker.subscribe_feed(instruments=[{"security_id": "738561", "exchange_segment": "NSE_EQ", "symbol": "X"}],
                                    on_tick=lambda t: None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
