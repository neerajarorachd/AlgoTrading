"""
Upstox broker adapter — implements BaseBroker.

IMPORTANT — verify before live use (real uncertainty flagged explicitly):

1. NO NETWORK ACCESS IN THIS SANDBOX. Endpoint paths/field names follow
   Upstox's v2 REST API structure as I know it — not verified against a
   live call. Verify against https://upstox.com/developer/api-documentation/
   before running against a real account, as this is one of the areas most
   likely to have changed since my training.

2. BRACKET/SUPER ORDER EQUIVALENT — Upstox's mechanism for an attached
   SL+Target is (to my knowledge) their GTT (Good Till Triggered) order type,
   which takes a list of rules (ENTRY/TARGET/STOPLOSS) rather than a single
   "place order with SL/target fields" call like Dhan's super order. I'm
   implementing `place_super_order` against that GTT-rules shape, but I hold
   this with LOWER confidence than the Dhan/Zerodha equivalents — confirm the
   current GTT API schema before depending on it. If GTT isn't suitable,
   the fallback is the same as noted for Zerodha: place a normal entry order
   and let this system's own Monitoring Engine (HLD §4.8) manage the SL/
   Target via its own price-watching + counter-order logic.

Implemented directly against the REST API (via `requests`) rather than any
Upstox Python SDK, since no package can be installed here without network
access. Method names map to the likely Upstox SDK/API call names (noted in
comments).
"""
from datetime import datetime, timezone
from typing import Callable, List, Optional

import requests

from .base_broker import BaseBroker
from .models import (
    BrokerAPIError,
    BrokerConnectionError,
    Candle,
    ExchangeSegment,
    Holding,
    OrderRequest,
    OrderResponse,
    OrderStatus,
    OrderType,
    Position,
    ProductType,
    Quote,
    TransactionType,
)

UPSTOX_BASE_URL = "https://api.upstox.com/v2"

_ORDER_STATUS_MAP = {
    "open": OrderStatus.OPEN,
    "complete": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "trigger pending": OrderStatus.PENDING,
    "pending": OrderStatus.PENDING,
}

_ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_LOSS: "SL",
    OrderType.STOP_LOSS_MARKET: "SL-M",
}

_PRODUCT_MAP = {
    ProductType.INTRADAY: "I",
    ProductType.DELIVERY: "D",
    ProductType.MARGIN: "M",
}

_INTERVAL_MAP = {
    "1min": "1minute", "3min": "3minute", "5min": "5minute",
    "15min": "15minute", "30min": "30minute", "60min": "60minute", "1day": "day",
}


class UpstoxBroker(BaseBroker):

    def __init__(self, access_token: str, ws_client_factory: Optional[Callable] = None,
                 session: Optional[requests.Session] = None):
        self.access_token = access_token
        self._session = session or requests.Session()
        self._ws_client_factory = ws_client_factory
        self._ws = None
        self._connected = False

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, payload: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        url = f"{UPSTOX_BASE_URL}{path}"
        try:
            resp = self._session.request(method, url, headers=self._headers(),
                                          json=payload, params=params, timeout=10)
        except requests.RequestException as e:
            raise BrokerConnectionError(f"Upstox API request failed: {e}") from e

        if resp.status_code >= 400:
            raise BrokerAPIError(
                f"Upstox API error {resp.status_code}: {resp.text}",
                status_code=resp.status_code, raw=self._safe_json(resp),
            )
        return self._safe_json(resp)

    @staticmethod
    def _safe_json(resp) -> dict:
        try:
            return resp.json()
        except ValueError:
            return {}

    # ---------------------------------------------------------------- connection

    def connect(self) -> None:
        self._request("GET", "/user/profile")
        self._connected = True

    def disconnect(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None
        self._connected = False

    # ---------------------------------------------------------------- market data

    def get_quote(self, symbol: str, security_id: str, exchange_segment: str) -> Quote:
        # security_id here = Upstox's `instrument_key`, e.g. "NSE_EQ|INE002A01018"
        data = self._request("GET", "/market-quote/ltp", params={"instrument_key": security_id})
        entry = next(iter(data.get("data", {}).values()), {})
        return Quote(
            symbol=symbol, ltp=float(entry.get("last_price", 0) or 0),
            open=0.0, high=0.0, low=0.0, close=0.0,   # ltp endpoint doesn't return OHLC; use /market-quote/ohlc if needed
            volume=0, timestamp=datetime.now(timezone.utc),
        )

    def get_historical_data(
        self, symbol: str, security_id: str, exchange_segment: str, timeframe: str,
        from_date: datetime, to_date: datetime,
    ) -> List[Candle]:
        interval = _INTERVAL_MAP.get(timeframe, "1minute")
        path = f"/historical-candle/{security_id}/{interval}/{to_date.strftime('%Y-%m-%d')}/{from_date.strftime('%Y-%m-%d')}"
        data = self._request("GET", path)

        candles = []
        for row in data.get("data", {}).get("candles", []):
            # Upstox returns rows: [timestamp_iso, open, high, low, close, volume, open_interest]
            candles.append(Candle(
                symbol=symbol, timeframe=timeframe,
                timestamp=datetime.fromisoformat(row[0]),
                open=float(row[1]), high=float(row[2]), low=float(row[3]),
                close=float(row[4]), volume=int(row[5]),
            ))
        return candles

    def subscribe_feed(self, instruments: List[dict], on_tick: Callable[[dict], None]) -> None:
        if self._ws is None:
            self._ws = self._make_ws_client(on_tick)
            self._ws.connect("wss://api.upstox.com/v2/feed/market-data-feed")
        keys = [i["security_id"] for i in instruments]
        self._ws.send({"guid": "sub-1", "method": "sub", "data": {"mode": "full", "instrumentKeys": keys}})

    def unsubscribe_feed(self, instruments: List[dict]) -> None:
        if self._ws is None:
            return
        keys = [i["security_id"] for i in instruments]
        self._ws.send({"guid": "unsub-1", "method": "unsub", "data": {"instrumentKeys": keys}})

    def _make_ws_client(self, on_tick: Callable[[dict], None]):
        if self._ws_client_factory is not None:
            return self._ws_client_factory(on_tick)
        raise NotImplementedError(
            "No ws_client_factory provided. Upstox's live feed uses protobuf-encoded "
            "binary messages over WebSocket, not plain JSON — inject a wrapper that "
            "decodes using Upstox's published .proto schema rather than assuming JSON."
        )

    # ---------------------------------------------------------------- orders

    def place_order(self, order: OrderRequest) -> OrderResponse:
        payload = self._order_payload(order)
        data = self._request("POST", "/order/place", payload=payload)
        order_id = str(data.get("data", {}).get("order_id", ""))
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id, status=OrderStatus.PENDING,
            symbol=order.symbol, quantity=order.quantity, filled_quantity=0,
            average_price=0.0, raw=data,
        )

    def place_super_order(self, order: OrderRequest) -> OrderResponse:
        # See module docstring point 2 — GTT rule schema needs verification.
        if order.stop_loss_price is None or order.target_price is None:
            raise ValueError("Super order requires both stop_loss_price and target_price")

        payload = {
            "instrument_token": order.security_id,
            "transaction_type": order.transaction_type.value,
            "quantity": order.quantity,
            "product": _PRODUCT_MAP.get(order.product_type, "I"),
            "type": "MULTIPLE",
            "rules": [
                {"strategy": "ENTRY", "trigger_type": "IMMEDIATE", "trigger_price": order.price},
                {"strategy": "TARGET", "trigger_type": "ABOVE" if order.transaction_type == TransactionType.BUY else "BELOW",
                 "trigger_price": order.target_price},
                {"strategy": "STOPLOSS", "trigger_type": "BELOW" if order.transaction_type == TransactionType.BUY else "ABOVE",
                 "trigger_price": order.stop_loss_price},
            ],
        }
        data = self._request("POST", "/order/gtt/place", payload=payload)
        order_id = str(data.get("data", {}).get("gtt_order_id", ""))
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id, status=OrderStatus.PENDING,
            symbol=order.symbol, quantity=order.quantity, filled_quantity=0,
            average_price=0.0, raw=data,
        )

    def _order_payload(self, order: OrderRequest) -> dict:
        return {
            "instrument_token": order.security_id,
            "transaction_type": order.transaction_type.value,
            "order_type": _ORDER_TYPE_MAP.get(order.order_type, "MARKET"),
            "quantity": order.quantity,
            "product": _PRODUCT_MAP.get(order.product_type, "I"),
            "price": order.price,
            "trigger_price": order.trigger_price,
            "disclosed_quantity": order.disclosed_quantity,
            "validity": order.validity,
            "is_amo": False,
        }

    def modify_order(self, order_id: str, **changes) -> OrderResponse:
        payload = {"order_id": order_id, **changes}
        data = self._request("PUT", "/order/modify", payload=payload)
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id, status=OrderStatus.PENDING,
            symbol="", quantity=0, filled_quantity=0, average_price=0.0, raw=data,
        )

    def cancel_order(self, order_id: str) -> OrderResponse:
        data = self._request("DELETE", "/order/cancel", params={"order_id": order_id})
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id, status=OrderStatus.CANCELLED,
            symbol="", quantity=0, filled_quantity=0, average_price=0.0, raw=data,
        )

    def get_order_status(self, order_id: str) -> OrderResponse:
        data = self._request("GET", "/order/details", params={"order_id": order_id})
        entry = data.get("data", {})
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id,
            status=_ORDER_STATUS_MAP.get(str(entry.get("status", "")).lower(), OrderStatus.UNKNOWN),
            symbol=entry.get("trading_symbol", ""),
            quantity=int(entry.get("quantity", 0)),
            filled_quantity=int(entry.get("filled_quantity", 0)),
            average_price=float(entry.get("average_price", 0) or 0), raw=data,
        )

    # ---------------------------------------------------------------- portfolio

    def get_positions(self) -> List[Position]:
        data = self._request("GET", "/portfolio/short-term-positions")
        positions = []
        for p in data.get("data", []):
            positions.append(Position(
                symbol=p.get("trading_symbol", ""), security_id=str(p.get("instrument_token", "")),
                exchange_segment=ExchangeSegment.NSE_EQ,
                product_type=ProductType.INTRADAY if p.get("product") == "I" else ProductType.DELIVERY,
                quantity=int(p.get("quantity", 0)), average_price=float(p.get("average_price", 0) or 0),
                ltp=float(p.get("last_price", 0) or 0),
                unrealized_pnl=float(p.get("unrealised", 0) or 0),
                realized_pnl=float(p.get("realised", 0) or 0),
            ))
        return positions

    def get_holdings(self) -> List[Holding]:
        data = self._request("GET", "/portfolio/long-term-holdings")
        holdings = []
        for h in data.get("data", []):
            holdings.append(Holding(
                symbol=h.get("trading_symbol", ""), security_id=str(h.get("instrument_token", "")),
                quantity=int(h.get("quantity", 0)), average_price=float(h.get("average_price", 0) or 0),
                ltp=float(h.get("last_price", 0) or 0),
            ))
        return holdings
