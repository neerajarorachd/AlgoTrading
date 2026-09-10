"""
Abstract broker interface. Every concrete broker adapter (DhanBroker now,
ZerodhaBroker / UpstoxBroker later) implements this exact interface, so
nothing else in the system (feed pipeline, order engine, ledger) needs to
know or care which broker is underneath.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable, List, Optional

from .models import Candle, Holding, OrderRequest, OrderResponse, Position, Quote


class BaseBroker(ABC):

    # ---- connection lifecycle ----
    @abstractmethod
    def connect(self) -> None:
        """Authenticate / establish whatever session state the broker requires."""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    # ---- market data ----
    @abstractmethod
    def get_quote(self, symbol: str, security_id: str, exchange_segment: str) -> Quote:
        raise NotImplementedError

    @abstractmethod
    def get_historical_data(
        self,
        symbol: str,
        security_id: str,
        exchange_segment: str,
        timeframe: str,
        from_date: datetime,
        to_date: datetime,
    ) -> List[Candle]:
        raise NotImplementedError

    @abstractmethod
    def subscribe_feed(self, instruments: List[dict], on_tick: Callable[[dict], None]) -> None:
        """instruments: list of {security_id, exchange_segment, symbol}."""
        raise NotImplementedError

    @abstractmethod
    def unsubscribe_feed(self, instruments: List[dict]) -> None:
        raise NotImplementedError

    # ---- orders ----
    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResponse:
        raise NotImplementedError

    @abstractmethod
    def place_super_order(self, order: OrderRequest) -> OrderResponse:
        """Bracket-style order: entry + attached stop-loss + target legs, managed by the broker."""
        raise NotImplementedError

    @abstractmethod
    def modify_order(self, order_id: str, **changes) -> OrderResponse:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str) -> OrderResponse:
        raise NotImplementedError

    @abstractmethod
    def get_order_status(self, order_id: str) -> OrderResponse:
        raise NotImplementedError

    # ---- portfolio ----
    @abstractmethod
    def get_positions(self) -> List[Position]:
        raise NotImplementedError

    @abstractmethod
    def get_holdings(self) -> List[Holding]:
        raise NotImplementedError
