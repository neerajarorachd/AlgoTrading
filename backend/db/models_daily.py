"""Daily (1-day) candles for the research/daily-update universe.

Kept in its own table and its own module (not candles_historical, not
db/models.py's body) on purpose: it is fed by the scheduled
scripts/update_daily_candles.py, one row per (symbol, trade date), and the
module only *adds* to a deployment -- it imports just `Base`, so it can be
copied to the VM without replacing the VM's older models.py.
db/models.py imports it at the bottom so Base.metadata.create_all() sees it.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Date, DateTime, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base


class CandleDaily(Base):
    __tablename__ = "candles_daily"
    __table_args__ = (
        UniqueConstraint("symbol", "exchange_segment", "trade_date", name="uq_candles_daily"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_segment: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)  # NSE trading day (IST)
    open_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    high_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    low_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    close_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
