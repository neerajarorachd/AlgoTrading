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
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, List, Optional

import requests

logger = logging.getLogger(__name__)

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

# Dhan's standard rate-limit backoff: on a 429, wait 1s and retry once; if it's
# still rate-limited, wait 2s and retry once more before giving up.
RATE_LIMIT_RETRY_DELAYS_SEC = (1.0, 2.0)

# Proactive pacing between REST calls, so bursts (e.g. hydrating several
# registered symbols at startup) don't trigger 429s in the first place — the
# backoff above is then just a safety net, not the primary defense.
MIN_REQUEST_INTERVAL_SEC = 1.0

# Dhan's /charts/* (historical/intraday candle) endpoints turned out to have a
# materially stricter effective rate limit than the rest of the REST API —
# confirmed live (2026-09-14): a batch of get_historical_data calls spaced at
# MIN_REQUEST_INTERVAL_SEC (1s) failed 12/13 with a 400 "DH-907 ... no data
# present" error (not a 429, so the retry-on-429 backoff above never kicked
# in), while the exact same calls spaced 3s apart succeeded 100%. Given a
# generic error code rather than a proper 429, this needs its own slower
# proactive pacing rather than relying on the retry backoff to catch it.
MIN_CHART_REQUEST_INTERVAL_SEC = 3.0

# Live feed self-healing. Without it, one dropped connection silently ended
# the feed for the rest of the day (found live 2026-10-06: no connection to
# api-feed.dhan.co at all, LTP "—" everywhere, and the pattern engine
# processed nothing all session). run_forever(reconnect=...) reopens the
# socket; _on_ws_open re-sends every tracked subscription, since Dhan's
# server forgets them with the old connection.
#
# NO client-side pings: the first version sent one every 30s with a 10s
# pong timeout, and Dhan's server doesn't answer client pings -- the library
# itself tore down a healthy connection every ~49s (62 of 63 "drops" that
# afternoon were "ping/pong timed out"). Liveness comes from the data
# instead: _feed_watchdog forces a reconnect when messages WERE flowing and
# then stop for FEED_STALE_RECONNECT_SEC.
FEED_RECONNECT_DELAY_SEC = 5
FEED_STALE_RECONNECT_SEC = 90
FEED_WATCHDOG_INTERVAL_SEC = 15
# Dhan accepts at most 100 instruments per subscribe message.
FEED_MAX_INSTRUMENTS_PER_MESSAGE = 100

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
                 session: Optional[requests.Session] = None, sleep: Optional[Callable[[float], None]] = None,
                 clock: Optional[Callable[[], float]] = None):
        """
        ws_client_factory: injectable factory that returns a websocket client object with
            .connect(url, on_message, on_error, on_close), .send(message), .close()
            (defaults lazily to `websocket-client`'s WebSocketApp at connect time;
             kept injectable so tests don't need that package installed).
        session: injectable requests.Session, so tests can mock HTTP calls without touching the real network.
        sleep: injectable delay function (defaults to time.sleep), so tests exercising the
            rate-limit backoff/throttle below don't actually block for real seconds.
        clock: injectable monotonic time source (defaults to time.monotonic), so throttle
            tests can control elapsed time deterministically instead of racing the wall clock.
        """
        self.client_id = client_id
        self.access_token = access_token
        self._session = session or requests.Session()
        self._ws_client_factory = ws_client_factory
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._ws = None
        self._connected = False
        # Every instrument currently subscribed on the live feed, keyed by
        # (exchange_segment, security_id) -- re-sent on each (re)connect.
        self._subscribed: dict = {}
        self._stopping = False  # True after a deliberate disconnect(), so closes aren't logged as drops
        # Live-feed health, read by GET /api/feed/status.
        self._feed_connected = False
        self._feed_connected_at: Optional[datetime] = None
        self._feed_last_message_at: Optional[datetime] = None
        self._feed_disconnects = 0
        self._feed_opens_this_session = 0
        self._feed_ever_received = False
        self._watchdog_started = False
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0
        self._chart_request_lock = threading.Lock()
        self._last_chart_request_at = 0.0

    # ---------------------------------------------------------------- headers/helpers

    def _headers(self) -> dict:
        return {
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
        }

    def _throttle(self, path: str) -> None:
        """Serializes REST calls with a minimum gap between them.

        Holding the lock across the wait (not just the timestamp update) is
        what makes this an actual queue rather than a best-effort check —
        concurrent callers (e.g. hydrating several symbols) block here one at
        a time, each waiting out whatever gap is left when its turn comes.

        /charts/* (historical/intraday candles) gets its own lock/timestamp
        and a longer minimum interval — a materially stricter rate limit
        than the rest of the REST API, confirmed live (see
        MIN_CHART_REQUEST_INTERVAL_SEC's own comment). Kept as a separate
        lock, not just a different interval under the same one, so a chart
        call's longer wait never blocks an unrelated quote/order call stuck
        behind the same lock.
        """
        if path.startswith("/charts/"):
            lock, last_at_attr, interval = self._chart_request_lock, "_last_chart_request_at", MIN_CHART_REQUEST_INTERVAL_SEC
        else:
            lock, last_at_attr, interval = self._request_lock, "_last_request_at", MIN_REQUEST_INTERVAL_SEC

        with lock:
            wait = interval - (self._clock() - getattr(self, last_at_attr))
            if wait > 0:
                self._sleep(wait)
            setattr(self, last_at_attr, self._clock())

    def _request(self, method: str, path: str, payload: Optional[dict] = None, params: Optional[dict] = None) -> dict:
        url = f"{DHAN_BASE_URL}{path}"
        remaining_retry_delays = list(RATE_LIMIT_RETRY_DELAYS_SEC)

        while True:
            self._throttle(path)
            try:
                resp = self._session.request(method, url, headers=self._headers(),
                                              data=json.dumps(payload) if payload is not None else None,
                                              params=params, timeout=10)
            except requests.RequestException as e:
                raise BrokerConnectionError(f"Dhan API request failed: {e}") from e

            if resp.status_code == 429 and remaining_retry_delays:
                self._sleep(remaining_retry_delays.pop(0))
                continue

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
        # State is cleared BEFORE attempting ws.close(), not after -- a raise
        # from close() (e.g. called from a thread other than the one running
        # run_forever(), a documented risk for a market-hours session
        # scheduler that connects/disconnects instead of running forever)
        # must not leave stale _ws/_connected state behind, or the next
        # connect_feed() call reuses a dead websocket object instead of
        # opening a fresh one.
        ws, self._ws, self._connected = self._ws, None, False
        # A deliberate stop ends the session: forget subscriptions so the next
        # session's connect doesn't re-subscribe everything on open, ahead of
        # hydration's backfill-then-subscribe ordering (feed/gap_fill.py).
        # Only an unexpected drop keeps them, for the reconnect to restore.
        self._stopping = True
        self._subscribed = {}
        self._feed_connected = False
        if ws is not None:
            try:
                ws.close()
            except Exception:
                logger.warning("DhanBroker.disconnect: ws.close() raised, ignoring (state already cleared)")

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
        # Track BEFORE sending: if the socket is mid-reconnect the send below
        # fails, but _on_ws_open re-sends everything tracked once it's back.
        for i in instruments:
            self._subscribed[(i["exchange_segment"], str(i["security_id"]))] = {
                "exchange_segment": i["exchange_segment"], "security_id": str(i["security_id"]),
            }

        if self._ws is None:
            self._stopping = False
            self._feed_opens_this_session = 0
            self._feed_ever_received = False
            self._ws = self._make_ws_client(on_tick)
            if self._ws_client_factory is None:  # real socket only -- test fakes drive health by hand
                self._start_feed_watchdog()
            self._ws.connect(
                f"{DHAN_FEED_WS_URL}?version=2&token={self.access_token}"
                f"&clientId={self.client_id}&authType=2"
            )

        if instruments:
            self._send_feed_request(21, instruments)

    def unsubscribe_feed(self, instruments: List[dict]) -> None:
        for i in instruments:
            self._subscribed.pop((i["exchange_segment"], str(i["security_id"])), None)
        if self._ws is None or not instruments:
            return
        self._send_feed_request(16, instruments)  # 16 = unsubscribe

    def _send_feed_request(self, request_code: int, instruments: List[dict]) -> None:
        """21 = Subscribe Full (OHLC/volume plus 5-level depth in one packet,
        type 8; 15/Ticker omits depth entirely, which is why depth never
        populated before), 16 = unsubscribe. Chunked to Dhan's 100-per-message
        limit. A send on a dropped socket is logged, not raised -- the
        reconnect path re-subscribes everything tracked."""
        for start in range(0, len(instruments), FEED_MAX_INSTRUMENTS_PER_MESSAGE):
            chunk = instruments[start:start + FEED_MAX_INSTRUMENTS_PER_MESSAGE]
            message = {
                "RequestCode": request_code,
                "InstrumentCount": len(chunk),
                "InstrumentList": [
                    {"ExchangeSegment": i["exchange_segment"], "SecurityId": i["security_id"]}
                    for i in chunk
                ],
            }
            try:
                self._ws.send(json.dumps(message))
            except Exception as e:
                logger.warning("Dhan feed: request %d for %d instrument(s) not sent (%s) -- will apply on reconnect",
                               request_code, len(chunk), e)

    # ---------------------------------------------------------------- live-feed health

    def _on_ws_open(self) -> None:
        # Disconnects are counted HERE, as "opened again": verified against
        # the real library (2026-10-06, local test server), a clean
        # server-side close (code 1000) goes straight to reconnect without
        # calling on_error or on_close -- a re-open is the one signal every
        # drop path is guaranteed to produce.
        if self._feed_opens_this_session > 0:
            self._feed_disconnects += 1
            logger.warning("Dhan feed: reconnected after a drop (#%d this session)", self._feed_disconnects)
        self._feed_opens_this_session += 1
        self._feed_connected = True
        self._feed_connected_at = datetime.now(timezone.utc)
        logger.info("Dhan feed: connected (subscribing %d tracked instrument(s))", len(self._subscribed))
        if self._subscribed:
            self._send_feed_request(21, list(self._subscribed.values()))

    def _on_ws_close(self, detail: str) -> None:
        self._feed_connected = False
        if self._stopping:
            logger.info("Dhan feed: closed (stopped)")
            return
        logger.warning("Dhan feed: connection lost (%s) -- reconnecting in %ds", detail, FEED_RECONNECT_DELAY_SEC)

    def _on_ws_message(self) -> None:
        self._feed_last_message_at = datetime.now(timezone.utc)
        self._feed_connected = True  # a message proves the link is up
        self._feed_ever_received = True

    def _start_feed_watchdog(self) -> None:
        if self._watchdog_started:
            return
        self._watchdog_started = True

        def _run():
            while True:
                time.sleep(FEED_WATCHDOG_INTERVAL_SEC)  # real sleep: self._sleep is the REST throttle's injectable one
                try:
                    self._check_feed_stale()
                except Exception:
                    logger.exception("Dhan feed watchdog: check failed")

        threading.Thread(target=_run, daemon=True, name="dhan-feed-watchdog").start()

    def _check_feed_stale(self, now: Optional[datetime] = None) -> bool:
        """Forces a reconnect (returns True) when data WAS flowing this
        session and nothing has arrived for FEED_STALE_RECONNECT_SEC since
        the later of the last message and the last (re)connect. Never fires
        before the first message of a session (e.g. 08:50-09:15, before the
        open) or after a deliberate stop."""
        if self._stopping or self._ws is None or not self._subscribed or not self._feed_ever_received:
            return False
        reference = max(t for t in (self._feed_last_message_at, self._feed_connected_at) if t is not None)
        silent_for = ((now or datetime.now(timezone.utc)) - reference).total_seconds()
        if silent_for < FEED_STALE_RECONNECT_SEC:
            return False
        force = getattr(self._ws, "force_reconnect", None)
        if force is None:
            return False
        logger.warning("Dhan feed: no data for %ds -- forcing a reconnect", silent_for)
        self._feed_connected = False
        force()
        return True

    def feed_status(self) -> dict:
        def iso(ts):
            return ts.isoformat().replace("+00:00", "Z") if ts else None
        return {
            "connected": self._feed_connected,
            "connected_at": iso(self._feed_connected_at),
            "last_message_at": iso(self._feed_last_message_at),
            "disconnects": self._feed_disconnects,
            "subscribed": len(self._subscribed),
        }

    def _make_ws_client(self, on_tick: Callable[[dict], None]):
        if self._ws_client_factory is not None:
            return self._ws_client_factory(on_tick)
        # Real default: lazy-import websocket-client so this module still imports fine
        # in environments (like this sandbox) that don't have it installed.
        import websocket  # type: ignore

        broker = self

        def _on_message(ws, message):
            broker._on_ws_message()
            try:
                on_tick(decode_market_data(message))
            except (TypeError, ValueError, json.JSONDecodeError):
                return

        def _on_open(ws):
            broker._on_ws_open()

        def _on_close(ws, status_code, reason):
            broker._on_ws_close(f"code={status_code} reason={reason!r}")

        def _on_error(ws, error):
            # With reconnect enabled, websocket-client reports a dropped link
            # via on_error only -- on_close fires just on a final teardown --
            # so connection-type errors must count as a disconnect here, or
            # feed_status() would say "connected" through an outage. Other
            # errors (an exception raised inside a callback) are just logged.
            if isinstance(error, (websocket.WebSocketException, ConnectionError, OSError, TimeoutError)):
                broker._on_ws_close(f"{type(error).__name__}: {error}")
            else:
                logger.warning("Dhan feed: callback error: %s", error)

        class _WSWrapper:
            def connect(self_inner, url):
                self_inner.app = websocket.WebSocketApp(
                    url, on_open=_on_open, on_message=_on_message, on_close=_on_close, on_error=_on_error,
                )
                # Blocks for the feed's whole life: reconnect=N reopens the
                # socket N seconds after any drop (on_open then re-subscribes).
                # Only an explicit close() (disconnect()) ends the loop. No
                # ping_interval on purpose -- see FEED_STALE_RECONNECT_SEC.
                self_inner.app.run_forever(reconnect=FEED_RECONNECT_DELAY_SEC)

            def force_reconnect(self_inner):
                # Closing the underlying socket (not app.close(), which would
                # also stop the run_forever loop) makes the read loop fail and
                # go through its normal reconnect path.
                sock = getattr(getattr(self_inner, "app", None), "sock", None)
                if sock is not None:
                    sock.close()

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
