from datetime import datetime, timedelta, timezone

import pytest

from db.models import CandleToday, InstrumentActivity, PatternOutcome, SubscribedSymbol
from pattern_outcome_analysis import (
    DEFAULT_CHECKPOINTS,
    _compute_outcome,
    _direction_for,
    analyze_instrument,
)

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"
_BASE_TS = datetime(2026, 9, 11, 9, 15, tzinfo=timezone.utc)


def _register_symbol(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()


def _insert_ramp_candles(session_factory, n=26, timeframe="1min"):
    """close = 100 + i, high = close + 1, low = close - 1, for i in 0..n-1."""
    with session_factory() as session:
        session.add_all([
            CandleToday(
                symbol=SYMBOL, exchange_segment=SEG, timeframe=timeframe,
                ts=_BASE_TS + timedelta(minutes=i),
                open_price=100.0 + i, high_price=101.0 + i, low_price=99.0 + i,
                close_price=100.0 + i, volume=100,
            )
            for i in range(n)
        ])
        session.commit()


def _insert_activity(session_factory, instrument_id, minute, activity, activity_type="candle_pattern", timeframe="1min"):
    ts = _BASE_TS + timedelta(minutes=minute)
    with session_factory() as session:
        session.add(InstrumentActivity(
            instrument_id=instrument_id, timeframe=timeframe, ts=ts,
            activity_type=activity_type, activity=activity, intensity=1.0,
            open_price=100.0 + minute, high_price=101.0 + minute,
            low_price=99.0 + minute, close_price=100.0 + minute,
        ))
        session.commit()


# --------------------------------------------------------------------- _direction_for

def test_direction_for_known_bullish_and_bearish_patterns():
    assert _direction_for("rsi_cross_above_60") == "bull"
    assert _direction_for("double_top") == "bear"


def test_direction_for_unclassified_pattern_is_none():
    assert _direction_for("doji") is None
    assert _direction_for("rectangle") is None


# --------------------------------------------------------------------- _compute_outcome (pure)

def test_compute_outcome_full_window():
    entry = 105.0
    # window = 20 candles after entry, closes 106..125, highs = close+1, lows = close-1
    window = [(None, 106.0 + i, 107.0 + i, 105.0 + i) for i in range(20)]
    outcome = _compute_outcome(entry, window, DEFAULT_CHECKPOINTS)
    assert outcome["window_candles"] == 20
    assert outcome["pct_change_5"] == pytest.approx((110.0 - entry) / entry)
    assert outcome["pct_change_10"] == pytest.approx((115.0 - entry) / entry)
    assert outcome["pct_change_15"] == pytest.approx((120.0 - entry) / entry)
    assert outcome["pct_change_20"] == pytest.approx((125.0 - entry) / entry)
    assert outcome["max_favorable_pct"] == pytest.approx((126.0 - entry) / entry)  # high of the last candle
    assert outcome["max_adverse_pct"] == pytest.approx((105.0 - entry) / entry)  # low of the first candle = 0


def test_compute_outcome_partial_window():
    entry = 100.0
    window = [(None, 101.0, 102.0, 100.5), (None, 103.0, 104.0, 102.0)]  # only 2 candles available
    outcome = _compute_outcome(entry, window, DEFAULT_CHECKPOINTS)
    assert outcome["window_candles"] == 2
    assert outcome["pct_change_5"] is None
    assert outcome["pct_change_10"] is None
    assert outcome["pct_change_15"] is None
    assert outcome["pct_change_20"] is None
    assert outcome["max_favorable_pct"] == pytest.approx((104.0 - entry) / entry)
    assert outcome["max_adverse_pct"] == pytest.approx((100.5 - entry) / entry)


def test_compute_outcome_empty_window():
    outcome = _compute_outcome(100.0, [], DEFAULT_CHECKPOINTS)
    assert outcome["window_candles"] == 0
    assert outcome["pct_change_5"] is None
    assert outcome["max_favorable_pct"] is None
    assert outcome["max_adverse_pct"] is None


# --------------------------------------------------------------------- analyze_instrument (engine-level)

def test_analyze_instrument_computes_outcomes_for_a_full_window(session_factory):
    _register_symbol(session_factory)
    _insert_ramp_candles(session_factory, n=26)
    with session_factory() as session:
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id
    _insert_activity(session_factory, instrument_id, minute=5, activity="rsi_cross_above_60", activity_type="indicator")

    written = analyze_instrument(session_factory, SYMBOL, SEG, "1min")
    assert written == 1

    with session_factory() as session:
        row = session.query(PatternOutcome).one()
    assert row.instrument_id == instrument_id
    assert row.pattern == "rsi_cross_above_60"
    assert row.activity_type == "indicator"
    assert row.direction == "bull"
    assert float(row.entry_price) == 105.0
    assert row.window_candles == 20
    assert float(row.pct_change_20) == pytest.approx((125.0 - 105.0) / 105.0)


def test_analyze_instrument_handles_a_partial_window_near_end_of_day(session_factory):
    _register_symbol(session_factory)
    _insert_ramp_candles(session_factory, n=26)
    with session_factory() as session:
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id
    # only 2 candles remain after this one (indices 24, 25 out of 0..25)
    _insert_activity(session_factory, instrument_id, minute=23, activity="doji")

    analyze_instrument(session_factory, SYMBOL, SEG, "1min")

    with session_factory() as session:
        row = session.query(PatternOutcome).filter_by(pattern="doji").one()
    assert row.direction is None
    assert row.window_candles == 2
    assert row.pct_change_5 is None
    assert row.max_favorable_pct is not None


def test_analyze_instrument_is_idempotent(session_factory):
    _register_symbol(session_factory)
    _insert_ramp_candles(session_factory, n=26)
    with session_factory() as session:
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id
    _insert_activity(session_factory, instrument_id, minute=5, activity="double_top", activity_type="graph_formation")

    first = analyze_instrument(session_factory, SYMBOL, SEG, "1min")
    second = analyze_instrument(session_factory, SYMBOL, SEG, "1min")
    assert first == 1
    assert second == 0

    with session_factory() as session:
        assert session.query(PatternOutcome).count() == 1


def test_analyze_instrument_returns_zero_when_symbol_not_registered(session_factory):
    assert analyze_instrument(session_factory, "UNKNOWN", SEG, "1min") == 0


def test_analyze_instrument_returns_zero_when_no_activities(session_factory):
    _register_symbol(session_factory)
    _insert_ramp_candles(session_factory, n=26)
    assert analyze_instrument(session_factory, SYMBOL, SEG, "1min") == 0
