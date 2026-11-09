"""Paper trading tables (2026-11-09, rulebook section 3a).

Live and backtest paper trades are kept in separate tables. Everything is
owned by a user (owner_id). Users pick what to paper-trade in
`paper_selections` (pattern hits for now; signals/strategies later via
item_type). Every hit of a selected item gets a trade row, including hits
that were not taken (status "not_taken" + reason).

Backtests have two layers: one master (the comparison: stocks, dates,
status) and one run per set being compared; trades belong to a run.

Own module like models_story.py: imports only `Base`; db/models.py imports
it at the bottom so create_all() sees the tables. No broker call ever
happens from paper trading.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from db.models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _ExitSettings:
    """Exit/size settings, shared by a live selection and a backtest run, all
    set by the user. Stop/target mode: 'atr' (value x ATR), 'pct' (value % of
    entry) or 'points' (value in rupees). ATR: user-chosen period and
    timeframe ('hit' = the hit's own timeframe). Size mode: 'amount' (rupees per
    trade) or 'qty' (fixed lot/quantity)."""
    stop_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="atr")
    stop_value: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    target_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="atr")
    target_value: Mapped[float] = mapped_column(Float, nullable=False, default=10.0)
    atr_period: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    atr_timeframe: Mapped[str] = mapped_column(String(8), nullable=False, default="hit")  # hit | 1min | 3min | 5min
    size_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="amount")
    amount_per_trade: Mapped[float] = mapped_column(Float, nullable=False, default=100000.0)
    quantity_per_trade: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    entry_from: Mapped[int] = mapped_column(Integer, nullable=False, default=930)   # HHMM IST
    entry_until: Mapped[int] = mapped_column(Integer, nullable=False, default=1445)  # HHMM IST, exclusive
    day_end: Mapped[int] = mapped_column(Integer, nullable=False, default=1510)      # HHMM IST square-off
    slippage_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)


class _TradeColumns:
    """One row per hit of a selected item, taken or not. Times are UTC."""
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False, default="pattern")
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)  # buy | sell
    hit_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    match_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)  # open | closed | not_taken
    not_taken_reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    hit_close: Mapped[float] = mapped_column(Float, nullable=False)
    atr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    target_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_ts: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # stop | target | day_end
    gross_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    costs: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # best move while open
    worst_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # worst move while open
    snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)      # JSON: indicators at the hit
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class PaperSelection(_ExitSettings, Base):
    """A user's choice to paper-trade one item live (mode 'live'). Backtests
    keep their selections on their own runs, so a backtest never changes
    what trades live."""

    __tablename__ = "paper_selections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False, default="pattern")
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (Index("uq_paper_selection", "owner_id", "item_type", "item_key", unique=True),)


class PaperTradeLive(_TradeColumns, Base):
    __tablename__ = "paper_trades_live"

    @declared_attr.directive
    def __table_args__(cls):
        return (Index("ix_paper_live_owner_status", "owner_id", "status"),
                Index("ix_paper_live_owner_hit", "owner_id", "hit_ts"))


class PaperBacktestMaster(Base):
    """One comparison: shared stocks + dates; its runs are compared side by side."""

    __tablename__ = "paper_backtest_masters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    symbols: Mapped[str] = mapped_column(Text, nullable=False)  # JSON list of instrument ids
    date_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    date_to: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued")  # queued|running|done|failed
    progress_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_paper_bt_master_owner", "owner_id", "created_at"),)


class PaperBacktestRun(_ExitSettings, Base):
    """One set being compared inside a master: its item(s) + exit settings.
    Totals are cached here when the run finishes (the trades stay the truth)."""

    __tablename__ = "paper_backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    master_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    items: Mapped[str] = mapped_column(Text, nullable=False)  # JSON [{"item_type": "pattern", "item_key": ...}]
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON totals, set at the end
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class PaperBacktestTrade(_TradeColumns, Base):
    __tablename__ = "paper_backtest_trades"

    run_id: Mapped[int] = mapped_column(Integer, nullable=False)

    @declared_attr.directive
    def __table_args__(cls):
        return (Index("ix_paper_bt_trade_run", "run_id", "hit_ts"),)
