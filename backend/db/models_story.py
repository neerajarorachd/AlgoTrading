"""Market story tables (plan: docs/market_story_plan.md, phase P2).

One row per *state run* of one subject (e.g. bb_width "squeezing" for 10
candles), not one row per candle. `market_story_today` holds the current
trading day, written by ActivityEngine's 15-min flush while the engine
setting `story_enabled` is 1; the EOD job moves it to `market_story_archive`
(eod_service.py, first step). Platform data (shared by all users), so no
owner_id. Own module like models_daily.py: it only imports `Base`, and
db/models.py imports it at the bottom so create_all() sees the tables.

Key: (instrument_id, timeframe, subject, start_ts) -- a run is identified by
where it started; its end, length and values change while it is open.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _StoryRunColumns:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)  # SubscribedSymbol.id
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    trading_day: Mapped[date] = mapped_column(Date, nullable=False)  # IST date of start_ts
    subject: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. bb_width, price_vs_vwap
    state: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. squeezing, above
    prev_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    start_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # first candle (UTC, naive)
    end_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # last candle so far
    candles: Mapped[int] = mapped_column(Integer, nullable=False)
    value_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[float | None] = mapped_column(Float, nullable=True)
    vol_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)  # avg volume / 20-candle avg
    details: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: touches, bounces...
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class MarketStoryToday(_StoryRunColumns, Base):
    __tablename__ = "market_story_today"
    __table_args__ = (
        UniqueConstraint("instrument_id", "timeframe", "subject", "start_ts", name="uq_market_story_today"),
        Index("ix_market_story_today_day", "trading_day", "timeframe", "instrument_id"),
    )


class MarketStoryArchive(_StoryRunColumns, Base):
    __tablename__ = "market_story_archive"
    __table_args__ = (
        UniqueConstraint("instrument_id", "timeframe", "subject", "start_ts", name="uq_market_story_archive"),
        Index("ix_market_story_archive_day", "instrument_id", "timeframe", "trading_day"),
    )


STORY_COLUMNS = ("instrument_id", "timeframe", "trading_day", "subject", "state", "prev_state", "start_ts",
                 "end_ts", "candles", "value_start", "value_end", "rank", "vol_ratio", "details", "is_open",
                 "updated_at")
