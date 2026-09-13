"""StrategyElement reads/writes — the catalog of everything a Strategy
condition can reference (see db/models.py's StrategyElement)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from db.models import StrategyElement
from db.session import session_scope

# Numeric elements — read from CandleIndicators (or CandleToday for raw
# OHLCV) — each a (code, source, description) triple, matching
# activity_engine.PATTERN_CATALOG's own (code, kind, description) shape
# used for event elements below.
NUMERIC_ELEMENT_CATALOG: Tuple[Tuple[str, str, str], ...] = (
    ("rsi", "candle_indicators", "RSI(14)"),
    ("macd_line", "candle_indicators", "MACD line"),
    ("macd_signal", "candle_indicators", "MACD signal line"),
    ("stoch_k", "candle_indicators", "Stochastic %K"),
    ("stoch_d", "candle_indicators", "Stochastic %D"),
    ("vwap", "candle_indicators", "Volume-weighted average price"),
    ("ma21", "candle_indicators", "21-period moving average"),
    ("ma50", "candle_indicators", "50-period moving average"),
    ("atr", "candle_indicators", "ATR(14)"),
    ("bb_upper", "candle_indicators", "Bollinger Band upper"),
    ("bb_middle", "candle_indicators", "Bollinger Band middle"),
    ("bb_lower", "candle_indicators", "Bollinger Band lower"),
    ("open", "candle", "Candle open price"),
    ("high", "candle", "Candle high price"),
    ("low", "candle", "Candle low price"),
    ("close", "candle", "Candle close price"),
    ("volume", "candle", "Candle volume"),
)


def seed_strategy_elements(session_factory, event_catalog: Sequence[Tuple[str, str, str]]) -> None:
    """Idempotent upsert of the full element catalog — every
    event_catalog entry (code, kind, description — pass
    activity_engine.PATTERN_CATALOG) becomes an "event" element, plus
    this module's own NUMERIC_ELEMENT_CATALOG as "numeric" elements.
    Safe to call every time (existing rows updated in place), same shape
    as LibActivities.seed_pattern_definitions."""
    with session_scope(session_factory) as session:
        for code, _kind, description in event_catalog:
            _upsert(session, code, "event", "instrument_activity", description)
        for code, source, description in NUMERIC_ELEMENT_CATALOG:
            _upsert(session, code, "numeric", source, description)


def _upsert(session, code: str, element_type: str, source: str, description: str) -> None:
    row = session.query(StrategyElement).filter_by(code=code).one_or_none()
    if row is None:
        row = StrategyElement(code=code, element_type=element_type, source=source, description=description)
        session.add(row)
    else:
        row.element_type = element_type
        row.source = source
        row.description = description
        row.active = True


def get_all(session) -> List[StrategyElement]:
    return (
        session.query(StrategyElement)
        .filter_by(active=True)
        .order_by(StrategyElement.element_type, StrategyElement.code)
        .all()
    )


def get_by_code(session, code: str) -> "StrategyElement | None":
    return session.query(StrategyElement).filter_by(code=code).one_or_none()
