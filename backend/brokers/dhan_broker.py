"""
Dhan broker adapter — implements BaseBroker against Dhan's REST + WebSocket API.

IMPORTANT — verify before live use:
This sandbox has no network access, so these endpoint paths and payload field
names are implemented from Dhan's v2 API documentation structure as I know it,
NOT verified against a live call. Broker APIs change over time. Before running
this against a real account, diff these request/response shapes against the
current docs at https://dhanhq.co/docs/v2/ and adjust anything that has moved.
The test suite in tests/test_dhan_broker.py verifies our *wrapper logic* is
internally consistent (request construction, response parsing, error handling)
using mocked HTTP responses — it does NOT verify actual Dhan API compatibility.

Implemented directly against the REST API (via `requests`) rather than the
`dhanhq` PyPI package, since that package isn't installable here without
network access. The method names below map 1:1 to the likely `dhanhq` SDK
calls (noted in comments) if you'd rather swap to the official SDK once you
have a networked environment — the surrounding BaseBroker interface doesn't
change either way.
"""
import json
from datetime import datetime, timezone
from typing import Callable, List, Optional

import requests

from .base_broker import BaseBroker
from .dhan_feed import decode_market_data
from .models import (
    BrokerAPIError,
    BrokerConnectionError,
    Candle,
    ExchangeSegment,
    Holding,
    MarketDepthLevel,
    OrderRequest,
    OrderResponse,
    OrderStatus,
    OrderType,
    Position,
    ProductType,
    Quote,
    TransactionType,
)

DHAN_BASE_URL = "https://api.dhan.co/v2"
DHAN_FEED_WS_URL = "wss://api-feed.dhan.co"

_ORDER_STATUS_MAP = {
    "PENDING": OrderStatus.PENDING,
    "TRANSIT": OrderStatus.TRANSIT,
    "OPEN": OrderStatus.OPEN,
    "TRADED": OrderStatus.FILLED,
    "PART_TRADED": OrderStatus.PARTIALLY_FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
}


class DhanBroker(BaseBroker):

    def __init__(self, client_id: str, access_token: str, ws_client_factory: Optional[Callable] = None,
                 session: Optional[requests.Session] = None):
        """
        ws_client_factory: injectable factory that returns a websocket client object with
            .connect(url, on_message, on_error, on_close), .send(message), .close()
            (defaults lazily to `websocket-client`'s WebSocketApp at connect time;
             kept injectable so tests don't need that package installed).
        session: injectable requests.Session, so tests can mock HTTP calls without touching the real network.
        """
        self.client_id = client_id
        self.access_token = access_token
        self._session = session or requests.Session()
        self._ws_client_factory = ws_client_factory
        self._ws = None
        self._connected = False

    # ---------------------------------------------------------------- headers/helpers

    def _headers(self) -> dict:
        return {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        url = f"{DHAN_BASE_URL}{path}"
        try:
            resp = self._session.request(method, url, headers=self._headers(),
                                          data=json.dumps(payload) if payload is not None else None,
                                          params=params, timeout=10)
        except requests.RequestException as e:
            raise BrokerConnectionError(f"Dhan API request failed: {e}") from e

        if resp.status_code >= 400:
            raise BrokerAPIError(
                f"Dhan API error {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
                raw=self._safe_json(resp),
            )
        return self._safe_json(resp)

    @staticmethod
    def _safe_json(resp) -> dict:
        try:
            return resp.json()
        except ValueError:
            return {}

    # ---------------------------------------------------------------- connection lifecycle

    def connect(self) -> None:
        # Lightweight authenticated call to confirm the token/client-id pair is valid.
        # (Maps to dhanhq SDK: dhan.get_fund_limits() or similar profile/auth check.)
        self._request("GET", "/fundlimit")
        self._connected = True

    def disconnect(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None
        self._connected = False

    # ---------------------------------------------------------------- market data

    def get_quote(self, symbol: str, security_id: str, exchange_segment: str) -> Quote:
        # Maps to dhanhq SDK: dhan.quote_data(...) / dhan.ohlc_data(...)
        payload = {exchange_segment: [int(security_id)]}
        data = self._request("POST", "/marketfeed/quote", payload=payload)
        entry = data["data"][exchange_segment][security_id]
        depth = entry.get("depth", {})
        return Quote(
            symbol=symbol,
            ltp=float(entry["last_price"]),
            open=float(entry["ohlc"]["open"]),
            high=float(entry["ohlc"]["high"]),
            low=float(entry["ohlc"]["low"]),
            close=float(entry["ohlc"]["close"]),
            volume=int(entry.get("volume", 0)),
            timestamp=datetime.now(timezone.utc),
            bid_depth=[
                MarketDepthLevel(
                    quantity=int(level.get("quantity", 0)),
                    orders=int(level.get("orders", 0)),
                    price=float(level.get("price", 0)),
                )
                for level in depth.get("buy", [])
            ],
            ask_depth=[
                MarketDepthLevel(
                    quantity=int(level.get("quantity", 0)),
                    orders=int(level.get("orders", 0)),
                    price=float(level.get("price", 0)),
                )
                for level in depth.get("sell", [])
            ],
        )

    def get_historical_data(
        self, symbol: str, security_id: str, exchange_segment: str, timeframe: str,
        from_date: datetime, to_date: datetime,
    ) -> List[Candle]:
        # Maps to dhanhq SDK: dhan.intraday_minute_data(...) for intraday timeframes,
        # dhan.historical_daily_data(...) for daily.
        is_intraday = timeframe in ("1min", "3min", "5min", "15min", "30min", "60min")
        path = "/charts/intraday" if is_intraday else "/charts/historical"
        payload = {
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "instrument": "EQUITY",
            "fromDate": from_date.strftime("%Y-%m-%d"),
            "toDate": to_date.strftime("%Y-%m-%d"),
        }
        if is_intraday:
            payload["interval"] = timeframe.replace("min", "")

        data = self._request("POST", path, payload=payload)

        candles = []
        # Dhan returns parallel arrays: open[], high[], low[], close[], volume[], timestamp[]
        for i, ts in enumerate(data.get("timestamp", [])):
            candles.append(Candle(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                open=float(data["open"][i]),
                high=float(data["high"][i]),
                low=float(data["low"][i]),
                close=float(data["close"][i]),
                volume=int(data["volume"][i]),
            ))
        return candles

    def subscribe_feed(self, instruments: List[dict], on_tick: Callable[[dict], None]) -> None:
        # Maps to dhanhq SDK: dhanhq.marketfeed.DhanFeed(...).subscribe(...)
        if self._ws is None:
            self._ws = self._make_ws_client(on_tick)
            self._ws.connect(
                f"{DHAN_FEED_WS_URL}?version=2&token={self.access_token}"
                f"&clientId={self.client_id}&authType=2"
            )

        subscribe_msg = {
            "RequestCode": 15,   # subscribe (per Dhan feed protocol convention)
            "InstrumentCount": len(instruments),
            "InstrumentList": [
                {"ExchangeSegment": i["exchange_segment"], "SecurityId": i["security_id"]}
                for i in instruments
            ],
        }
        self._ws.send(json.dumps(subscribe_msg))

    def unsubscribe_feed(self, instruments: List[dict]) -> None:
        if self._ws is None:
            return
        unsubscribe_msg = {
            "RequestCode": 16,   # unsubscribe
            "InstrumentCount": len(instruments),
            "InstrumentList": [
                {"ExchangeSegment": i["exchange_segment"], "SecurityId": i["security_id"]}
                for i in instruments
            ],
        }
        self._ws.send(json.dumps(unsubscribe_msg))

    def _make_ws_client(self, on_tick: Callable[[dict], None]):
        if self._ws_client_factory is not None:
            return self._ws_client_factory(on_tick)
        # Real default: lazy-import websocket-client so this module still imports fine
        # in environments (like this sandbox) that don't have it installed.
        import websocket  # type: ignore

        def _on_message(ws, message):
            try:
                on_tick(decode_market_data(message))
            except (TypeError, ValueError, json.JSONDecodeError):
                return

        class _WSWrapper:
            def connect(self_inner, url):
                self_inner.app = websocket.WebSocketApp(url, on_message=_on_message)
                self_inner.app.run_forever()

            def send(self_inner, message):
                self_inner.app.send(message)

            def close(self_inner):
                self_inner.app.close()

        return _WSWrapper()

    # ---------------------------------------------------------------- orders

    def place_order(self, order: OrderRequest) -> OrderResponse:
        # Maps to dhanhq SDK: dhan.place_order(...)
        payload = self._order_payload(order)
        data = self._request("POST", "/orders", payload=payload)
        return self._parse_order_response(order, data)

    def place_super_order(self, order: OrderRequest) -> OrderResponse:
        # Maps to dhanhq SDK: dhan.place_super_order(...) — bracket order with attached SL/Target legs.
        if order.stop_loss_price is None or order.target_price is None:
            raise ValueError("Super order requires both stop_loss_price and target_price")

        payload = self._order_payload(order)
        payload.update({
            "targetPrice": order.target_price,
            "stopLossPrice": order.stop_loss_price,
        })
        if order.trailing_jump is not None:
            payload["trailingJump"] = order.trailing_jump

        data = self._request("POST", "/super/orders", payload=payload)
        return self._parse_order_response(order, data)

    def _order_payload(self, order: OrderRequest) -> dict:
        return {
            "transactionType": order.transaction_type.value,
            "exchangeSegment": order.exchange_segment.value,
            "productType": order.product_type.value,
            "orderType": order.order_type.value,
            "securityId": order.security_id,
            "quantity": order.quantity,
            "price": order.price,
            "triggerPrice": order.trigger_price,
            "disclosedQuantity": order.disclosed_quantity,
            "validity": order.validity,
        }

    def _parse_order_response(self, order: OrderRequest, data: dict) -> OrderResponse:
        return OrderResponse(
            order_id=str(data.get("orderId", "")),
            broker_order_id=str(data.get("orderId", "")),
            status=_ORDER_STATUS_MAP.get(data.get("orderStatus", ""), OrderStatus.UNKNOWN),
            symbol=order.symbol,
            quantity=order.quantity,
            filled_quantity=int(data.get("filledQty", 0)),
            average_price=float(data.get("averageTradedPrice", 0) or 0),
            raw=data,
        )

    def modify_order(self, order_id: str, **changes) -> OrderResponse:
        # Maps to dhanhq SDK: dhan.modify_order(...)
        payload = {"orderId": order_id, **changes}
        data = self._request("PUT", f"/orders/{order_id}", payload=payload)
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id,
            status=_ORDER_STATUS_MAP.get(data.get("orderStatus", ""), OrderStatus.UNKNOWN),
            symbol=data.get("tradingSymbol", ""), quantity=int(data.get("quantity", 0)),
            filled_quantity=int(data.get("filledQty", 0)),
            average_price=float(data.get("averageTradedPrice", 0) or 0), raw=data,
        )

    def cancel_order(self, order_id: str) -> OrderResponse:
        # Maps to dhanhq SDK: dhan.cancel_order(...)
        data = self._request("DELETE", f"/orders/{order_id}")
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id, status=OrderStatus.CANCELLED,
            symbol=data.get("tradingSymbol", ""), quantity=int(data.get("quantity", 0)),
            filled_quantity=int(data.get("filledQty", 0)), average_price=0.0, raw=data,
        )

    def get_order_status(self, order_id: str) -> OrderResponse:
        # Maps to dhanhq SDK: dhan.get_order_by_id(...)
        data = self._request("GET", f"/orders/{order_id}")
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id,
            status=_ORDER_STATUS_MAP.get(data.get("orderStatus", ""), OrderStatus.UNKNOWN),
            symbol=data.get("tradingSymbol", ""), quantity=int(data.get("quantity", 0)),
            filled_quantity=int(data.get("filledQty", 0)),
            average_price=float(data.get("averageTradedPrice", 0) or 0), raw=data,
        )

    # ---------------------------------------------------------------- portfolio

    def get_positions(self) -> List[Position]:
        # Maps to dhanhq SDK: dhan.get_positions()
        data = self._request("GET", "/positions")
        positions = []
        for p in data if isinstance(data, list) else data.get("data", []):
            positions.append(Position(
                symbol=p.get("tradingSymbol", ""),
                security_id=str(p.get("securityId", "")),
                exchange_segment=ExchangeSegment(p.get("exchangeSegment", "NSE_EQ")),
                product_type=ProductType(p.get("productType", "INTRADAY")),
                quantity=int(p.get("netQty", 0)),
                average_price=float(p.get("costPrice", 0) or 0),
                ltp=float(p.get("lastTradedPrice", 0) or 0),
                unrealized_pnl=float(p.get("unrealizedProfit", 0) or 0),
                realized_pnl=float(p.get("realizedProfit", 0) or 0),
            ))
        return positions

    def get_holdings(self) -> List[Holding]:
        # Maps to dhanhq SDK: dhan.get_holdings()
        data = self._request("GET", "/holdings")
        holdings = []
        for h in data if isinstance(data, list) else data.get("data", []):
            holdings.append(Holding(
                symbol=h.get("tradingSymbol", ""),
                security_id=str(h.get("securityId", "")),
                quantity=int(h.get("totalQty", 0)),
                average_price=float(h.get("avgCostPrice", 0) or 0),
                ltp=float(h.get("lastTradedPrice", 0) or 0),
            ))
        return holdings
