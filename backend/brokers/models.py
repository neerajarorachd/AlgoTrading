"""
Shared data models used by the broker abstraction layer.
Every broker adapter (Dhan, and later Zerodha/Upstox) speaks these types,
so nothing above this layer needs to know which broker is underneath.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class TransactionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    STOP_LOSS_MARKET = "STOP_LOSS_MARKET"


class ProductType(str, Enum):
    INTRADAY = "INTRADAY"
    DELIVERY = "DELIVERY"
    MARGIN = "MARGIN"


class ExchangeSegment(str, Enum):
    NSE_EQ = "NSE_EQ"
    NSE_FNO = "NSE_FNO"
    BSE_EQ = "BSE_EQ"
    IDX = "IDX"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    TRANSIT = "TRANSIT"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass
class Candle:
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class MarketDepthLevel:
    quantity: int
    orders: int
    price: float


@dataclass
class Quote:
    symbol: str
    ltp: float
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime
    bid_depth: List[MarketDepthLevel] = field(default_factory=list)
    ask_depth: List[MarketDepthLevel] = field(default_factory=list)


@dataclass
class OrderRequest:
    symbol: str
    security_id: str
    exchange_segment: ExchangeSegment
    transaction_type: TransactionType
    quantity: int
    order_type: OrderType
    product_type: ProductType
    price: float = 0.0
    trigger_price: float = 0.0
    disclosed_quantity: int = 0
    validity: str = "DAY"
    # Super-order legs (bracket-style: entry + attached SL + Target)
    is_super_order: bool = False
    stop_loss_price: Optional[float] = None
    target_price: Optional[float] = None
    trailing_jump: Optional[float] = None


@dataclass
class OrderResponse:
    order_id: str
    broker_order_id: str
    status: OrderStatus
    symbol: str
    quantity: int
    filled_quantity: int
    average_price: float
    raw: dict = field(default_factory=dict)   # untouched broker payload, for debugging/audit


@dataclass
class Position:
    symbol: str
    security_id: str
    exchange_segment: ExchangeSegment
    product_type: ProductType
    quantity: int          # net quantity, signed (+long, -short)
    average_price: float
    ltp: float
    unrealized_pnl: float
    realized_pnl: float


@dataclass
class Holding:
    symbol: str
    security_id: str
    quantity: int
    average_price: float
    ltp: float


class BrokerAPIError(Exception):
    """Raised for any broker-side error (rejected order, auth failure, rate limit, etc.)."""
    def __init__(self, message: str, status_code: Optional[int] = None, raw: Optional[dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.raw = raw or {}


class BrokerConnectionError(Exception):
    """Raised when the broker connection (REST auth or WebSocket) cannot be established/maintained."""
    pass
