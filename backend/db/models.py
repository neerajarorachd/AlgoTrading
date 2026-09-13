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


class InstrumentActivity(Base):
    """One detected candlestick/indicator event for one instrument.

    Keyed by instrument_id (SubscribedSymbol.id), not the raw symbol string —
    SubscribedSymbol already IS this project's broker-instrument mapping
    (its own id alongside the broker's security_id), so activities reference
    that id rather than duplicating a separate instrument table for a system
    with exactly one broker wired up today. If/when a second broker adapter
    is actually built, SubscribedSymbol is the natural place to grow a
    broker-agnostic instrument identity — this table wouldn't need to change.

    activity_type is the broad category (e.g. "candle_pattern",
    "ma_crossover", "macd_crossover" — categories grow over time); activity
    is the specific name within that category (e.g. "doji", "hammer",
    "three_white_soldiers"). The unique constraint makes detection idempotent
    — re-processing the same candle (a reconnect replay, a backfill re-run)
    never duplicates the same finding.

    intensity is the same wick/body (or range/body) ratio the detector
    checked against its own qualifying threshold — NULL for a pattern with
    no defined intensity formula yet (today: the multi-candle patterns) or
    for a mathematically infinite ratio (a perfect doji, open == close
    exactly) rather than persisting a sentinel that reads as a real number.
    The OHLC columns are the triggering candle's own prices — for a
    multi-candle pattern that's the last candle in the sequence, matching
    `ts`. Both exist so a pattern's geometry can be reviewed or re-scored
    later without re-reading candles_today. This is deliberately NOT yet
    the full picture: a second table (not built yet) will link here to
    record volume/RSI/MACD/stochastic at formation time, and a backtesting
    pass (not built yet) will record what each pattern's outcome actually
    was over the following N candles — thresholds here are a starting
    point pending calibration against that, not a final answer.
    """

    __tablename__ = "instrument_activity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    activity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    activity: Mapped[str] = mapped_column(String(64), nullable=False)
    intensity: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    open_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    high_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    low_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    close_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "timeframe", "ts", "activity", name="uq_instrument_activity",
        ),
        Index("ix_instrument_activity_instrument_ts", "instrument_id", "ts"),
    )


class PatternDefinition(Base):
    """Catalog of known candle formations — data-driven metadata sitting on
    top of the actual detection logic (backend/activity_engine.py), not a
    replacement for it. The condition itself (the OHLC geometry) stays in
    code: expressing arbitrary shape rules as DB rows would need a small
    rule-evaluation engine, which is overkill for today's fixed, small
    pattern set. This table exists so the known-pattern list — code, kind,
    a human description of the condition — is queryable without reading
    Python, and so a future UI can show "why did this fire" from `description`
    rather than the detector's source.

    `code` matches the keys used in activity_engine.py's SINGLE_CANDLE_PATTERNS
    / MULTI_CANDLE_PATTERNS dicts and InstrumentActivity.activity values.
    """

    __tablename__ = "pattern_definitions"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # "single_candle" | "multi_candle" | "price_action" | "indicator" | "structure" | "graph_formation"
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EngineSetting(Base):
    """Tunable numeric parameters for activity_engine.py's detectors —
    lookback windows, similarity/depth thresholds, etc. — stored here
    rather than only as hardcoded module constants, so a future
    backtesting/calibration pass can update them directly (a plain UPDATE
    or upsert) without a code change or redeploy.

    Deliberately starts empty: a missing key means "no calibrated override
    yet," and the caller falls back to its own hardcoded default — the
    system behaves identically whether this table has zero rows or many.
    This is not a required catalog like PatternDefinition (nothing needs
    seeding); it only grows rows once something has actually been
    recalibrated.

    One row per parameter (e.g. key="swing_lookback", value=5.0) — a
    single float column covers both int-like settings (lookback windows)
    and true float thresholds (similarity/depth ratios), since an int is
    just a float with no fractional part.
    """

    __tablename__ = "engine_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class PatternPrediction(Base):
    """One open (or resolved) forward-looking call from a graph formation or
    indicator crossover — a deliberately simple, temporary stand-in for the
    real Strategy/Entry-monitor system (not built yet, see
    backend/prediction_tracker.py's own module docstring). Each qualifying
    pattern is treated as its own standalone "strategy" for now (2026-09-14
    instruction: "each complicated formation we can treat as a standalone
    strategy so that we should keep on getting predictions") — no combining
    signals yet, one row per fired pattern.

    Opened the moment a tracked pattern fires (immediately, not through
    ActivityEngine's buffer/flush — these fire only a handful of times a day
    per instrument, unlike candle patterns firing hundreds of times, so the
    DB-round-trip-minimization concern that motivated buffer/flush doesn't
    apply here). Resolved candle-by-candle afterward by PredictionTracker
    against its own stop_loss/target.

    neckline is only meaningful for double_top/double_bottom (measured-move
    patterns, whose stop/target derive from their own swing-point geometry)
    — NULL for crossover-based predictions (ATR-based stop/target instead,
    no neckline concept).

    Unique on (instrument_id, timeframe, pattern, detected_ts) — same
    idempotency reasoning as InstrumentActivity's own unique constraint:
    re-processing the same candle (a replay re-run, a backfill re-run)
    must never duplicate the same prediction.
    """

    __tablename__ = "pattern_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    pattern: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)  # "bull" | "bear"
    detected_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    neckline: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    stop_loss: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    target: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    # nullable = pending; "target_hit" | "stop_hit" | "sideways" once resolved
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    outcome_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    candles_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_pattern_predictions_pending", "instrument_id", "timeframe", "outcome"),
        UniqueConstraint(
            "instrument_id", "timeframe", "pattern", "detected_ts", name="uq_pattern_prediction",
        ),
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
