from datetime import datetime, timezone

import pytest

from activity_engine import (
    ActivityEngine,
    detect_bearish_engulfing,
    detect_bullish_engulfing,
    detect_dark_cloud_cover,
    detect_doji,
    detect_hammer,
    detect_piercing_line,
    detect_shooting_star,
    detect_three_black_crows,
    detect_three_white_soldiers,
    detect_tweezer_bottom,
    detect_tweezer_top,
    doji_intensity,
    engulfing_intensity,
    hammer_intensity,
    piercing_dark_cloud_intensity,
    seed_pattern_definitions,
    shooting_star_intensity,
    tweezer_bottom_intensity,
    tweezer_top_intensity,
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

    with session_factory() as session:
        row = session.query(InstrumentActivity).filter_by(
            instrument_id=instrument_id, activity="three_white_soldiers",
        ).one()
    assert row.intensity is None  # no intensity formula defined for this pattern yet
    assert float(row.close_price) == 106.0  # OHLC is the triggering (last) candle's own


def test_engine_is_idempotent_for_the_same_candle_reprocessed(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    doji = _candle(0, open=100.0, high=101.0, low=99.0, close=100.05)
    engine.on_candle_closed(SYMBOL, SEG, doji)
    engine.on_candle_closed(SYMBOL, SEG, doji)  # reconnect replay of the same candle

    with session_factory() as session:
        rows = session.query(InstrumentActivity).all()
    assert len(rows) == 1  # not duplicated


def test_engine_skips_silently_when_symbol_not_registered(session_factory):
    engine = ActivityEngine(session_factory)
    doji = _candle(0, open=100.0, high=101.0, low=99.0, close=100.05)
    engine.on_candle_closed(SYMBOL, SEG, doji)  # must not raise, no SubscribedSymbol row exists

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

    with session_factory() as session:
        activities = {row.activity for row in session.query(InstrumentActivity).all()}
    assert "three_white_soldiers" in activities


# --------------------------------------------------------------------- pattern catalog

def test_seed_pattern_definitions_is_idempotent(session_factory):
    seed_pattern_definitions(session_factory)
    seed_pattern_definitions(session_factory)  # must not raise or duplicate

    with session_factory() as session:
        rows = session.query(PatternDefinition).all()
    codes = {row.code for row in rows}
    assert {"doji", "hammer", "shooting_star", "three_white_soldiers", "three_black_crows"} <= codes
