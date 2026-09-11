from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SubscribedSymbol(Base):
    """Live source of truth for what the market feed is subscribed to.

    exchange_segment/security_id are stored (not just symbol/exchange/segment) so
    startup hydration can call broker.subscribe_feed()/unsubscribe_feed() directly,
    without re-resolving through InstrumentMaster on every restart.
    """

    __tablename__ = "subscribed_symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    segment: Mapped[str] = mapped_column(String(16), nullable=False)
    exchange_segment: Mapped[str] = mapped_column(String(32), nullable=False)
    security_id: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_close: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "exchange", "segment", name="uq_subscribed_symbol"),
    )


class CandleToday(Base):
    """Current trading day's 1/3/5-min candles, keyed to survive dual-listed symbols.

    Column names avoid `open`/`close`, which are reserved words in T-SQL.
    """

    __tablename__ = "candles_today"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_segment: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    open_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    high_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    low_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    close_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("symbol", "exchange_segment", "timeframe", "ts", name="uq_candles_today"),
        Index("ix_candles_today_symbol_tf_ts", "symbol", "timeframe", "ts"),
    )


class BrokerAccount(Base):
    """Mirror of the Trading project's own BrokerAccount table (SQLite, on the VM).

    PascalCase table/column names deliberately break AlgoTrading's own snake_case
    convention — this mirrors Trading's schema/semantics exactly, not an
    AlgoTrading-native concept, and matching names makes that relationship
    unambiguous. Written by Trading's own refresh cycle (LibSQLServerTokenMirror.py)
    and the one-time backfill script; AlgoTrading only ever reads this table.
    """

    __tablename__ = "BrokerAccount"

    AccountID: Mapped[str] = mapped_column(String(64), primary_key=True)
    Broker: Mapped[str] = mapped_column(String(32), nullable=False)
    ClientID: Mapped[str] = mapped_column(String(64), nullable=False)
    ApiKey: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    ApiSecret: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    IsActive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    CreatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BrokerToken(Base):
    """Mirror of the Trading project's own BrokerToken table. See BrokerAccount."""

    __tablename__ = "BrokerToken"

    # NOT autoincrement — mirrors Trading's own TokenID values exactly, never
    # independently assigned, so a mirrored row's identity always matches its source.
    TokenID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    AccountID: Mapped[str] = mapped_column(String(64), nullable=False)
    TokenType: Mapped[int] = mapped_column(Integer, nullable=False)
    AccessToken: Mapped[str] = mapped_column(String(2048), nullable=False)
    RefreshToken: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    ExpiresAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    IsActive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    CreatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    UpdatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    LastRefreshedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
