"""
Zerodha (Kite Connect) broker adapter — implements BaseBroker.

IMPORTANT — verify before live use (more uncertainty here than Dhan):

1. NO NETWORK ACCESS IN THIS SANDBOX. Endpoint paths/field names below follow
   Kite Connect's REST API structure as I know it — not verified against a
   live call. Verify against https://kite.trade/docs/connect/v3/ before
   running against a real account.

2. BRACKET ORDERS (the "super order" equivalent) — SEBI's 2020/2021 margin
   framework changes led Zerodha to discontinue Bracket Orders (BO) and
   Cover Orders (CO) for retail intraday equity trading. I'm flagging this
   explicitly rather than confidently implementing a `variety=bo` call as if
   it definitely still works: it may not be available at all for your
   account/segment. `place_super_order()` below attempts the BO-style call
   as documented historically, but the realistic fallback — and what you
   should plan for — is: place a normal entry order, and let this system's
   own Monitoring Engine (see HLD §4.8) manage the SL/Target by watching
   price and firing counter orders itself, rather than relying on a
   broker-native bracket order for Zerodha specifically. Confirm current BO
   availability for your account before depending on the native path.

3. LIVE FEED PROTOCOL — Kite's real-time ticker (KiteTicker) uses a compact
   BINARY WebSocket protocol, not JSON. Hand-rolling a binary tick parser
   from memory carries real risk of silently producing garbage prices if I
   get the byte layout wrong. Rather than guess at that, `subscribe_feed`/
   `unsubscribe_feed` here follow the same *interface* (instruments list +
   on_tick callback) as Dhan for consistency with BaseBroker, but the
   feed methods are designed to delegate to the official `kiteconnect`
   package's `KiteTicker` class (via the injectable `ws_client_factory`)
   once you have network access — do not trust a hand-rolled binary parser
   for this without testing it against real ticks first.

Implemented directly against the REST API (via `requests`) rather than the
`kiteconnect` PyPI package, since that package isn't installable here without
network access. Method names map 1:1 to likely `kiteconnect.KiteConnect` SDK
calls (noted in comments).
"""
import json
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

KITE_BASE_URL = "https://api.kite.trade"

_ORDER_STATUS_MAP = {
    "OPEN": OrderStatus.OPEN,
    "COMPLETE": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "TRIGGER PENDING": OrderStatus.PENDING,
}

_ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_LOSS: "SL",
    OrderType.STOP_LOSS_MARKET: "SL-M",
}

_PRODUCT_MAP = {
    ProductType.INTRADAY: "MIS",
    ProductType.DELIVERY: "CNC",
    ProductType.MARGIN: "NRML",
}

_INTERVAL_MAP = {
    "1min": "minute", "3min": "3minute", "5min": "5minute",
    "15min": "15minute", "30min": "30minute", "60min": "60minute", "1day": "day",
}


class ZerodhaBroker(BaseBroker):

    def __init__(self, api_key: str, access_token: str, ws_client_factory: Optional[Callable] = None,
                 session: Optional[requests.Session] = None):
        self.api_key = api_key
        self.access_token = access_token
        self._session = session or requests.Session()
        self._ws_client_factory = ws_client_factory
        self._ws = None
        self._connected = False

    def _headers(self) -> dict:
        return {
            "Authorization": f"token {self.api_key}:{self.access_token}",
            "X-Kite-Version": "3",
        }

    def _request(self, method: str, path: str, payload: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        url = f"{KITE_BASE_URL}{path}"
        try:
            # Kite Connect order endpoints take form-encoded data, not JSON — using `data=` not `json=`.
            resp = self._session.request(method, url, headers=self._headers(),
                                          data=payload, params=params, timeout=10)
        except requests.RequestException as e:
            raise BrokerConnectionError(f"Zerodha API request failed: {e}") from e

        if resp.status_code >= 400:
            raise BrokerAPIError(
                f"Zerodha API error {resp.status_code}: {resp.text}",
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
        # Maps to kiteconnect SDK: kite.profile()
        self._request("GET", "/user/profile")
        self._connected = True

    def disconnect(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None
        self._connected = False

    # ---------------------------------------------------------------- market data

    def get_quote(self, symbol: str, security_id: str, exchange_segment: str) -> Quote:
        # Maps to kiteconnect SDK: kite.quote(...)
        exchange = exchange_segment.replace("_EQ", "").replace("_FNO", "")
        instrument = f"{exchange}:{symbol}"
        data = self._request("GET", "/quote", params={"i": instrument})
        entry = data["data"][instrument]
        ohlc = entry["ohlc"]
        return Quote(
            symbol=symbol, ltp=float(entry["last_price"]),
            open=float(ohlc["open"]), high=float(ohlc["high"]),
            low=float(ohlc["low"]), close=float(ohlc["close"]),
            volume=int(entry.get("volume", 0)), timestamp=datetime.now(timezone.utc),
        )

    def get_historical_data(
        self, symbol: str, security_id: str, exchange_segment: str, timeframe: str,
        from_date: datetime, to_date: datetime,
    ) -> List[Candle]:
        # Maps to kiteconnect SDK: kite.historical_data(instrument_token, from_date, to_date, interval)
        # security_id here = Kite's numeric `instrument_token` (must be resolved from the instruments dump).
        interval = _INTERVAL_MAP.get(timeframe, "minute")
        path = f"/instruments/historical/{security_id}/{interval}"
        params = {"from": from_date.strftime("%Y-%m-%d"), "to": to_date.strftime("%Y-%m-%d")}
        data = self._request("GET", path, params=params)

        candles = []
        for row in data.get("data", {}).get("candles", []):
            # Kite returns rows: [timestamp_iso, open, high, low, close, volume]
            candles.append(Candle(
                symbol=symbol, timeframe=timeframe,
                timestamp=datetime.fromisoformat(row[0]),
                open=float(row[1]), high=float(row[2]), low=float(row[3]),
                close=float(row[4]), volume=int(row[5]),
            ))
        return candles

    def subscribe_feed(self, instruments: List[dict], on_tick: Callable[[dict], None]) -> None:
        # See module docstring point 3 — real implementation should use kiteconnect's
        # KiteTicker (binary protocol) rather than a hand-rolled parser.
        if self._ws is None:
            self._ws = self._make_ws_client(on_tick)
            self._ws.connect(f"wss://ws.kite.trade?api_key={self.api_key}&access_token={self.access_token}")
        tokens = [int(i["security_id"]) for i in instruments]
        self._ws.send(json.dumps({"a": "subscribe", "v": tokens}))

    def unsubscribe_feed(self, instruments: List[dict]) -> None:
        if self._ws is None:
            return
        tokens = [int(i["security_id"]) for i in instruments]
        self._ws.send(json.dumps({"a": "unsubscribe", "v": tokens}))

    def _make_ws_client(self, on_tick: Callable[[dict], None]):
        if self._ws_client_factory is not None:
            return self._ws_client_factory(on_tick)
        raise NotImplementedError(
            "No ws_client_factory provided. For real use, inject a wrapper around "
            "kiteconnect.KiteTicker (which handles Kite's binary tick protocol) "
            "rather than relying on a default JSON-based client — see module docstring."
        )

    # ---------------------------------------------------------------- orders

    def place_order(self, order: OrderRequest) -> OrderResponse:
        # Maps to kiteconnect SDK: kite.place_order(variety="regular", ...)
        payload = self._order_payload(order)
        data = self._request("POST", "/orders/regular", payload=payload)
        return self._parse_place_response(order, data)

    def place_super_order(self, order: OrderRequest) -> OrderResponse:
        # See module docstring point 2 — BO availability must be verified for your account.
        if order.stop_loss_price is None or order.target_price is None:
            raise ValueError("Super order requires both stop_loss_price and target_price")

        payload = self._order_payload(order)
        payload.update({
            "squareoff": str(round(order.target_price - order.price, 2)),
            "stoploss": str(round(order.price - order.stop_loss_price, 2)),
        })
        if order.trailing_jump is not None:
            payload["trailing_stoploss"] = str(order.trailing_jump)

        data = self._request("POST", "/orders/bo", payload=payload)
        return self._parse_place_response(order, data)

    def _order_payload(self, order: OrderRequest) -> dict:
        return {
            "tradingsymbol": order.symbol,
            "exchange": order.exchange_segment.value.replace("_EQ", "").replace("_FNO", ""),
            "transaction_type": order.transaction_type.value,
            "order_type": _ORDER_TYPE_MAP.get(order.order_type, "MARKET"),
            "quantity": order.quantity,
            "product": _PRODUCT_MAP.get(order.product_type, "MIS"),
            "price": order.price,
            "trigger_price": order.trigger_price,
            "disclosed_quantity": order.disclosed_quantity,
            "validity": order.validity,
        }

    def _parse_place_response(self, order: OrderRequest, data: dict) -> OrderResponse:
        order_id = str(data.get("data", {}).get("order_id", ""))
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id,
            status=OrderStatus.PENDING,   # Kite's place_order response doesn't include fill status; requires a follow-up get_order_status call
            symbol=order.symbol, quantity=order.quantity,
            filled_quantity=0, average_price=0.0, raw=data,
        )

    def modify_order(self, order_id: str, **changes) -> OrderResponse:
        # Maps to kiteconnect SDK: kite.modify_order(variety="regular", order_id=..., ...)
        data = self._request("PUT", f"/orders/regular/{order_id}", payload=changes)
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id, status=OrderStatus.PENDING,
            symbol="", quantity=0, filled_quantity=0, average_price=0.0, raw=data,
        )

    def cancel_order(self, order_id: str) -> OrderResponse:
        # Maps to kiteconnect SDK: kite.cancel_order(variety="regular", order_id=...)
        data = self._request("DELETE", f"/orders/regular/{order_id}")
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id, status=OrderStatus.CANCELLED,
            symbol="", quantity=0, filled_quantity=0, average_price=0.0, raw=data,
        )

    def get_order_status(self, order_id: str) -> OrderResponse:
        # Maps to kiteconnect SDK: kite.order_history(order_id) — takes the LAST entry as current status
        data = self._request("GET", f"/orders/{order_id}")
        history = data.get("data", [])
        latest = history[-1] if history else {}
        return OrderResponse(
            order_id=order_id, broker_order_id=order_id,
            status=_ORDER_STATUS_MAP.get(latest.get("status", ""), OrderStatus.UNKNOWN),
            symbol=latest.get("tradingsymbol", ""),
            quantity=int(latest.get("quantity", 0)),
            filled_quantity=int(latest.get("filled_quantity", 0)),
            average_price=float(latest.get("average_price", 0) or 0), raw=data,
        )

    # ---------------------------------------------------------------- portfolio

    def get_positions(self) -> List[Position]:
        # Maps to kiteconnect SDK: kite.positions()["net"]
        data = self._request("GET", "/portfolio/positions")
        positions = []
        for p in data.get("data", {}).get("net", []):
            positions.append(Position(
                symbol=p.get("tradingsymbol", ""), security_id=str(p.get("instrument_token", "")),
                exchange_segment=ExchangeSegment.NSE_EQ,
                product_type=ProductType.INTRADAY if p.get("product") == "MIS" else ProductType.DELIVERY,
                quantity=int(p.get("quantity", 0)), average_price=float(p.get("average_price", 0) or 0),
                ltp=float(p.get("last_price", 0) or 0),
                unrealized_pnl=float(p.get("unrealised", 0) or 0),
                realized_pnl=float(p.get("realised", 0) or 0),
            ))
        return positions

    def get_holdings(self) -> List[Holding]:
        # Maps to kiteconnect SDK: kite.holdings()
        data = self._request("GET", "/portfolio/holdings")
        holdings = []
        for h in data.get("data", []):
            holdings.append(Holding(
                symbol=h.get("tradingsymbol", ""), security_id=str(h.get("instrument_token", "")),
                quantity=int(h.get("quantity", 0)), average_price=float(h.get("average_price", 0) or 0),
                ltp=float(h.get("last_price", 0) or 0),
            ))
        return holdings
