"""Manual trades (rulebook 3b, 2026-11-11): trades a user places by hand from
the grid, the chart or the Buy/Sell ticket. Paper now; live later in the
same table (mode 'live' is refused until live trading exists).

- trades: one row per order, from placed (pending limit) to closed.
- trade_indicator_snapshots: indicator values at entry and at exit, one row
  per trade x event x timeframe (1/3/5-min), from the live engine's last
  closed candle, plus the pattern colour and what the screen recommended.
- trade_accounts: each user's paper capital (Rs 5L by default).

Own module like models_paper.py: imports only Base; db/models.py imports it
at the bottom so create_all() sees the tables. Times are naive UTC.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base

PAPER_CAPITAL_DEFAULT = 500000.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TradeAccount(Base):
    __tablename__ = "trade_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mode: Mapped[str] = mapped_column(String(8), nullable=False, default="paper")  # paper | live
    starting_capital: Mapped[float] = mapped_column(Float, nullable=False, default=PAPER_CAPITAL_DEFAULT)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (Index("uq_trade_account", "owner_id", "mode", unique=True),)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mode: Mapped[str] = mapped_column(String(8), nullable=False, default="paper")      # paper | live
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="grid")     # grid | chart | ticket
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_segment: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)                  # buy | sell
    product: Mapped[str] = mapped_column(String(10), nullable=False, default="intraday")  # intraday | delivery
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(6), nullable=False, default="market")  # market | limit
    limit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # pending (limit waiting) | open | closed | cancelled
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    placed_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ltp_at_order: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    target_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    initial_stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    initial_target_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # a limit exit waiting for its price (Exit -> Limit, 11-11); closes as reason "limit"
    exit_limit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    slippage_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)
    exit_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # target | stop | limit (limit exit) | manual | day_end | cancelled
    exit_reason: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    gross_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    costs: Mapped[Optional[float]] = mapped_column(Float, nullable=True)       # charges + slippage, rupees
    est_costs: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # rough expenses, set once at entry
    net_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # best price our way while open
    worst_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    screen_signal: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # Buy/Sell/Hold shown on the row
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # expiry element (rulebook 3b), from the entry day (placed day while pending)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    days_to_expiry: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_expiry_day: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_expiry_week: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (Index("ix_trades_owner_status", "owner_id", "status"),
                      Index("ix_trades_owner_placed", "owner_id", "placed_ts"))


class TradeIndicatorSnapshot(Base):
    __tablename__ = "trade_indicator_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String(6), nullable=False)       # entry | exit
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)   # 1min | 3min | 5min
    event_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # the fill price
    candle_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # last closed candle used
    open: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    close: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_volume_20: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rsi: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    macd_line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    macd_signal: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    macd_hist: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stoch_k: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stoch_d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    adx: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    plus_di: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    minus_di: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vwap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ema5: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ema14: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ema21: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ema50: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ma21: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ma50: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bb_upper: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bb_middle: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bb_lower: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bb_width_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    atr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    colour: Mapped[Optional[str]] = mapped_column(String(12), nullable=True)   # pattern colour on this timeframe
    top_pattern: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    top_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (Index("ix_trade_snap_trade", "trade_id", "event"),)


class TradeOrder(Base):
    """The order book behind each trade (rulebook 3b, 11-11), the traditional
    bracket: the entry order, then -- once it fills -- a stop leg (SL-M) and a
    target leg (limit), one cancels the other (OCO); a manual exit cancels
    both; a limit exit replaces the target leg and keeps the stop. Kept in
    step with the trade by TradeEngine._sync_orders; the trade row stays the
    source of the live levels."""
    __tablename__ = "trade_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(Integer, nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)        # entry | stop | target | exit
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)  # market | limit | sl_m
    side: Mapped[str] = mapped_column(String(4), nullable=False)        # buy | sell (this order's own side)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # limit / trigger price
    status: Mapped[str] = mapped_column(String(10), nullable=False)     # pending | filled | cancelled
    placed_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    done_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    fill_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (Index("ix_trade_orders_trade", "trade_id"),
                      Index("ix_trade_orders_owner_status", "owner_id", "status"))
