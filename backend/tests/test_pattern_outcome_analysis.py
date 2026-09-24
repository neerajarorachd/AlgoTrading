from datetime import datetime, timedelta, timezone

import pytest

from db.models import CandleHistorical, CandleIndicators, InstrumentActivity, PatternOutcome, SubscribedSymbol
from pattern_outcome_analysis import (
    DEFAULT_CHECKPOINTS,
    _compute_outcome,
    _direction_for,
    _truncate_at_data_gap,
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
    """close = 100 + i, high = close + 1, low = close - 1, for i in 0..n-1.
    CandleHistorical (the persistent archive analyze_instrument actually
    reads from, 2026-09-15 fix — it used to read CandleToday, which is
    only ever a few recent days, not real historical data), not
    CandleToday."""
    with session_factory() as session:
        session.add_all([
            CandleHistorical(
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


def _insert_indicators(session_factory, instrument_id, minute, timeframe="1min", **values):
    ts = _BASE_TS + timedelta(minutes=minute)
    with session_factory() as session:
        session.add(CandleIndicators(instrument_id=instrument_id, timeframe=timeframe, ts=ts, **values))
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


# --------------------------------------------------------------------- _truncate_at_data_gap
# Real bug found live 2026-09-15: HINDCOPPER's own CandleHistorical has a
# genuine 440-day data-collection hole (2025-04-01 to 2026-06-16, NOT a
# market closure). Without gap-awareness, a checkpoint computed across it
# reported a price move spanning MONTHS as if it were "30 candles later" —
# confirmed live: a fake +134% "30-candle move" traced directly to it.

def test_truncate_at_data_gap_cuts_window_at_a_real_gap():
    anchor = _BASE_TS
    window = [
        (_BASE_TS + timedelta(minutes=1), 101.0, 102.0, 100.0),
        (_BASE_TS + timedelta(minutes=2), 102.0, 103.0, 101.0),
        (_BASE_TS + timedelta(days=440), 500.0, 501.0, 499.0),  # the gap
        (_BASE_TS + timedelta(days=440, minutes=1), 501.0, 502.0, 500.0),
    ]
    truncated = _truncate_at_data_gap(anchor, window)
    assert len(truncated) == 2  # only the 2 real candles before the gap survive
    assert truncated[-1][1] == 102.0


def test_truncate_at_data_gap_cuts_at_the_very_first_candle_if_it_is_already_past_the_gap():
    """The detection candle itself can sit right before a hole — window[0]
    alone must be checked against anchor_ts, not just gaps BETWEEN later
    candles in the window."""
    anchor = _BASE_TS
    window = [(_BASE_TS + timedelta(days=440), 500.0, 501.0, 499.0)]
    truncated = _truncate_at_data_gap(anchor, window)
    assert truncated == []


def test_truncate_at_data_gap_tolerates_a_normal_weekend_closure():
    anchor = _BASE_TS
    window = [
        (_BASE_TS + timedelta(minutes=1), 101.0, 102.0, 100.0),
        (_BASE_TS + timedelta(days=3), 103.0, 104.0, 102.0),  # a real 3-day weekend/holiday gap
        (_BASE_TS + timedelta(days=3, minutes=1), 104.0, 105.0, 103.0),
    ]
    truncated = _truncate_at_data_gap(anchor, window)
    assert truncated == window  # untouched -- well within _MAX_NORMAL_GAP


def test_truncate_at_data_gap_no_gap_returns_window_unchanged():
    anchor = _BASE_TS
    window = [(_BASE_TS + timedelta(minutes=i), 100.0 + i, 101.0 + i, 99.0 + i) for i in range(1, 6)]
    assert _truncate_at_data_gap(anchor, window) == window


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


def test_analyze_instrument_truncates_a_forward_window_at_a_real_data_gap(session_factory):
    """Real bug found live 2026-09-15: a 440-day hole in HINDCOPPER's own
    CandleHistorical data let a checkpoint report a price move spanning
    MONTHS as "30 candles later." Reproduced at the engine level: 3 real
    candles, then a huge jump in ts (not a normal weekend), then 2 more
    candles far on the other side — window_candles/checkpoints must stop
    at the gap, never silently include the far-side candles."""
    _register_symbol(session_factory)
    with session_factory() as session:
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id
        session.add_all([
            CandleHistorical(
                symbol=SYMBOL, exchange_segment=SEG, timeframe="1min", ts=_BASE_TS + timedelta(minutes=i),
                open_price=100.0 + i, high_price=101.0 + i, low_price=99.0 + i, close_price=100.0 + i, volume=100,
            )
            for i in range(4)  # entry candle (minute 0) + 3 real forward candles (1, 2, 3)
        ])
        far_side_start = _BASE_TS + timedelta(days=440)
        session.add_all([
            CandleHistorical(
                symbol=SYMBOL, exchange_segment=SEG, timeframe="1min", ts=far_side_start + timedelta(minutes=i),
                open_price=500.0, high_price=501.0, low_price=499.0, close_price=500.0, volume=100,
            )
            for i in range(2)  # far side of the gap -- must NOT be treated as candles 4-5
        ])
        session.commit()
    _insert_activity(session_factory, instrument_id, minute=0, activity="doji")

    analyze_instrument(session_factory, SYMBOL, SEG, "1min")

    with session_factory() as session:
        row = session.query(PatternOutcome).filter_by(pattern="doji").one()
    assert row.window_candles == 3  # only the 3 real candles before the gap
    assert row.pct_change_5 is None  # never reaches checkpoint 5 -- the far-side candles don't count
    # max_favorable/adverse must come from the 3 real candles (highs 102..104), NOT the far-side 501.
    assert float(row.max_favorable_pct) == pytest.approx((104.0 - 100.0) / 100.0)


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


# --------------------------------------------------------------------- indicator value + state (2026-09-15)

def test_analyze_instrument_stores_indicator_value_and_classified_state(session_factory):
    """"store indicator state + values" (explicit instruction) — matched
    by exact ts against CandleIndicators, no bisect needed (an activity
    and its indicator snapshot are always written for the same candle)."""
    _register_symbol(session_factory)
    _insert_ramp_candles(session_factory, n=26)
    with session_factory() as session:
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id
    _insert_activity(session_factory, instrument_id, minute=5, activity="rsi_cross_above_60", activity_type="indicator")
    _insert_indicators(
        session_factory, instrument_id, minute=5,
        rsi=75.0, macd_line=1.5, macd_signal=1.0, stoch_k=85.0,
    )

    analyze_instrument(session_factory, SYMBOL, SEG, "1min")

    with session_factory() as session:
        row = session.query(PatternOutcome).one()
    assert float(row.entry_rsi) == 75.0
    assert row.entry_rsi_state == "overbought"
    assert float(row.entry_macd_line) == 1.5
    assert float(row.entry_macd_signal) == 1.0
    assert row.entry_macd_state == "bullish"
    assert float(row.entry_stoch_k) == 85.0
    assert row.entry_stoch_state == "overbought"


def test_analyze_instrument_leaves_indicator_fields_null_without_a_matching_snapshot(session_factory):
    """No CandleIndicators row was ever written for this exact occurrence
    (e.g. a replay that skipped ActivityEngine.flush() for the indicator
    side) — every entry_*/state field comes back None, not an error."""
    _register_symbol(session_factory)
    _insert_ramp_candles(session_factory, n=26)
    with session_factory() as session:
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id
    _insert_activity(session_factory, instrument_id, minute=5, activity="hammer")
    # no CandleIndicators row at all

    analyze_instrument(session_factory, SYMBOL, SEG, "1min")

    with session_factory() as session:
        row = session.query(PatternOutcome).one()
    assert row.entry_rsi is None
    assert row.entry_rsi_state is None
    assert row.entry_macd_state is None
    assert row.entry_stoch_state is None


def test_analyze_instrument_indicator_snapshot_matched_by_exact_timestamp_only(session_factory):
    """A CandleIndicators row at a DIFFERENT candle must not leak into an
    activity's own snapshot — exact ts match only, not nearest-before."""
    _register_symbol(session_factory)
    _insert_ramp_candles(session_factory, n=26)
    with session_factory() as session:
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id
    _insert_activity(session_factory, instrument_id, minute=5, activity="hammer")
    _insert_indicators(session_factory, instrument_id, minute=4, rsi=40.0)  # one candle earlier

    analyze_instrument(session_factory, SYMBOL, SEG, "1min")

    with session_factory() as session:
        row = session.query(PatternOutcome).one()
    assert row.entry_rsi is None


# --------------------------------------------------------------------- indicator trend (2026-09-15, "lets do it next")

def test_analyze_instrument_stores_indicator_trend_from_lookback_series(session_factory):
    """RSI climbing 30 -> 40 -> 50 -> 60 -> 70 over the 5 candles ending at
    (and including) the activity's own candle should classify as
    "increasing" — the lookback series comes from CandleIndicators rows
    already bulk-loaded, not a fresh query per activity."""
    _register_symbol(session_factory)
    _insert_ramp_candles(session_factory, n=26)
    with session_factory() as session:
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id
    for minute, rsi in [(1, 30.0), (2, 40.0), (3, 50.0), (4, 60.0), (5, 70.0)]:
        _insert_indicators(session_factory, instrument_id, minute=minute, rsi=rsi)
    _insert_activity(session_factory, instrument_id, minute=5, activity="hammer")

    analyze_instrument(session_factory, SYMBOL, SEG, "1min")

    with session_factory() as session:
        row = session.query(PatternOutcome).one()
    assert row.entry_rsi_trend == "increasing"


def test_analyze_instrument_indicator_trend_lookback_does_not_reach_before_available_data(session_factory):
    """Only 2 indicator rows exist (minute 4 and 5) — classify_series_trend
    should use just those, not error over a shorter-than-lookback window."""
    _register_symbol(session_factory)
    _insert_ramp_candles(session_factory, n=26)
    with session_factory() as session:
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id
    _insert_indicators(session_factory, instrument_id, minute=4, rsi=70.0)
    _insert_indicators(session_factory, instrument_id, minute=5, rsi=40.0)
    _insert_activity(session_factory, instrument_id, minute=5, activity="hammer")

    analyze_instrument(session_factory, SYMBOL, SEG, "1min")

    with session_factory() as session:
        row = session.query(PatternOutcome).one()
    assert row.entry_rsi_trend == "decreasing"


def test_analyze_instrument_indicator_trend_null_without_any_snapshot(session_factory):
    _register_symbol(session_factory)
    _insert_ramp_candles(session_factory, n=26)
    with session_factory() as session:
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id
    _insert_activity(session_factory, instrument_id, minute=5, activity="hammer")

    analyze_instrument(session_factory, SYMBOL, SEG, "1min")

    with session_factory() as session:
        row = session.query(PatternOutcome).one()
    assert row.entry_rsi_trend is None
    assert row.entry_macd_trend is None
    assert row.entry_stoch_trend is None
