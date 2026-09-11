from datetime import datetime, timedelta, timezone

import pytest

from activity_engine import (
    ActivityEngine,
    bb_squeeze_intensity,
    bb_widening_intensity,
    compute_bollinger,
    detect_bb_squeeze,
    detect_bb_widening,
    detect_bearish_engulfing,
    detect_bullish_engulfing,
    detect_dark_cloud_cover,
    detect_doji,
    detect_hammer,
    detect_piercing_line,
    detect_price_vwap_divergence,
    detect_shooting_star,
    detect_three_black_crows,
    detect_three_white_soldiers,
    detect_tweezer_bottom,
    detect_tweezer_top,
    detect_vwap_gap_fill,
    doji_intensity,
    engulfing_intensity,
    hammer_intensity,
    piercing_dark_cloud_intensity,
    seed_pattern_definitions,
    shooting_star_intensity,
    tweezer_bottom_intensity,
    tweezer_top_intensity,
    vwap_divergence_intensity,
    vwap_gap_fill_intensity,
)
from brokers.models import Candle
from db.models import InstrumentActivity, PatternDefinition, SubscribedSymbol

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"


def _candle(ts_minute, open, high, low, close, timeframe="1min"):
    return Candle(
        symbol=SYMBOL, timeframe=timeframe,
        timestamp=datetime(2026, 9, 11, 9, ts_minute, tzinfo=timezone.utc),
        open=open, high=high, low=low, close=close, volume=100,
    )


_BASE_TS = datetime(2026, 9, 11, 9, 15, tzinfo=timezone.utc)


def _pc(i, close, volume=1000, timeframe="1min"):
    """A price-action test candle: flat body (open == close) so candlestick
    shape detectors stay out of the way, high/low +-1 around close so a
    constant-volume run's VWAP typical price reduces to the close itself."""
    return Candle(
        symbol=SYMBOL, timeframe=timeframe,
        timestamp=_BASE_TS + timedelta(minutes=i),
        open=close, high=close + 1.0, low=close - 1.0, close=close, volume=volume,
    )


# --------------------------------------------------------------------- single-candle detectors

def test_detect_doji_true_for_near_equal_open_close():
    c = _candle(0, open=100.0, high=101.0, low=99.0, close=100.05)
    assert detect_doji(c) is True


def test_detect_doji_false_for_a_normal_body():
    c = _candle(0, open=100.0, high=101.0, low=99.0, close=100.8)
    assert detect_doji(c) is False


def test_detect_doji_false_for_zero_range():
    c = _candle(0, open=100.0, high=100.0, low=100.0, close=100.0)
    assert detect_doji(c) is False


def test_detect_hammer_true_for_small_top_body_long_lower_wick():
    # body 100->100.5 (0.5), lower wick down to 97 (3.5, >=2x body), tiny upper wick
    c = _candle(0, open=100.0, high=100.6, low=97.0, close=100.5)
    assert detect_hammer(c) is True


def test_detect_hammer_false_when_upper_wick_too_long():
    c = _candle(0, open=100.0, high=103.0, low=97.0, close=100.5)
    assert detect_hammer(c) is False


def test_detect_shooting_star_true_for_small_bottom_body_long_upper_wick():
    c = _candle(0, open=100.0, high=103.5, low=99.9, close=100.1)
    assert detect_shooting_star(c) is True


def test_detect_shooting_star_false_for_a_hammer_shape():
    c = _candle(0, open=100.0, high=100.6, low=97.0, close=100.5)
    assert detect_shooting_star(c) is False


# --------------------------------------------------------------------- intensity

def test_hammer_intensity_is_wick_to_body_ratio():
    # body=0.5, lower_wick=3.0 -> 6x — a strong hammer, well past the 2x floor
    c = _candle(0, open=100.0, high=100.6, low=97.0, close=100.5)
    assert hammer_intensity(c) == 6.0


def test_hammer_intensity_higher_for_a_longer_wick_same_body():
    weak = _candle(0, open=100.0, high=100.6, low=98.0, close=100.5)  # wick=2.0, body=0.5 -> 4x
    strong = _candle(1, open=100.0, high=100.6, low=95.0, close=100.5)  # wick=5.0, body=0.5 -> 10x
    assert hammer_intensity(strong) > hammer_intensity(weak)


def test_shooting_star_intensity_is_wick_to_body_ratio():
    # body=0.1, upper_wick=3.4 -> 34x
    c = _candle(0, open=100.0, high=103.5, low=99.9, close=100.1)
    assert shooting_star_intensity(c) == pytest.approx(34.0)


def test_doji_intensity_is_range_to_body_ratio():
    # body=0.05, range=2.0 -> 40x
    c = _candle(0, open=100.0, high=101.0, low=99.0, close=100.05)
    assert doji_intensity(c) == pytest.approx(40.0)


def test_doji_intensity_is_infinite_for_a_perfect_doji():
    c = _candle(0, open=100.0, high=101.0, low=99.0, close=100.0)  # open == close exactly
    assert doji_intensity(c) == float("inf")


# --------------------------------------------------------------------- two-candle detectors

def test_detect_bullish_engulfing_true():
    candles = [
        _candle(0, open=102.0, high=102.2, low=99.8, close=100.0),   # bearish, body=2.0
        _candle(1, open=99.5, high=102.6, low=99.4, close=102.5),    # bullish, body=3.0, engulfs
    ]
    assert detect_bullish_engulfing(candles) is True
    assert detect_bearish_engulfing(candles) is False
    assert engulfing_intensity(candles) == pytest.approx(1.5)  # 3.0 / 2.0


def test_detect_bullish_engulfing_false_when_body_not_fully_contained():
    candles = [
        _candle(0, open=102.0, high=102.2, low=99.8, close=100.0),
        _candle(1, open=100.5, high=102.6, low=99.9, close=102.5),  # opens above a.close — doesn't engulf
    ]
    assert detect_bullish_engulfing(candles) is False


def test_detect_bearish_engulfing_true():
    candles = [
        _candle(0, open=100.0, high=102.2, low=99.8, close=102.0),   # bullish, body=2.0
        _candle(1, open=102.5, high=102.6, low=99.4, close=99.5),    # bearish, body=3.0, engulfs
    ]
    assert detect_bearish_engulfing(candles) is True
    assert detect_bullish_engulfing(candles) is False


def test_detect_piercing_line_true():
    # A: long bearish, open=110 close=105 (body=5, midpoint=107.5), low=104.5
    a = _candle(0, open=110.0, high=110.2, low=104.5, close=105.0)
    # B: opens below A's low, closes above the midpoint but below A's open
    b = _candle(1, open=104.0, high=108.2, low=103.8, close=108.0)
    candles = [a, b]
    assert detect_piercing_line(candles) is True
    assert detect_dark_cloud_cover(candles) is False
    assert piercing_dark_cloud_intensity(candles) == pytest.approx(0.2)  # |108-107.5| / 2.5


def test_detect_piercing_line_false_when_it_fully_engulfs_instead():
    a = _candle(0, open=110.0, high=110.2, low=104.5, close=105.0)
    b = _candle(1, open=104.0, high=110.3, low=103.8, close=110.1)  # closes above A's open
    assert detect_piercing_line([a, b]) is False


def test_detect_dark_cloud_cover_true():
    # A: long bullish, open=100 close=105 (body=5, midpoint=102.5), high=105.5
    a = _candle(0, open=100.0, high=105.5, low=99.8, close=105.0)
    # B: opens above A's high, closes below the midpoint but above A's open
    b = _candle(1, open=106.0, high=106.2, low=101.8, close=102.0)
    candles = [a, b]
    assert detect_dark_cloud_cover(candles) is True
    assert detect_piercing_line(candles) is False
    assert piercing_dark_cloud_intensity(candles) == pytest.approx(0.2)  # |102-102.5| / 2.5


def test_detect_tweezer_bottom_true():
    a = _candle(0, open=102.0, high=102.2, low=99.5, close=100.0)   # bearish
    b = _candle(1, open=100.2, high=102.3, low=99.52, close=102.0)  # bullish, matching low
    candles = [a, b]
    assert detect_tweezer_bottom(candles) is True
    assert detect_tweezer_top(candles) is False
    assert tweezer_bottom_intensity(candles) > 100  # lows only 0.02 apart


def test_detect_tweezer_bottom_false_when_lows_dont_match():
    a = _candle(0, open=102.0, high=102.2, low=99.5, close=100.0)
    b = _candle(1, open=100.2, high=102.3, low=98.0, close=102.0)  # low far from a's
    assert detect_tweezer_bottom([a, b]) is False


def test_detect_tweezer_top_true():
    a = _candle(0, open=100.0, high=102.5, low=99.8, close=102.0)   # bullish
    b = _candle(1, open=102.3, high=102.52, low=100.0, close=100.5)  # bearish, matching high
    candles = [a, b]
    assert detect_tweezer_top(candles) is True
    assert detect_tweezer_bottom(candles) is False


# --------------------------------------------------------------------- three-candle detectors

def test_detect_three_white_soldiers_true_for_a_steady_climb():
    candles = [
        _candle(0, open=100.0, high=102.1, low=99.9, close=102.0),
        _candle(1, open=101.0, high=104.1, low=100.9, close=104.0),
        _candle(2, open=103.0, high=106.1, low=102.9, close=106.0),
    ]
    assert detect_three_white_soldiers(candles) is True
    assert detect_three_black_crows(candles) is False


def test_detect_three_white_soldiers_false_when_not_enough_candles():
    candles = [_candle(0, 100, 102, 99, 101.9), _candle(1, 101, 104, 100, 103.9)]
    assert detect_three_white_soldiers(candles) is False


def test_detect_three_white_soldiers_false_when_gap_opens_outside_prior_body():
    candles = [
        _candle(0, open=100.0, high=102.1, low=99.9, close=102.0),
        _candle(1, open=103.0, high=105.1, low=102.9, close=105.0),  # opens above prior close — a gap
        _candle(2, open=104.0, high=107.1, low=103.9, close=107.0),
    ]
    assert detect_three_white_soldiers(candles) is False


def test_detect_three_black_crows_true_for_a_steady_decline():
    candles = [
        _candle(0, open=106.0, high=106.1, low=103.9, close=104.0),
        _candle(1, open=104.5, high=104.6, low=101.9, close=102.0),
        _candle(2, open=102.5, high=102.6, low=99.9, close=100.0),
    ]
    assert detect_three_black_crows(candles) is True
    assert detect_three_white_soldiers(candles) is False


# --------------------------------------------------------------------- Bollinger Bands (pure)

def test_compute_bollinger_none_before_full_window():
    assert compute_bollinger([100.0] * 19) is None


def test_compute_bollinger_zero_width_for_constant_closes():
    bb = compute_bollinger([100.0] * 20)
    assert bb is not None
    assert bb.middle == 100.0
    assert bb.width == 0.0


def test_compute_bollinger_widens_with_more_spread():
    tight = compute_bollinger([100.0, 100.1] * 10)
    wide = compute_bollinger([90.0, 110.0] * 10)
    assert wide.width > tight.width


def test_detect_bb_squeeze_true_for_a_new_low_width():
    widths = [0.05] * 19 + [0.02]
    assert detect_bb_squeeze(widths) is True
    assert detect_bb_widening(widths) is False


def test_detect_bb_widening_true_for_a_new_high_width():
    widths = [0.02] * 19 + [0.05]
    assert detect_bb_widening(widths) is True
    assert detect_bb_squeeze(widths) is False


def test_detect_bb_squeeze_false_before_full_lookback():
    assert detect_bb_squeeze([0.01] * 19) is False


def test_detect_bb_squeeze_false_when_not_meaningfully_tighter():
    widths = [0.05] * 19 + [0.048]  # a new low, but barely — not a real squeeze
    assert detect_bb_squeeze(widths) is False


def test_bb_squeeze_and_widening_intensity_exceed_one():
    assert bb_squeeze_intensity([0.05] * 19 + [0.02]) > 1.0
    assert bb_widening_intensity([0.02] * 19 + [0.05]) > 1.0


# --------------------------------------------------------------------- VWAP divergence / gap-fill (pure)

def test_detect_price_vwap_divergence_true_for_a_new_extreme_past_threshold():
    gaps = [0.001] * 9 + [0.004]
    assert detect_price_vwap_divergence(gaps) is True


def test_detect_price_vwap_divergence_false_below_threshold():
    assert detect_price_vwap_divergence([0.0005] * 10) is False


def test_detect_price_vwap_divergence_false_before_full_lookback():
    assert detect_price_vwap_divergence([0.01] * 9) is False


def test_detect_vwap_gap_fill_true_after_a_big_reversion():
    gaps = [0.001] * 8 + [0.006, 0.002]
    assert detect_vwap_gap_fill(gaps) is True


def test_detect_vwap_gap_fill_false_when_gap_never_exceeded_threshold():
    assert detect_vwap_gap_fill([0.001] * 10) is False


def test_detect_vwap_gap_fill_false_when_still_extended():
    gaps = [0.001] * 8 + [0.006, 0.005]  # barely shrunk, still mostly extended
    assert detect_vwap_gap_fill(gaps) is False


def test_vwap_divergence_and_gap_fill_intensity():
    assert vwap_divergence_intensity([0.001] * 9 + [0.006]) == pytest.approx(0.006 / 0.003)
    assert vwap_gap_fill_intensity([0.001] * 8 + [0.006, 0.002]) == pytest.approx(0.006 / 0.002)


# --------------------------------------------------------------------- engine persistence

def test_engine_persists_a_detected_pattern(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id

    engine = ActivityEngine(session_factory)
    doji = _candle(0, open=100.0, high=101.0, low=99.0, close=100.05)
    engine.on_candle_closed(SYMBOL, SEG, doji)

    # nothing hits the DB until flush() is called — see ActivityEngine's
    # own docstring for why (db calls scale with flushes, not detections)
    assert engine.buffered_count() == 1
    with session_factory() as session:
        assert session.query(InstrumentActivity).count() == 0

    flushed = engine.flush()
    assert flushed == 1
    assert engine.buffered_count() == 0

    with session_factory() as session:
        rows = session.query(InstrumentActivity).filter_by(instrument_id=instrument_id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.activity == "doji"
    assert row.activity_type == "candle_pattern"
    assert row.timeframe == "1min"
    assert float(row.intensity) == pytest.approx(40.0)  # range=2.0, body=0.05 -> 40x
    assert float(row.open_price) == 100.0
    assert float(row.high_price) == 101.0
    assert float(row.low_price) == 99.0
    assert float(row.close_price) == 100.05


def test_engine_stores_null_intensity_for_a_perfect_doji(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id

    engine = ActivityEngine(session_factory)
    perfect_doji = _candle(0, open=100.0, high=101.0, low=99.0, close=100.0)  # open == close
    engine.on_candle_closed(SYMBOL, SEG, perfect_doji)
    engine.flush()

    with session_factory() as session:
        row = session.query(InstrumentActivity).filter_by(instrument_id=instrument_id).one()
    assert row.intensity is None  # infinite ratio stored as NULL, not a sentinel


def test_engine_stores_null_intensity_for_a_multi_candle_pattern(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()
        instrument_id = session.query(SubscribedSymbol).filter_by(symbol=SYMBOL).one().id

    engine = ActivityEngine(session_factory)
    candles = [
        _candle(0, open=100.0, high=102.1, low=99.9, close=102.0),
        _candle(1, open=101.0, high=104.1, low=100.9, close=104.0),
        _candle(2, open=103.0, high=106.1, low=102.9, close=106.0),
    ]
    for c in candles:
        engine.on_candle_closed(SYMBOL, SEG, c)
    engine.flush()

    with session_factory() as session:
        row = session.query(InstrumentActivity).filter_by(
            instrument_id=instrument_id, activity="three_white_soldiers",
        ).one()
    assert row.intensity is None  # no intensity formula defined for this pattern yet
    assert float(row.close_price) == 106.0  # OHLC is the triggering (last) candle's own


def test_engine_is_idempotent_across_separate_flushes(session_factory):
    """A reconnect replay (or a next-day restart re-touching an overlapping
    candle) reprocesses the same candle in a later, separate flush cycle —
    must not duplicate the row, not just within one buffer/flush."""
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    doji = _candle(0, open=100.0, high=101.0, low=99.0, close=100.05)
    engine.on_candle_closed(SYMBOL, SEG, doji)
    engine.flush()
    engine.on_candle_closed(SYMBOL, SEG, doji)  # reprocessed in a later cycle
    engine.flush()

    with session_factory() as session:
        rows = session.query(InstrumentActivity).all()
    assert len(rows) == 1  # not duplicated


def test_engine_is_idempotent_within_one_flush(session_factory):
    """Same guarantee, but both detections land in the same buffer before
    any flush — the optimistic bulk-insert path must fall back correctly
    on an intra-batch duplicate, not just a cross-flush one."""
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    doji = _candle(0, open=100.0, high=101.0, low=99.0, close=100.05)
    engine.on_candle_closed(SYMBOL, SEG, doji)
    engine.on_candle_closed(SYMBOL, SEG, doji)  # same candle, buffered twice before any flush
    assert engine.buffered_count() == 2
    engine.flush()

    with session_factory() as session:
        rows = session.query(InstrumentActivity).all()
    assert len(rows) == 1  # the intra-batch duplicate was resolved, not both inserted


def test_engine_skips_silently_when_symbol_not_registered(session_factory):
    engine = ActivityEngine(session_factory)
    doji = _candle(0, open=100.0, high=101.0, low=99.0, close=100.05)
    engine.on_candle_closed(SYMBOL, SEG, doji)  # must not raise, no SubscribedSymbol row exists
    assert engine.buffered_count() == 0  # nothing to buffer — instrument couldn't be resolved

    engine.flush()
    with session_factory() as session:
        rows = session.query(InstrumentActivity).all()
    assert rows == []


def test_engine_detects_three_white_soldiers_across_calls(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    candles = [
        _candle(0, open=100.0, high=102.1, low=99.9, close=102.0),
        _candle(1, open=101.0, high=104.1, low=100.9, close=104.0),
        _candle(2, open=103.0, high=106.1, low=102.9, close=106.0),
    ]
    for c in candles:
        engine.on_candle_closed(SYMBOL, SEG, c)
    engine.flush()

    with session_factory() as session:
        activities = {row.activity for row in session.query(InstrumentActivity).all()}
    assert "three_white_soldiers" in activities


def test_engine_detects_bb_squeeze_through_on_candle_closed(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    # 19 volatile closes, then flat @100 for the rest — by minute 39 the
    # rolling 20-close window is entirely flat (width 0), a new low against
    # the 20-reading width lookback that just finished filling.
    for i in range(1, 20):
        engine.on_candle_closed(SYMBOL, SEG, _pc(i, 105.0 if i % 2 == 0 else 95.0))
    for i in range(20, 40):
        engine.on_candle_closed(SYMBOL, SEG, _pc(i, 100.0))
    engine.flush()

    with session_factory() as session:
        activities = {row.activity for row in session.query(InstrumentActivity).all()}
    assert "bb_squeeze" in activities


def test_engine_detects_bb_widening_through_on_candle_closed(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    # Mirror of the squeeze test: flat first, then volatile — the rolling
    # window ends up entirely volatile, a new high against the width lookback.
    for i in range(1, 20):
        engine.on_candle_closed(SYMBOL, SEG, _pc(i, 100.0))
    for i in range(20, 40):
        engine.on_candle_closed(SYMBOL, SEG, _pc(i, 105.0 if i % 2 == 0 else 95.0))
    engine.flush()

    with session_factory() as session:
        activities = {row.activity for row in session.query(InstrumentActivity).all()}
    assert "bb_widening" in activities


def test_engine_detects_vwap_divergence_and_gap_fill_through_on_candle_closed(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    # Flat, then a steady climb that pulls price further and further ahead
    # of the (slower-moving) cumulative vwap, then one candle back near
    # vwap's own level to close most of that gap back up.
    closes = [100.0, 100.0, 100.0, 100.0, 100.0, 103.0, 106.0, 110.0, 115.0, 121.0, 105.5]
    for i, close in enumerate(closes, start=1):
        engine.on_candle_closed(SYMBOL, SEG, _pc(i, close))
    engine.flush()

    with session_factory() as session:
        activities = {row.activity for row in session.query(InstrumentActivity).all()}
    assert "price_vwap_divergence" in activities
    assert "vwap_gap_fill" in activities


def test_to_dataframe_reflects_the_buffer(session_factory):
    pytest.importorskip("pandas")
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    doji = _candle(0, open=100.0, high=101.0, low=99.0, close=100.05)
    engine.on_candle_closed(SYMBOL, SEG, doji)

    df = engine.to_dataframe()
    assert len(df) == 1
    assert df.iloc[0]["activity"] == "doji"
    # dtypes are tuned, not left at pandas' defaults — see to_dataframe's docstring
    assert str(df["activity"].dtype) == "category"
    assert str(df["timeframe"].dtype) == "category"
    assert str(df["intensity"].dtype) == "float32"
    assert str(df["instrument_id"].dtype) == "int32"


def test_to_dataframe_empty_buffer_returns_empty_dataframe(session_factory):
    pytest.importorskip("pandas")
    engine = ActivityEngine(session_factory)
    df = engine.to_dataframe()
    assert len(df) == 0


# --------------------------------------------------------------------- pattern catalog

def test_seed_pattern_definitions_is_idempotent(session_factory):
    seed_pattern_definitions(session_factory)
    seed_pattern_definitions(session_factory)  # must not raise or duplicate

    with session_factory() as session:
        rows = session.query(PatternDefinition).all()
    codes = {row.code for row in rows}
    assert {"doji", "hammer", "shooting_star", "three_white_soldiers", "three_black_crows"} <= codes
