from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Index, Integer, Numeric, SmallInteger, String, Time, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class _CodedEnum(TypeDecorator):
    """Storage-normalization helper (added 2026-09-16): a small, fixed-
    vocabulary string column stored as a SmallInteger (2 bytes, SQL
    Server's 5-byte DECIMAL-tier discussion doesn't apply to integers —
    SMALLINT is always 2 bytes) instead of a String (2-9+ bytes of actual
    characters plus per-row variable-length overhead) — transparent at the
    ORM boundary, so no caller anywhere needs to know the column isn't
    still a plain Python string. Subclasses set _VALUES to the column's
    real vocabulary, confirmed against live data (SELECT DISTINCT), not
    assumed from a docstring — activity_type's real values ("candle_pattern"
    instead of separate "single_candle"/"multi_candle") didn't match
    PatternDefinition.kind's own comment, caught by checking first.

    An unrecognized string on write, or an unrecognized code on read,
    raises rather than silently coercing to None — a real data-quality
    bug (e.g. indicators.py's own classify_* vocabulary changing without
    this list being updated) must be loud, never a silent miscategorization
    as "no state."
    """

    impl = SmallInteger
    cache_ok = True
    _VALUES: tuple[str, ...] = ()

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        try:
            return self._VALUES.index(value) + 1
        except ValueError:
            raise ValueError(
                f"{type(self).__name__}: unrecognized value {value!r}, expected one of {self._VALUES}"
            )

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return self._VALUES[value - 1]
        except IndexError:
            raise ValueError(f"{type(self).__name__}: unrecognized stored code {value!r}")


class OversoldNeutralOverboughtCode(_CodedEnum):
    """RSI/Stochastic state — indicators.classify_rsi/classify_stochastic's
    own vocabulary."""

    cache_ok = True
    _VALUES = ("oversold", "neutral", "overbought")


class BullishBearishCode(_CodedEnum):
    """MACD state — indicators.classify_macd's own vocabulary."""

    cache_ok = True
    _VALUES = ("bullish", "bearish")


class TrendCode(_CodedEnum):
    """RSI/MACD/Stochastic trend — indicators.classify_series_trend's own
    vocabulary."""

    cache_ok = True
    _VALUES = ("increasing", "decreasing", "flat")


class ActivityTypeCode(_CodedEnum):
    """InstrumentActivity/PatternOutcome's activity_type category —
    confirmed 2026-09-16 via SELECT DISTINCT against live data (NOT
    PatternDefinition.kind's own comment, which lists "single_candle"/
    "multi_candle" separately — the real data uses a merged
    "candle_pattern" for both instead)."""

    cache_ok = True
    _VALUES = ("price_action", "structure", "graph_formation", "candle_pattern", "indicator")


class WindowKindCode(_CodedEnum):
    """GuidingScenario's lookback-window tag — added 2026-09-16 for the
    guiding-scenarios feature (see backend/guiding_scenarios.py): "2y" (2
    years) and "3m" (3 months) are generated and stored as two independent
    sets, never merged into one row."""

    cache_ok = True
    _VALUES = ("2y", "3m")


class IndicatorDimensionCode(_CodedEnum):
    """GuidingScenarioIndicatorStat's `dimension` column — which of the 6
    marginal indicator dimensions (state or trend, RSI/MACD/Stochastic) a
    row's win-rate breakdown is for. Matches LibPatternOutcomes.
    ALL_COMBO_DIMENSIONS' naming exactly (no "entry_"/"signal_" prefix)."""

    cache_ok = True
    _VALUES = ("rsi_state", "rsi_trend", "macd_state", "macd_trend", "stoch_state", "stoch_trend")


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


class CandleHistorical(Base):
    """Persistent multi-day candle history — 1/3/5-min AND "1day" — unlike
    CandleToday, which is explicitly current-trading-day-only and fed live
    by the market feed. This table is fed on demand by
    backend/historical_data_service.py's ensure_data_available(), which
    fetches whatever range is missing from the broker's own historical
    REST endpoint and persists it here so a later backtest over the same
    (or an overlapping) range doesn't need to re-fetch it.

    Same shape/keying as CandleToday (kept as a separate table rather than
    merged, since the two have different write paths/lifetimes — one is
    live-feed-driven, the other is fetch-on-demand for backtesting).
    """

    __tablename__ = "candles_historical"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_segment: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)  # "1min" | "3min" | "5min" | "1day"
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    open_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    high_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    low_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    close_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("symbol", "exchange_segment", "timeframe", "ts", name="uq_candles_historical"),
        Index("ix_candles_historical_symbol_tf_ts", "symbol", "timeframe", "ts"),
    )


class HistoricalDataCoverage(Base):
    """One (symbol, exchange_segment, timeframe)'s explicitly-fetched
    date range — the actual "have we already fetched this?" answer for
    historical_data_service.ensure_data_available, kept as its own
    explicit watermark rather than derived from CandleHistorical's own
    row timestamps.

    Deriving coverage from stored rows' min/max ts sounds simpler but is
    WRONG the moment two separate, non-adjacent date ranges have ever been
    fetched for the same key (e.g. a backtest over March, then later one
    over January): the row-derived earliest/latest would span both
    islands and silently claim the untouched gap between them is
    "covered" too. This table avoids that by only ever being set by
    ensure_data_available itself, and only ever grown when the newly
    requested range actually touches (overlaps or is adjacent to) what's
    already covered — a genuinely disjoint request REPLACES it instead of
    merging, so a later re-request of the abandoned range safely
    re-fetches (wasteful, never silently wrong) rather than being
    misreported as covered. Found the gap-vs-single-watermark issue the
    hard way via live verification, not by inspection.
    """

    __tablename__ = "historical_data_coverage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_segment: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    covered_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    covered_to: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "exchange_segment", "timeframe", name="uq_historical_data_coverage"),
    )


class CandleIndicators(Base):
    """One row of computed indicator VALUES per candle — RSI/MACD/
    Stochastic/VWAP/MA21/MA50/ATR/Bollinger Bands — the "walkthrough
    engine" snapshot flagged as prerequisite infrastructure earlier in
    this project (see memory: walkthrough_configurable_mode). Needed so
    Strategies (backend/db/ops/LibStrategies.py) can eventually evaluate
    numeric-value conditions (RSI >= 40, MA21 > VWAP) against real
    per-candle data, for both backtesting and live trading.

    activity_engine.py's ActivityEngine already computes every one of
    these values transiently inside on_candle_closed (to check its own
    crossover detectors) and previously discarded them — this table
    persists the exact same values instead, so a strategy condition and
    a pattern detector are guaranteed to agree on what RSI/MACD/etc. was
    at a given candle, never two independently-recomputed numbers that
    could drift apart.

    One row per (instrument_id, timeframe, ts) — mirrors CandleToday's
    own keying, but by instrument_id (matching InstrumentActivity/
    PatternPrediction/PatternOutcome) rather than by symbol, since this
    is engine-detection state, not raw market data. Nullable columns are
    exactly the ones with a warm-up period before they're defined
    (RSI/MACD/ATR need more closes than their period, Stochastic needs
    its own lookback, MA50 needs 50 closes) — same "None until ready"
    convention indicators.py's own update_rsi/update_macd/etc. already
    use.
    """

    __tablename__ = "candle_indicators"

    # Numeric precision note (normalized 2026-09-16, see storage-normalization
    # notes below InstrumentActivity): SQL Server's DECIMAL storage engine has
    # a hard tier boundary at total precision 9 (<=9 digits -> 5 bytes on
    # disk; 10-19 -> 9 bytes) — Numeric(18,6)'s declared range was never
    # actually used (RSI/Stochastic are 0-100, MACD/ATR are small deltas,
    # VWAP/MA/BB are real stock prices, confirmed by direct MIN/MAX scan
    # against the live 218k-row table before narrowing: nothing exceeds
    # +-2328). Numeric(9,4) (oscillators/deltas) and Numeric(9,2) (price-
    # level fields, matching NSE's real 2-decimal quoting precision, with
    # room to 9,999,999.99 for headroom well past any real NSE price) both
    # land in the 5-byte tier — same Python Decimal in/out, no code changes
    # needed anywhere outside this file.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    rsi: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    macd_line: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    macd_signal: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    stoch_k: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    stoch_d: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    vwap: Mapped[float | None] = mapped_column(Numeric(9, 2), nullable=True)
    ma21: Mapped[float | None] = mapped_column(Numeric(9, 2), nullable=True)
    ma50: Mapped[float | None] = mapped_column(Numeric(9, 2), nullable=True)
    atr: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    bb_upper: Mapped[float | None] = mapped_column(Numeric(9, 2), nullable=True)
    bb_middle: Mapped[float | None] = mapped_column(Numeric(9, 2), nullable=True)
    bb_lower: Mapped[float | None] = mapped_column(Numeric(9, 2), nullable=True)

    __table_args__ = (
        UniqueConstraint("instrument_id", "timeframe", "ts", name="uq_candle_indicators"),
        Index("ix_candle_indicators_instrument_ts", "instrument_id", "ts"),
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
    record volume/RSI/MACD/stochastic at formation time. PatternOutcome
    (this same file, written by backend/pattern_outcome_analysis.py) now
    records what each pattern's outcome actually was over the following N
    candles — thresholds here are a starting point pending calibration
    against that, not a final answer.
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


class InstrumentActivityDailyCount(Base):
    """Running per-(instrument, IST trading day) event counts by category --
    a STORED rollup for the Market Watch row's quick-glance "5 candle
    formations, 3 indicator crossovers" badges (explicit instruction,
    2026-10-01: "better to store date, instrument, cs, ind" than recompute
    live). Incremented at write time (db/ops/LibActivities.py's
    persist_bulk/persist_one, in the SAME transaction as the InstrumentActivity
    insert itself), not a COUNT(*) GROUP BY scan on every page load — same
    "precompute once, cheap lookup forever" philosophy as GuidingScenario
    (see [[guiding_scenarios_redesign]]), just incremental instead of batch.

    trading_date is the candle's own IST calendar date (this table's one
    deliberate denormalization — every other activity table keys by the raw
    UTC `ts`), because that's the boundary a trader actually thinks in
    ("today's events"), matching condition_evaluator.py's own
    TRADING_DATE_COLUMN convention elsewhere in this codebase.

    Only the two categories the Market Watch badges show are tracked
    (candle_pattern, indicator) — not a generic per-activity_type table —
    deliberately narrow to match what's actually displayed; add a column
    if/when a third category badge is wanted, same as every other
    additive column in this schema.
    """

    __tablename__ = "instrument_activity_daily_counts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    candle_pattern_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indicator_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("instrument_id", "trading_date", name="uq_instrument_activity_daily_counts"),
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


class SystemSetting(Base):
    """Tunable parameters for the live-trading recommendation-queue
    subsystem (backend/recommendation_engine.py, db/ops/LibRecommendations.py)
    — the win% quality gate, Wilson-score confidence level, minimum sample
    size, expiry-in-candles, and queue bandwidth described in
    recommendation_engine.py's own module docstring.

    Deliberately a SEPARATE table from EngineSetting, not a shared
    key-namespace on it: EngineSetting is scoped to activity_engine.py's
    own pattern-DETECTION thresholds (swing_lookback, etc.) — a different
    concern — and is read-only from code today ("a future calibration
    pass, not yet implemented" per its own docstring). SystemSetting is
    the opposite: meant to be actively tuned, presumably via a future UI,
    so db/ops/LibSystemSettings.py supports both reading AND writing from
    day one, and this table is SEEDED with real defaults at startup
    (recommendation_engine.SYSTEM_SETTING_SEED, called from app.py) rather
    than starting empty — a row needs to exist before anything can show
    or edit it.

    Same key/value/description shape as EngineSetting (one float column
    covers both int-like settings, e.g. queue bandwidth, and true float
    thresholds, e.g. the win% threshold) — no reason to diverge from an
    already-proven convention for the same kind of data.
    """

    __tablename__ = "system_settings"

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


class PatternOutcome(Base):
    """Neutral, un-opinionated record of what actually happened to price in
    the candles after a pattern fired — independent of any predicted stop-
    loss/target (that's PatternPrediction's job, backend/prediction_tracker.py).
    Built to answer exactly "what happens after a pattern occurred" — the
    first deliverable toward the Strategy/backtesting system, per project
    guidance: "Freehand backtesting"/pattern-outcome analysis comes before
    Strategies, which come before the full backtesting engine.

    One row per (instrument_id, timeframe, pattern, detected_ts) — same
    idempotency convention as InstrumentActivity/PatternPrediction, so
    re-running the analysis over the same historical data never duplicates
    a row.

    pct_change_N is the % price change from entry_price to the close N
    candles later; nullable when fewer than N candles remained in the
    day's data at detection time (e.g. a pattern firing in the last few
    minutes before close). max_favorable_pct/max_adverse_pct are measured
    over whichever window was actually available (window_candles records
    how many candles that was) — the best/worst price seen relative to
    entry, regardless of the pattern's own assumed direction; a caller
    that cares about direction reads it off the `direction` column
    (nullable — not every pattern has one, e.g. rectangle/doji) and
    interprets favorable/adverse accordingly itself, rather than this
    table baking in an assumption.
    """

    __tablename__ = "pattern_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    pattern: Mapped[str] = mapped_column(String(64), nullable=False)
    activity_type: Mapped[str] = mapped_column(ActivityTypeCode, nullable=False)
    direction: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)  # "bull" | "bear" | None
    detected_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Numeric precision note (normalized 2026-09-16, same reasoning as
    # CandleIndicators above): real value ranges confirmed against live
    # data before narrowing (entry_price max ~2228, pct_change_*/max_
    # favorable/adverse max_abs ~0.2 i.e. ~20%, matching NSE circuit
    # limits) — Numeric(9,2)/(9,6) both land in SQL Server's 5-byte
    # DECIMAL storage tier (<=9 total precision) vs the old Numeric(18,x)'s
    # 9-byte tier, with wide headroom over anything actually seen.
    entry_price: Mapped[float] = mapped_column(Numeric(9, 2), nullable=False)
    pct_change_5: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    pct_change_10: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    pct_change_15: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    pct_change_20: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    # Added 2026-09-15 alongside pattern_outcome_analysis.py's own
    # DEFAULT_CHECKPOINTS extension (5/10/15/20 -> 5/10/15/20/30, explicit
    # instruction: "analyze 5, 10, 15, 20, 30 candles"). Rows analyzed
    # BEFORE this change keep pct_change_30=NULL and a window_candles=20
    # (not 30) max_favorable_pct/max_adverse_pct — persist_bulk only
    # inserts rows that don't already exist by (instrument, timeframe,
    # pattern, detected_ts), it never widens an already-analyzed row's
    # window. A full re-analysis (delete then re-run analyze_instrument)
    # is needed to backfill this column and the wider max_favorable/
    # adverse window onto already-analyzed data.
    pct_change_30: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    max_favorable_pct: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    max_adverse_pct: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    window_candles: Mapped[int] = mapped_column(Integer, nullable=False)

    # Indicator VALUE + classified STATE at the moment this pattern fired
    # (explicit instruction, 2026-09-15: "store indicator state + values")
    # — looked up from CandleIndicators (same instrument/timeframe/ts as
    # this row's own detected_ts) and persisted here directly by
    # pattern_outcome_analysis.py, rather than joined + classified live on
    # every read. State labels come from indicators.py's classify_rsi/
    # classify_macd/classify_stochastic (a pure function of the value
    # columns alongside them — recomputing a label from scratch is a
    # backfill, not a schema change, if the thresholds are ever tuned).
    # All nullable: null when CandleIndicators has no row for this exact
    # (instrument, timeframe, ts) — e.g. a replay that never called
    # ActivityEngine.flush() for the indicator side (see
    # [[intensity_checkpoint_analysis]]) — or during the RSI/MACD/ATR
    # warm-up period, same "None until ready" convention CandleIndicators
    # itself already uses.
    entry_rsi: Mapped[Optional[float]] = mapped_column(Numeric(9, 4), nullable=True)
    entry_rsi_state: Mapped[Optional[str]] = mapped_column(OversoldNeutralOverboughtCode, nullable=True)
    entry_macd_line: Mapped[Optional[float]] = mapped_column(Numeric(9, 4), nullable=True)
    entry_macd_signal: Mapped[Optional[float]] = mapped_column(Numeric(9, 4), nullable=True)
    entry_macd_state: Mapped[Optional[str]] = mapped_column(BullishBearishCode, nullable=True)
    entry_stoch_k: Mapped[Optional[float]] = mapped_column(Numeric(9, 4), nullable=True)
    entry_stoch_state: Mapped[Optional[str]] = mapped_column(OversoldNeutralOverboughtCode, nullable=True)

    # The "condition" half of the same idea (backtesting_calibration_plan.md's
    # "Next level" note: RSI "increasing," "just crossed the 60 threshold"),
    # added 2026-09-15 alongside the value/state columns above — was this
    # indicator climbing/falling/flat over the INDICATOR_TREND_LOOKBACK
    # candles immediately before (and including) this one firing? Computed
    # by indicators.classify_series_trend over the already-persisted
    # CandleIndicators series (signed-value trend, not classify_trend's own
    # abs()-based magnitude trend — an indicator crossing zero is a real
    # directional move, not "shrinking then growing"). Same nullable/
    # backfill-on-tune convention as the state columns above.
    entry_rsi_trend: Mapped[Optional[str]] = mapped_column(TrendCode, nullable=True)
    entry_macd_trend: Mapped[Optional[str]] = mapped_column(TrendCode, nullable=True)
    entry_stoch_trend: Mapped[Optional[str]] = mapped_column(TrendCode, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_pattern_outcomes_instrument_pattern", "instrument_id", "pattern"),
        UniqueConstraint(
            "instrument_id", "timeframe", "pattern", "detected_ts", name="uq_pattern_outcome",
        ),
    )


class RecommendationSystem(Base):
    """Registry of "recommendation systems" — standing, DB-configured scan
    jobs that watch a Watchlist and feed the shared Recommendation queue.
    First (and, since the 2026-09-16 guiding-scenarios redesign, only) row
    is "RS1" (the user's own term throughout design discussion, kept
    verbatim as its `code` rather than invented terminology) — RS2/RS3
    (real, built and tested 2026-09-15) were explicitly removed once RS1's
    own mechanism was redesigned around precomputed guiding scenarios
    instead of live per-signal analysis; RS3's "looser sample-size floor"
    role is effectively superseded by the new dual-window (2y/3m) design,
    and RS2's exact joint-combination matching is exactly what the
    "indicator state must never interfere in the decision" product
    decision ruled out.

    An RS owns WHERE/WHAT to scan (watchlist_id, timeframe, top_n_per_run).
    It does NOT own how pattern analysis itself works beyond picking WHICH
    of recommendation_engine.py's analysis modes to run — that logic
    ("AM1", per the user: "AM1 is an internal engine of RS1, basically
    analysis logic") is recommendation_engine.generate_recommendations()
    itself, called once per candidate this RS's (not-yet-built) polling loop
    finds. "AM1" is not a separate table/class: it is RS1's own analysis
    step, same as e.g. ActivityEngine has no separate "detector" table.

    `analysis_mode` is kept as a column for forward compatibility (a future
    second analysis system is still a real possibility, same as `source` on
    Recommendation) but only one value exists today: "primary_confirmation"
    — despite the name (unchanged since 2026-09-15 to avoid an idle rename),
    as of the guiding-scenarios redesign this means "look up the
    precomputed GuidingScenario table" (guiding_scenarios.match_guiding_
    scenario), not the old live confirmation/veto flow — indicator
    dimensions no longer gate anything live, see GuidingScenarioIndicatorStat's
    own docstring for where that analysis moved to instead.

    The *_override columns let one RS tune a threshold without touching the
    SystemSetting every other RS shares (None = inherit the global
    SystemSetting value, same fallback idiom as EngineSetting/SystemSetting
    themselves). `kind`/`strategy_id` anticipate the user's own "there will
    be user defined strategies, all of those will also share the
    recommendations" — a future "strategy"-kind row driven by a Strategy
    record instead of hardcoded system logic — but only "system" is used
    today; nothing reads strategy_id yet.

    Deliberately NOT built here (per explicit sequencing — "First design RS1
    then we will discuss Polling system and then queuing system"): the
    watchlist-iteration polling loop itself, and the per-run top-N reduction
    across a whole watchlist's candidates. Recommendation.recommendation_
    system_id (see below) is the hook a future poller will stamp; nothing
    writes it yet.
    """

    __tablename__ = "recommendation_systems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)  # "RS1"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="system")  # "system" | "strategy"
    # The RS's PARENT Strategy (added use, 2026-09-19): child Strategies
    # (Strategy.parent_id == this) are its pattern rules -- see rule_gate.py.
    strategy_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # How a pattern's several rules combine: "all" (every evaluable rule must
    # pass, default) | "any" (one passing rule is enough).
    rule_combine_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="all", server_default="all")
    # Capital a "% of available fund" pattern default is sized against until
    # strategy accounts / live broker balance exist (see pattern_defaults.py).
    fallback_capital: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    watchlist_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False, default="3min")
    top_n_per_run: Mapped[int] = mapped_column(Integer, nullable=False, default=3)  # "1 to 3, configurable"
    analysis_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="primary_confirmation")
    # None on any of these = inherit the matching global SystemSetting key
    # (recommendation_win_pct_threshold, etc. — see recommendation_engine.py)
    win_pct_threshold_override: Mapped[Optional[float]] = mapped_column(Numeric(6, 4), nullable=True)
    min_sample_size_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    wilson_confidence_override: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    expiry_candles_override: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)
    analysis_lookback_days_override: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)
    max_regenerations_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    analysis_start_time_minutes_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class Recommendation(Base):
    """One queued (or resolved) live-trading recommendation — ONE row per
    live pattern occurrence (2026-09-15 correction: "multiple recommendations
    from one stock" means multiple DIFFERENT patterns firing — e.g. Hammer,
    Triangle, an LL-HH-HL structure, and a MACD crossover on the same stock
    are 4 separate candidates — never multiple rows for the SAME pattern
    occurrence). See backend/recommendation_engine.py's own module docstring
    for the full generation flow and db/ops/LibRecommendations.py for the
    queue-reading/selection operations.

    `source` names WHICH analysis system produced this row (today always
    "pattern_outcome_bands", the only analysis system that exists) — kept
    as a plain string, not an enum/FK, so a future second analysis system
    can populate this same table with its own source tag, no schema change
    needed (explicit requirement: "different analysis system will generate
    recommendations").

    **Guiding-scenario lookup, not live analysis** (redesigned 2026-09-16 —
    superseding the original primary+confirmation/veto flow described here
    before that date): RS1 no longer computes anything live against raw
    PatternOutcome data. Instead backend/guiding_scenarios.py mines
    PatternOutcome WEEKLY into a small precomputed GuidingScenario table
    (pattern + intensity band, win% >= threshold, real sample size — see
    its own docstring), and a live pattern firing does one cheap indexed
    lookup (guiding_scenarios.match_guiding_scenario) against that table.
    band_kind/band_label/band_min/band_max/qualifying_checkpoint/
    sample_count/win_count/win_pct/wilson_score are copied straight from
    the matched GuidingScenario row (band_kind is always "intensity" now —
    the old "pattern"/"combination" fallback modes are gone, see below).
    guiding_scenario_id/window_kind record exactly which precomputed row
    and lookback window justified this recommendation.

    Explicit product decision: indicator state/trend (rsi_state, macd_state,
    etc.) is stored here purely as signal CONTEXT (audit trail, same as
    before) and NEVER gates the decision — no live veto step exists anymore.
    veto_reason/confirmations_checked/confirmations_passed are kept as
    columns (existing rows retain their historical values) but every new
    row gets (None, 0, 0) — nothing left to verify once the gate is a
    direct table lookup. The old "no intensity -> whole-pattern fallback"
    behavior is also gone: a live signal with no intensity value cannot be
    evaluated by this mechanism at all (pattern + intensity band are both
    required per the guiding-scenario decision gate), so generate_
    recommendations() simply returns None for it.

    status lifecycle: "queued" (a guiding scenario matched, waiting for
    bandwidth) -> "selected" (LibRecommendations.mark_selected picked it) OR
    "expired" (LibRecommendations.sweep_expired swept it, unfilled, past
    expires_at) OR "superseded" (a regeneration sibling lost to whichever
    mark_selected actually picked — see parent_recommendation_id).
    "rejected" is terminal, set AT CREATION when no guiding scenario matched
    the live signal — persisted anyway (when SystemSetting recommendation_
    persist_rejected is truthy) purely for audit/review, never enters the
    pending queue.

    expires_at is generated_at + candle_duration(timeframe) *
    expiry_candles (see backend/timeframes.py), NOT detected_ts-relative —
    a regenerated recommendation (parent_recommendation_id set) gets a
    fresh window measured from its own generation moment, not from the
    original (already-past) live signal's timestamp, which would already
    be expired the instant it's created.

    parent_recommendation_id / regeneration_count implement "go for
    analysis again": when sweep_expired finds a queued row past
    expires_at, it's marked "expired" and (capped by SystemSetting
    recommendation_max_regenerations) recommendation_engine.
    sweep_and_regenerate re-runs the SAME live signal (instrument/
    timeframe/pattern/direction/entry_price/detected_ts/intensity/
    indicator states, all stored on this row) against FRESH historical
    data, producing a new row chained via parent_recommendation_id with
    regeneration_count = parent.regeneration_count + 1. Self-referential,
    no enforced FK — matches this schema's existing plain-int "FK-like"
    convention (e.g. Strategy.parent_id).

    Unique on (instrument_id, timeframe, pattern, direction, detected_ts,
    regeneration_count, recommendation_system_id) — one row per live
    pattern occurrence PER RS per generation round. recommendation_
    system_id is included deliberately (bug found live, 2026-09-15, back
    when RS2/RS3 existed alongside RS1 and shared its engine, so two RS's
    could evaluate the identical live signal and collide on an otherwise-
    identical key) — kept even after RS2/RS3's 2026-09-16 removal since the
    same shape of collision is possible again the moment a second RS row
    (even a future "strategy"-kind one, see `kind` above) exists. A caller
    passing no recommendation_system_id (None) — a direct manual call with
    no RS context — still gets ordinary replay-safe idempotency for repeats
    WITH an RS, but NOT between two None-RS calls for the identical signal:
    SQL treats NULL as distinct from NULL in a unique constraint (true on
    both SQL Server and SQLite), so two None-recommendation_system_id calls
    for the same signal would NOT collide. Accepted deliberately — no live
    caller invokes this with recommendation_system_id=None today; None is
    only ever a manual/test call — revisit if a real no-RS caller is ever
    added.
    """

    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # Which RecommendationSystem row (e.g. RS1) orchestrated this generation —
    # distinct from `source`, which names the ANALYSIS algorithm ("AM1" =
    # "pattern_outcome_bands") that RS used internally. Nullable: a row
    # generated by a direct generate_recommendations() call with no RS
    # context (e.g. the existing test suite, a manual script) leaves this
    # unset. Plain int, no enforced FK — same convention as instrument_id.
    recommendation_system_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    pattern: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)  # "bull" | "bear"
    entry_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    detected_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Live signal context this recommendation was generated from — needed
    # verbatim to re-run generate_recommendations() on regeneration without
    # re-reading CandleIndicators. All nullable: a live signal need not
    # have every dimension available (e.g. intensity is null for patterns
    # with no intensity formula, see InstrumentActivity.intensity's own
    # docstring).
    signal_intensity: Mapped[Optional[float]] = mapped_column(Numeric(18, 4), nullable=True)
    signal_rsi_state: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    signal_rsi_trend: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    signal_macd_state: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    signal_macd_trend: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    signal_stoch_state: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    signal_stoch_trend: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # Raw indicator VALUES alongside the labels above — added 2026-09-15,
    # a real gap found while asking "are we storing indicator state AND
    # values": only the classified state/trend labels were kept, never the
    # actual numbers (PatternOutcome already stores both, for the same
    # reason — see its own entry_rsi/entry_macd_line/entry_macd_signal/
    # entry_stoch_k). Column names mirror PatternOutcome's exactly (signal_
    # rsi <-> entry_rsi, etc.) so the two tables read the same way.
    signal_rsi: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    signal_macd_line: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    signal_macd_signal: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    signal_stoch_k: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)

    # Which PRIMARY band this row's scored evidence came from (audit trail)
    band_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # "intensity" | "pattern" (no intensity value available)
    band_label: Mapped[str] = mapped_column(String(32), nullable=False)
    band_min: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    band_max: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)

    # Scored evidence — the PRIMARY band only (see class docstring)
    qualifying_checkpoint: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    win_count: Mapped[int] = mapped_column(Integer, nullable=False)
    win_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 4), nullable=True)
    wilson_score: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False)

    # CONFIRMATION verdict — set only when the primary band cleared its own
    # gate (otherwise confirmation is skipped entirely, nothing to verify).
    # veto_reason is non-null exactly when some confirming dimension's own
    # historical win rate was low enough to reject an otherwise-qualifying
    # primary signal (e.g. "rsi_state=oversold win_pct=0% n=6").
    # confirmations_checked/passed count how many of the live signal's
    # available indicator dimensions had enough history to evaluate at all,
    # and how many of those did NOT veto — used by LibRecommendations.
    # pick_best as a tie-breaker when two candidates' wilson_score are close.
    veto_reason: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    # Rule-gate outcome note (rule_gate.py): e.g. "rule skipped: R1 (insufficient
    # data)" on a queued row, or "rules failed: R1" on a rejected one.
    rule_note: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    # What to do about it, from the RS's per-pattern defaults
    # (pattern_defaults.suggest_order): direction-aware SL/target prices and a
    # quantity. None when the RS had no defaults or no way to size the order.
    suggested_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    suggested_sl_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4), nullable=True)
    suggested_target_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4), nullable=True)
    confirmations_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmations_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Snapshot of the SystemSetting values in effect at generation time
    win_pct_threshold_applied: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    min_sample_size_applied: Mapped[int] = mapped_column(Integer, nullable=False)
    wilson_confidence_applied: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    selected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    regeneration_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_recommendation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Added 2026-09-16 (guiding-scenarios redesign): which precomputed
    # GuidingScenario row justified this recommendation, when RS1 sourced
    # it from a guiding-scenario lookup rather than a live band
    # computation. Plain int, no enforced FK, same convention as every
    # other cross-table reference in this schema. Nullable: a row created
    # before this column existed, or by a caller not using the
    # guiding-scenario path, has no such row to point to. A future sizing
    # step joins GuidingScenarioIndicatorStat via this link when it needs
    # volume/SL-target guidance — deliberately not duplicated onto every
    # Recommendation row.
    guiding_scenario_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Which lookback window (see WindowKindCode/GuidingScenario) this
    # recommendation was matched against — snapshotted here rather than
    # only inferred via guiding_scenario_id, since the weekly regeneration
    # job can delete-and-replace that GuidingScenario row before this
    # Recommendation is ever swept/regenerated, at which point the FK
    # target would already be gone. Nullable: a row created before this
    # column existed, or under the old live-analysis mechanism, has none.
    window_kind: Mapped[Optional[str]] = mapped_column(WindowKindCode, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "timeframe", "pattern", "direction", "detected_ts",
            "regeneration_count", "recommendation_system_id", name="uq_recommendation",
        ),
        Index("ix_recommendations_status_expires", "status", "expires_at"),
        Index("ix_recommendations_signal", "instrument_id", "timeframe", "pattern", "detected_ts"),
    )


class GuidingScenario(Base):
    """A precomputed, weekly-regenerated "this pattern + intensity band has
    historically won often enough to trust" row — the decision gate for
    RS1's live matching (backend/guiding_scenarios.py), replacing RS1's old
    live per-signal DB analysis. Explicit product decision (2026-09-16):
    the gate is pattern + intensity band ONLY — indicator state/trend must
    never interfere in this decision (see GuidingScenarioIndicatorStat for
    where that data lives instead, informational only).

    Two independent lookback windows are generated and stored side by side
    (window_kind "2y"/"3m", see WindowKindCode) — no combination rule
    decided yet; a live caller can consult one, the other, or both.

    Regeneration is delete-and-bulk-reinsert per (instrument_id, timeframe,
    window_kind), not an incremental diff — the qualifying set can
    genuinely gain or lose members week to week, and this matches the
    "clean re-fetch over patch" approach already established this session
    for HINDCOPPER's own historical data quality fix.

    Column shapes mirror Recommendation's own scoring/snapshot columns
    (sample_count/win_count/win_pct/wilson_score, win_pct_threshold_applied/
    min_sample_size_applied) — same rationale: a later threshold retune
    must never reinterpret an already-decided row.
    """

    __tablename__ = "guiding_scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    pattern: Mapped[str] = mapped_column(String(64), nullable=False)
    window_kind: Mapped[str] = mapped_column(WindowKindCode, nullable=False)
    direction: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)  # "bull" | "bear" | None

    band_min: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False)
    band_max: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False)

    checkpoint: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    win_count: Mapped[int] = mapped_column(Integer, nullable=False)
    win_pct: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    wilson_score: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False)

    win_pct_threshold_applied: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    min_sample_size_applied: Mapped[int] = mapped_column(Integer, nullable=False)

    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "timeframe", "pattern", "window_kind", "band_min", "band_max",
            name="uq_guiding_scenario",
        ),
        Index("ix_guiding_scenarios_lookup", "instrument_id", "timeframe", "pattern", "window_kind"),
    )


class GuidingScenarioIndicatorStat(Base):
    """The informational/education layer alongside a GuidingScenario —
    marginal win-rate breakdown for one indicator state or trend, computed
    over ONLY that scenario's own occurrence subset (not the whole
    pattern). Never gates the take-call/pass decision (see GuidingScenario's
    own docstring) — laid down for future volume-sizing/SL-target-ratio use
    and manual review, not consumed by any decision logic yet.

    `label` stays a plain string rather than one of this file's _CodedEnum
    types: a single column has to hold labels from 3 different vocabularies
    (oversold/neutral/overbought, bullish/bearish, increasing/decreasing/
    flat) depending on `dimension`, and a _CodedEnum needs one fixed
    vocabulary per column — String(16) is already small enough that the
    extra normalization isn't worth the added complexity here.
    """

    __tablename__ = "guiding_scenario_indicator_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guiding_scenario_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension: Mapped[str] = mapped_column(IndicatorDimensionCode, nullable=False)
    label: Mapped[str] = mapped_column(String(16), nullable=False)

    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    win_count: Mapped[int] = mapped_column(Integer, nullable=False)
    win_pct: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    wilson_score: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "guiding_scenario_id", "dimension", "label", name="uq_guiding_scenario_indicator_stat",
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


class StrategyElement(Base):
    """Catalog of everything a Strategy condition can reference — the
    building-block vocabulary for StrategyCondition below.

    An "event" element is a boolean — did this fire on this candle or
    not (a candle pattern, indicator crossover, structure signal, or
    graph formation; `code` matches PatternDefinition.code exactly, this
    catalog is seeded from the same PATTERN_CATALOG, not a separate
    list) — a condition referencing one needs no operator/value.

    A "numeric" element reads a value (RSI, MACD line, VWAP, MA21,
    close, ...) from `source` (candle_indicators or candles_today) and
    supports comparison operators against either a static value/range or
    another numeric element.
    """

    __tablename__ = "strategy_elements"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    element_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "event" | "numeric"
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # "instrument_activity" | "candle_indicators" | "candle"
    description: Mapped[str] = mapped_column(String(256), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RecommendationOutcome(Base):
    """What actually happened after a Recommendation, one row per
    recommendation (ANY status -- queued, rejected-by-rule, expired,
    selected -- so rejected ones can be measured for false negatives, and
    recommended ones for false alarms). Written by recommendation_outcomes.py
    from the candles that followed detected_ts.

    `win` uses the system's one win rule (LibPatternOutcomes._band_stats):
    price moved in the predicted direction at the recommendation's own
    qualifying_checkpoint, sign only, no magnitude threshold -- so it is
    directly comparable to the win_pct stored on the recommendation. None
    until that many candles exist. All pct values are the raw (direction-
    UNadjusted) fractional move from entry_price, same convention as
    PatternOutcome. status "partial" = still accumulating candles; "complete"
    = all checkpoints observed, or no more data is coming (a day old)."""

    __tablename__ = "recommendation_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    candles_observed: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint: Mapped[int] = mapped_column(Integer, nullable=False)
    checkpoint_pct: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    win: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    pct_change_5: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    pct_change_10: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    pct_change_15: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    pct_change_20: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    pct_change_30: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    max_favorable_pct: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    max_adverse_pct: Mapped[Optional[float]] = mapped_column(Numeric(9, 6), nullable=True)
    # Which of the recommendation's own suggested levels price reached first
    # within the observed window: "target" | "sl" | "none". A candle touching
    # both counts as "sl" (same conservative rule order_backtest.py uses).
    # None when the recommendation had no suggested SL/target.
    first_hit: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    candles_to_hit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (UniqueConstraint("recommendation_id", name="uq_recommendation_outcomes_rec"),)


class RecommendationSystemPatternDefault(Base):
    """Per-pattern order defaults for a recommendation system: how big, and
    where the SL and target sit, for every recommendation of that pattern.
    Per PATTERN, not per rule (rules overlap patterns). pattern "*" is the
    mandatory fallback row for every pattern without its own row.

    size_mode "max_qty": quantity = max_qty. "available_fund": quantity =
    fund_pct % of the RS's available fund / entry price (fund = the RS's
    fallback_capital until strategy accounts exist). sl_pct/target_pct are
    PERCENT units (0.40 = 0.40%) off the entry price, direction-aware."""

    __tablename__ = "recommendation_system_pattern_defaults"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_system_id: Mapped[int] = mapped_column(Integer, nullable=False)
    pattern: Mapped[str] = mapped_column(String(64), nullable=False)
    size_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="max_qty")
    max_qty: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fund_pct: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)
    sl_pct: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    target_pct: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)

    __table_args__ = (UniqueConstraint("recommendation_system_id", "pattern", name="uq_rs_pattern_default"),)


class Strategy(Base):
    """Top-level metadata for one strategy. The condition tree itself
    lives in StrategyConditionGroup/StrategyCondition below, keyed by
    strategy_id — a strategy's root group is simply the
    StrategyConditionGroup row for this strategy_id with
    parent_group_id IS NULL (no root_group_id column here; storing one
    would need a circular-reference two-step insert for no real benefit).

    parent_id supports duplication/versioning ("strategy Parent id
    (duplicate)") — a strategy created by copying another points back at
    it; self-referential, no enforced FK (matches this codebase's existing
    convention of plain int "FK-like" columns, e.g. InstrumentActivity's
    own instrument_id).

    This table only stores a strategy's DEFINITION — evaluating one
    against real data (backtesting or live trading) is a separate engine;
    see backend/db/ops/LibStrategies.py's own module docstring and (for
    the order-management side) backend/scripts/order_backtest.py.

    The columns below (order-management + SL/target formula) mirror the
    Trading project's own `Strategies` table (StrategyMaxQty, StrategyFund,
    ExitAtLoss/ExitAtLossCount, StrategyMaxTradesAtOnce,
    StrategyTradingStartTime/StrategyNewOrderEndTime/StrategyTradingEndTime
    — reviewed read-only via SSH, 2026-09-14, never copied) — living on the
    strategy itself, not a backtest-run-only config, since these are
    meant to be reused by live trading once that's built too, exactly as
    Trading does. All nullable/additive: a strategy that hasn't set these
    falls back to the same engine defaults order_backtest.py's own
    EngineConfig already uses, so existing rows are unaffected.

    Unlike Trading's `StrategyParams` (hardcoded RSIIN/SKIN/MACDFASTIN/...
    columns, one per possible indicator), per-run parameter VALUES for a
    ranged StrategyCondition are NOT stored here — see BacktestRunParameter,
    which references the StrategyCondition directly and works for any
    element without a schema change.
    """

    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    family: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    # --- SL/target formula (paired with a Strategy's own trade direction) ---
    direction: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)  # "bull" | "bear" | "both"
    sl_formula_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # "atr" | "fixed_percent" | "fixed_points"
    sl_atr_multiplier: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)
    sl_fixed_value: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    target_formula_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # "risk_reward" | "fixed_percent" | "fixed_points"
    target_risk_reward_ratio: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)
    target_fixed_value: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)

    # --- order management (Trading's own Strategies columns, see docstring) ---
    capital_per_trade: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)  # StrategyFund
    max_vol_per_call: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # StrategyMaxQty
    max_orders_at_a_time: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # StrategyMaxTradesAtOnce
    daily_max_trade_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # StrategyDailyMaxTradeCount
    exit_at_loss: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    exit_at_loss_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    trading_start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)  # StrategyTradingStartTime
    new_order_end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)  # StrategyNewOrderEndTime
    trading_end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)  # StrategyTradingEndTime (square-off)
    round_trip_cost_rate: Mapped[Optional[float]] = mapped_column(Numeric(8, 6), nullable=True)
    # "First order of the day sized at a literal fixed quantity" override
    # (explicit instruction, 2026-09-14: "first order qty 1") — None keeps
    # the normal pool/multiplier-based sizing for every order including
    # the first. See order_backtest.py's EngineConfig.first_order_quantity.
    first_order_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # "Nx margin" sizing per order SEQUENCE NUMBER within the trading day —
    # "first, second and third all three params of strategy" (explicit
    # instruction) — order3 also covers every order past the 3rd ("further
    # orders will take from third"). See order_backtest.py's
    # EngineConfig.order_volume_multipliers / engine_config_from_strategy.
    order1_margin_multiplier: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)
    order2_margin_multiplier: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)
    order3_margin_multiplier: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)
    # Comma-separated pattern names (activity_engine.py's own names, e.g.
    # "vwap_rejection_bear,vwap_rejection_bull") this strategy trades
    # EXCLUSIVELY — real gap found 2026-09-15: order_backtest.py's
    # simulate() otherwise trades every registered BULLISH_PATTERNS/
    # BEARISH_PATTERNS entry blended together, with no way to isolate one
    # specific setup's own P&L. NULL (default) keeps that original
    # behavior unchanged for every strategy that predates this column —
    # see order_backtest.py's EngineConfig.pattern_filter /
    # engine_config_from_strategy.
    pattern_filter: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    # --- risk-management additions from the HINDCOPPER double_top backtest
    # investigation (2026-09-17) — same nullable/additive convention as
    # everything above: null keeps EngineConfig's own default, see
    # order_backtest.py's engine_config_from_strategy for the exact mapping.
    #
    # Cumulative REALIZED GROSS LOSS for the day (losing trades' magnitude
    # only, never netted against the day's winning trades) as a fraction of
    # capital_per_trade — once reached, no new entries for the rest of that
    # trading day. Proven live on real data: this single change cut
    # double_top's loss roughly in half at every SL/TG combo tested. See
    # EngineConfig.max_daily_loss_pct.
    max_daily_loss_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 6), nullable=True)
    # Exit a position early once MACD flips against its own direction,
    # before its fixed stop/target fires — checked candle-by-candle,
    # stop/target still takes priority on the same candle. Tested and
    # found to make results WORSE in every case on HINDCOPPER's 1min data
    # (MACD too laggy/noisy at that granularity) — kept as an available,
    # OFF-by-default option for other symbols/timeframes where it might
    # behave differently, not a recommended default. See
    # EngineConfig.exit_on_macd_reversal.
    exit_on_macd_reversal: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # Comma-separated floats (same string convention as pattern_filter
    # above, parsed to a tuple in engine_config_from_strategy) — "start
    # low, size up on a win streak" position sizing, index i = the
    # multiplier used after i consecutive wins, last value reused past the
    # tuple's length. Tested and found to NOT reliably help (redistributes
    # P&L into fewer/larger swings rather than improving the edge) — kept
    # available, not a recommended default. See
    # EngineConfig.win_streak_multipliers.
    win_streak_multipliers: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Multi-candle order execution (entries/exits spread across several
    # candles at a volume-weighted price instead of shrinking to one
    # candle's own liquidity) plus its own tuning knobs — used throughout
    # this investigation's real-money runs alongside liquidity_safety_
    # divisor/itemized_costs/compounding below. See EngineConfig.
    # spread_fills/max_fill_candles/max_exit_candles/max_fill_price_drift_pct.
    spread_fills: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    max_fill_candles: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_exit_candles: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_fill_price_drift_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 6), nullable=True)
    # Real per-candle liquidity cap: order quantity capped at candle.volume
    # // this divisor. See EngineConfig.liquidity_safety_divisor.
    liquidity_safety_divisor: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Real itemized Indian equity intraday cost model (brokerage/STT/
    # exchange/SEBI/stamp/GST) instead of one flat round_trip_cost_rate.
    # See EngineConfig.itemized_costs.
    itemized_costs: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # Position sizing draws from the running available-fund pool
    # (margin + realized P&L) rather than the fixed original
    # capital_per_trade. See EngineConfig.compounding.
    compounding: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # Pre-entry liquidity GATE (refuses the signal outright, unlike
    # liquidity_safety_divisor above which shrinks/spreads a fill instead):
    # only take a trade when the mean traded volume over the last
    # min_avg_volume_lookback candles exceeds min_avg_volume_multiple x the
    # desired order quantity. Added 2026-09-18, explicit instruction: "mean
    # volume[last 5 candles] > order size". See EngineConfig.
    # min_avg_volume_multiple/min_avg_volume_lookback.
    min_avg_volume_multiple: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)
    min_avg_volume_lookback: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Per-strategy override of activity_engine.ActivityEngine's swing_lookback
    # (how many candles on EACH side confirm a swing high/low, and so how
    # far back structure patterns like Higher-High/Higher-Low BOS can only
    # ever confirm) -- explicit instruction, 2026-10-03: "we can make it
    # variable so that we can test on 3 as well as 7 candles too." Previously
    # only a single GLOBAL EngineSetting row (live trading + every backtest
    # sharing one value) with no way to vary it per backtest run. NULL keeps
    # that global/module default, same as every other field here -- see
    # EngineConfig.swing_lookback / engine_config_from_strategy.
    swing_lookback: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class StrategyConditionGroup(Base):
    """One node in a strategy's condition tree. `operator` ("AND" | "OR")
    combines this group's own direct children — both sub-groups (other
    StrategyConditionGroup rows with parent_group_id pointing here) and
    leaf conditions (StrategyCondition rows with group_id pointing here).
    Arbitrary nesting depth. `parent_group_id` is null only for a
    strategy's root group (see Strategy's own docstring)."""

    __tablename__ = "strategy_condition_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_group_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    operator: Mapped[str] = mapped_column(String(4), nullable=False)  # "AND" | "OR"

    __table_args__ = (
        Index("ix_strategy_condition_groups_strategy", "strategy_id"),
        Index("ix_strategy_condition_groups_parent", "parent_group_id"),
    )


class StrategyCondition(Base):
    """One atomic check (leaf) inside a StrategyConditionGroup.

    Three shapes now, distinguished by which columns are set:
      - EVENT check: element_code names an "event" element (e.g. "doji",
        "double_top") — fired or not; operator/compare_type/values/
        formulas are all null.
      - NUMERIC check (element-registry based, original shape):
        element_code names a "numeric" element (e.g. "rsi", "ma21").
        `operator` is one of >, >=, <, <=, ==, !=. The right-hand side is
        EITHER another element (compare_type="element",
        compared_element_code set — e.g. "MA21 > VWAP") OR a static value
        (compare_type="static", static_value set) OR a static RANGE for
        backtesting parameter sweeps (compare_type="static",
        static_value_min/max/step set instead of static_value — e.g.
        "RSI (40-60, interval 5)"). Exactly one of static_value or the
        min/max/step triple is ever set, never both.
      - FORMULA check (added 2026-09-18, see backend/condition_evaluator.py
        and [[chartink_style_condition_builder_plan]]): `left_formula` is
        set to an arbitrary restricted arithmetic expression over
        condition_evaluator.Field keywords with optional candle offsets
        (e.g. "close[-1] - high[-1]", "vwap[-2]") — a strict superset of
        a plain element_code, so a trivial formula like "rsi" is valid
        too. `operator` + `right_formula` compare it against another
        formula or a bare numeric literal string (e.g. right_formula="45"
        for "rsi >= 45"); `operator` alone with no right_formula means
        left_formula is ITSELF already a full comparison (e.g.
        "rsi >= 45" as one formula). When left_formula is set, it takes
        priority over element_code/compare_type/compared_element_code/
        static_value, which are ignored for this row. element_code is
        nullable specifically to allow a pure formula row with no
        registered single element behind it.
    """

    __tablename__ = "strategy_conditions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(Integer, nullable=False)
    element_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    operator: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    compare_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # "static" | "element" | None
    compared_element_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    static_value: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    static_value_min: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    static_value_max: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    static_value_step: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    left_formula: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    right_formula: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    __table_args__ = (
        Index("ix_strategy_conditions_group", "group_id"),
    )


class Watchlist(Base):
    """A named, curated group of instruments — how a Strategy will
    eventually be pointed at "one or multiple stocks/indexes" for
    backtesting (attaching a Watchlist to a Strategy is deferred until
    backtesting's own instrument-selection UI is designed; this table and
    its CRUD stand alone for now).

    Deliberately separate from SubscribedSymbol.active, which means "the
    live feed is subscribed to this" — a Watchlist's own `active` is just
    normal soft-delete for the watchlist itself, unrelated to feed state.
    """

    __tablename__ = "watchlists"

    # No uniqueness constraint on name: unlike SubscribedSymbol (whose
    # natural-key uniqueness is load-bearing — the whole reactivate-vs-create
    # branch in routes_symbols.py depends on it), nothing here requires
    # watchlist names to be unique, and enforcing it would need the same
    # reactivate-on-recreate handling delete() doesn't otherwise need for a
    # simple soft-deletable entity — not worth the complexity for a
    # constraint nobody asked for.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class WatchlistInstrument(Base):
    """One instrument's membership in a Watchlist. instrument_id references
    SubscribedSymbol.id — same plain-int "FK-like" convention used
    throughout this schema (InstrumentActivity, CandleIndicators, etc.).

    `active` is membership-level soft removal (matches the user's own
    "ID, Instrument, Active" schema and SubscribedSymbol's own soft-delete
    convention) — removing an instrument from a watchlist sets this False
    rather than deleting the row, so membership history isn't lost.
    """

    __tablename__ = "watchlist_instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watchlist_id: Mapped[int] = mapped_column(Integer, nullable=False)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("watchlist_id", "instrument_id", name="uq_watchlist_instrument"),
        Index("ix_watchlist_instruments_watchlist", "watchlist_id"),
    )


class BacktestRunMaster(Base):
    """One backtesting SESSION — mirrors the Trading project's own
    `BTOrderRunMaster` (reviewed read-only via SSH, 2026-09-14). A session
    groups multiple BacktestRun rows that answer one underlying question
    several ways — e.g. "1-min candles vs. 3-min candles" or "RSI 45 vs.
    RSI 50" are each 2 runs under 1 session, not 2 sessions.

    Scoped to EITHER one instrument OR one watchlist, never both — a
    watchlist session expands to one BacktestRun per member instrument at
    execution time ("one run per stock in watchlist", explicit
    instruction), each with its own instrument_id (see BacktestRun). Not
    enforced as a DB CHECK constraint (matches this schema's existing
    convention for similar exactly-one-of invariants, e.g.
    StrategyCondition's static_value vs. min/max/step) — enforced at the
    application layer instead.

    `mode` distinguishes backtesting type 1 ("occurrence_count" — just
    count pattern occurrences, no trade simulation, no strategy_id on its
    runs) from type 2/3 ("strategy_trade" — a system or user-defined
    Strategy actually traded; "system" vs. "user-defined" isn't a
    structural difference, both are just Strategy rows evaluated the same
    way).

    `source` names WHAT requested this run (2026-09-15, explicit: "all
    backtests should be recorded. you can set the source of the backtest
    as RS1/Strategy-abc/RS2 etc.") — free string, e.g. "Strategy-<name>"
    for a normal strategy_trade run (run_backtest.py's own default when
    none is given), "occurrence_backtest" for a manually-run occurrence
    scan, or "RS1"/"RS2"/"RS3" for a run a recommendation system itself
    triggers (not done automatically by recommendation_engine.py today —
    that module deliberately stays separate from backtesting, per its own
    docstring; this field exists so a FUTURE caller that does trigger a
    backtest from RS1/2/3 has somewhere to record that provenance).
    Nullable — every run before this column existed has no source on
    record, which is fine, not an error.

    best_run_id is denormalized (not just derived via a query on demand)
    because Trading's own BestRunID/BestRunParams columns already proved
    this is worth caching — "which run/parameter combination won" is asked
    a lot more often than it's computed, and computing it means scanning
    every sibling run's own BacktestRunResult. Set once every run in the
    session has finished, not maintained incrementally. Meaningless (left
    null) for an occurrence_count session — there's no P&L to rank by.
    """

    __tablename__ = "backtest_run_masters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="strategy_trade")  # "occurrence_count" | "strategy_trade"
    source: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # e.g. "Strategy-<name>", "occurrence_backtest", "RS1"
    instrument_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    watchlist_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    date_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    date_to: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")  # "queued" | "running" | "completed" | "failed"
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    time_elapsed_seconds: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_backtest_run_masters_instrument", "instrument_id"),
        Index("ix_backtest_run_masters_watchlist", "watchlist_id"),
    )


class BacktestRun(Base):
    """One RUN within a BacktestRunMaster session — mirrors Trading's own
    `BTOrderRun`, minus its ~20 hardcoded indicator-parameter columns
    (RSIIN/SKIN/MACDFASTIN/...): those are replaced by BacktestRunParameter
    rows referencing the actual StrategyCondition, so a new indicator never
    needs a new column here.

    "Runs can be different strategies too" (explicit instruction) — nothing
    here ties a run to its siblings' strategy_id, so a session can freely
    mix runs across entirely different Strategy rows for a side-by-side
    comparison, not just parameter sweeps of one strategy. strategy_id is
    nullable — an "occurrence_count"-mode run (see BacktestRunMaster.mode)
    has none, it's just counting pattern occurrences.

    instrument_id is always set, even when the session itself was scoped
    to a watchlist rather than one instrument — a watchlist session
    expands into one run per member, each pinned to its own instrument
    ("one run per stock in watchlist", explicit instruction), so a run is
    never ambiguous about which stock it covers.

    Order-management settings (maxVolPerCall, MaxOrdersAtATime, etc.) are
    NOT duplicated here — they live on the referenced Strategy (see its own
    docstring) and are read from there when the run actually executes.
    timeframe is a single value ("1min", "3min", ...), matching the user's
    own framing of "1-min candles and 3-min candles" as two separate runs,
    not one run spanning both.

    `pattern` is the mirror of `strategy_id` for occurrence_count mode —
    which single pattern name (activity_engine.py's own, e.g. "hammer")
    this run counted occurrences of. Nullable, unused for strategy_trade
    runs (a strategy can involve many patterns at once via pattern_filter).
    """

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_master_id: Mapped[int] = mapped_column(Integer, nullable=False)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pattern: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    error_message: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    time_elapsed_seconds: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_backtest_runs_master", "run_master_id"),
        Index("ix_backtest_runs_instrument", "instrument_id"),
        Index("ix_backtest_runs_strategy", "strategy_id"),
    )


class BacktestRunParameter(Base):
    """The MATERIALIZED side of a parameter sweep: which concrete value a
    given BacktestRun actually used for one of its strategy's RANGED
    StrategyConditions (static_value_min/max/step set). The sweep
    DEFINITION already lives on StrategyCondition itself — nothing here
    duplicates Start/End/Interval the way Trading's own
    `BTStrategyRunParams` does, only the one point in that range this run
    picked.

    One row per (run, ranged condition) — a run sweeping 2 conditions at
    once (e.g. RSI range AND Stochastic range together) gets 2 rows, one
    per condition, each with its own concrete value for this run.
    """

    __tablename__ = "backtest_run_parameters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_condition_id: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)

    __table_args__ = (
        Index("ix_backtest_run_parameters_run", "run_id"),
        UniqueConstraint("run_id", "strategy_condition_id", name="uq_backtest_run_parameter"),
    )


class BacktestRunResult(Base):
    """1:1 aggregate summary for one BacktestRun — mirrors the aggregate
    columns Trading bakes directly onto its own BTOrderRun row (WinCount/
    LooseCount/WinPercentage/RunProfit/Pre-PostPortfolioValue/...), kept as
    its own table instead per this project's existing convention of
    separating a thing's definition/identity from its computed results
    (e.g. CandleToday vs. CandleIndicators).

    NOT carried over from Trading: NoFundCount, JackpotDaysCount,
    LossAtSecondTradeCount/LossAtThirdTradeCount — those all assume a
    shared-portfolio, fund-sufficiency-checked execution model Trading has
    and this project doesn't (yet) — each trade here sizes off its own
    fixed capital_per_trade, not a running shared balance. Worth adding if/
    when that model gets built.
    """

    __tablename__ = "backtest_run_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_ratio: Mapped[Optional[float]] = mapped_column(Numeric(6, 4), nullable=True)
    total_gross_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_expenses: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    total_net_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    pre_run_capital: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    post_run_capital: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    winning_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losing_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flat_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    days_with_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("run_id", name="uq_backtest_run_result_run"),
    )


class BacktestTrade(Base):
    """One trade/order within a BacktestRun — mirrors Trading's own
    `BTOrders`, trimmed to what this project's own order_backtest.py
    (OrderBook._close) already computes per trade rather than Trading's
    full order-lifecycle column set (separate SL/target broker order IDs,
    a monitoring flag, etc. — this project doesn't place real broker
    orders during a backtest, so those don't apply).

    entry_*/exit_* indicator columns mirror Trading's own
    `BTOrderMomentumIndicators1M/3M/5M` (RSI/Stochastic/MACD/VWAP/MA/ATR/BB
    snapshotted at both IN and OUT) — added explicitly so a failing or
    winning trade's indicator state is queryable directly, without a join,
    for exactly the kind of "why did this fail" analysis those tables
    existed for. All nullable (same warm-up-period convention as
    CandleIndicators itself, which these values should generally match at
    the same instrument/timeframe/ts — captured independently per trade
    here rather than joined at query time, so a trade's own recorded
    reasoning never drifts if CandleIndicators rows are ever recomputed).
    """

    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    pattern: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)  # "bull" | "bear"
    entry_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    stop_loss: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    target: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    exit_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    exit_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(16), nullable=False)  # "target_hit" | "stop_hit" | "eod_squareoff" | "run_end"
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # How much of the run's shared fund pool this trade locked up while
    # open — see order_backtest.py's OrderBook.available_fund /
    # [[shared_fund_pool_order_backtest]]. Nullable: older trade rows
    # (persisted before the pooled-fund model) never had this.
    margin_used: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    gross_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    expenses: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    net_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    entry_rsi: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    entry_macd_line: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    entry_macd_signal: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    entry_stoch_k: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    entry_stoch_d: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    entry_vwap: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    entry_ma21: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    entry_ma50: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    entry_atr: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    entry_bb_upper: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    entry_bb_middle: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    entry_bb_lower: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    # classified STATE alongside the raw values above -- added 2026-09-18
    # (see [[backtest_column_picker_report_plan]]), same value+state
    # convention PatternOutcome already uses (indicators.classify_rsi/
    # classify_macd/classify_stochastic, a pure function of the value
    # columns right above), so a report can pick "RSI State entry" as a
    # column without recomputing it from the raw value at read time.
    entry_rsi_state: Mapped[Optional[str]] = mapped_column(OversoldNeutralOverboughtCode, nullable=True)
    entry_macd_state: Mapped[Optional[str]] = mapped_column(BullishBearishCode, nullable=True)
    entry_stoch_state: Mapped[Optional[str]] = mapped_column(OversoldNeutralOverboughtCode, nullable=True)
    # Was this indicator climbing/falling/flat over the
    # INDICATOR_TREND_LOOKBACK candles ending at (and including) this one —
    # same convention as PatternOutcome.entry_rsi_trend/etc (added 2026-09-18,
    # explicit instruction to keep the SAME shared lookback rather than a
    # wider one-off just for this grid, so a trend label means the same
    # thing everywhere it appears in the app).
    entry_rsi_trend: Mapped[Optional[str]] = mapped_column(TrendCode, nullable=True)
    entry_macd_trend: Mapped[Optional[str]] = mapped_column(TrendCode, nullable=True)
    entry_stoch_trend: Mapped[Optional[str]] = mapped_column(TrendCode, nullable=True)

    exit_rsi: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_macd_line: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_macd_signal: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_stoch_k: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_stoch_d: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_vwap: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_ma21: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_ma50: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_atr: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_bb_upper: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_bb_middle: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_bb_lower: Mapped[Optional[float]] = mapped_column(Numeric(18, 6), nullable=True)
    exit_rsi_state: Mapped[Optional[str]] = mapped_column(OversoldNeutralOverboughtCode, nullable=True)
    exit_macd_state: Mapped[Optional[str]] = mapped_column(BullishBearishCode, nullable=True)
    exit_stoch_state: Mapped[Optional[str]] = mapped_column(OversoldNeutralOverboughtCode, nullable=True)
    exit_rsi_trend: Mapped[Optional[str]] = mapped_column(TrendCode, nullable=True)
    exit_macd_trend: Mapped[Optional[str]] = mapped_column(TrendCode, nullable=True)
    exit_stoch_trend: Mapped[Optional[str]] = mapped_column(TrendCode, nullable=True)

    __table_args__ = (
        Index("ix_backtest_trades_run", "run_id"),
        Index("ix_backtest_trades_run_entry_ts", "run_id", "entry_ts"),
    )


class BacktestDayResult(Base):
    """One trading day's rollup within a BacktestRun — mirrors Trading's
    own `BTOrdersRunDayReport`."""

    __tablename__ = "backtest_day_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    net_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)

    __table_args__ = (
        Index("ix_backtest_day_results_run", "run_id"),
        UniqueConstraint("run_id", "date", name="uq_backtest_day_result"),
    )


class InstrumentWatchExclusion(Base):
    """Per-instrument "what to watch" on Market Watch -- explicit
    instruction, 2026-10-03: "for each added instrument, I should be able
    to setup what to watch, like formations, indicators, strategies. By
    default, all should be selected."

    Stores only the EXCLUSIONS (what's been turned off), not the full
    selection -- an instrument with zero rows here watches everything,
    matching "by default, all should be selected" exactly without a
    41-pattern-plus-every-strategy row per instrument for the common case
    of "watch everything." item_code is a PatternDefinition.code (e.g.
    "doji") when item_type="pattern", or a Strategy.id as a string when
    item_type="strategy" -- same "one string code column regardless of
    underlying kind" convention StrategyCondition.element_code already
    uses, not two nullable type-specific columns.

    Display-only for v1 (Market Watch hides an excluded item from what it
    shows for that instrument) -- does NOT stop ActivityEngine from
    detecting it. Detection is currently one shared pass across every
    subscribed instrument; scoping detection itself per instrument would
    be a real engine change, deferred until actually needed.
    """

    __tablename__ = "instrument_watch_exclusions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(Integer, nullable=False)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "pattern" | "strategy"
    item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("instrument_id", "item_type", "item_code", name="uq_watch_exclusion"),
        Index("ix_watch_exclusions_instrument", "instrument_id"),
    )
