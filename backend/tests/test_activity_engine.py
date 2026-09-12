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
    detect_evening_star,
    detect_hammer,
    detect_morning_star,
    detect_piercing_line,
    detect_double_bottom,
    detect_double_top,
    detect_price_vwap_divergence,
    detect_shooting_star,
    detect_swing_high,
    detect_swing_low,
    detect_three_black_crows,
    detect_three_white_soldiers,
    detect_tweezer_bottom,
    detect_tweezer_top,
    detect_vwap_gap_fill,
    doji_intensity,
    double_bottom_intensity,
    double_top_intensity,
    engulfing_intensity,
    evening_star_intensity,
    hammer_intensity,
    morning_star_intensity,
    piercing_dark_cloud_intensity,
    seed_pattern_definitions,
    shooting_star_intensity,
    swing_high_intensity,
    swing_low_intensity,
    SwingPoint,
    tweezer_bottom_intensity,
    tweezer_top_intensity,
    vwap_divergence_intensity,
    vwap_gap_fill_intensity,
)
from brokers.models import Candle
from db.models import InstrumentActivity, PatternDefinition, SubscribedSymbol
from indicators import (
    MacdState,
    RsiState,
    StochasticState,
    cross_intensity,
    crossed_above,
    crossed_below,
    update_macd,
    update_rsi,
    update_stochastic,
)

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


def test_detect_morning_star_true_for_a_gapped_reversal():
    candles = [
        _candle(0, open=110.0, high=110.5, low=99.5, close=100.0),
        _candle(1, open=98.0, high=99.0, low=97.5, close=98.5),  # star, gaps below a's close
        _candle(2, open=99.0, high=108.5, low=98.5, close=108.0),  # closes past a's midpoint (105)
    ]
    assert detect_morning_star(candles) is True
    assert detect_evening_star(candles) is False


def test_detect_morning_star_false_when_star_does_not_gap_down():
    candles = [
        _candle(0, open=110.0, high=110.5, low=99.5, close=100.0),
        _candle(1, open=101.0, high=102.0, low=100.5, close=102.0),  # overlaps a's close
        _candle(2, open=99.0, high=108.5, low=98.5, close=108.0),
    ]
    assert detect_morning_star(candles) is False


def test_detect_morning_star_false_when_third_candle_stops_short_of_midpoint():
    candles = [
        _candle(0, open=110.0, high=110.5, low=99.5, close=100.0),
        _candle(1, open=98.0, high=99.0, low=97.5, close=98.5),
        _candle(2, open=99.0, high=102.5, low=98.5, close=102.0),  # below midpoint (105)
    ]
    assert detect_morning_star(candles) is False


def test_detect_evening_star_true_for_a_gapped_reversal():
    candles = [
        _candle(0, open=100.0, high=110.5, low=99.5, close=110.0),
        _candle(1, open=111.5, high=112.5, low=111.0, close=112.0),  # star, gaps above a's close
        _candle(2, open=111.0, high=111.5, low=101.5, close=102.0),  # closes past a's midpoint (105)
    ]
    assert detect_evening_star(candles) is True
    assert detect_morning_star(candles) is False


def test_detect_evening_star_false_when_star_does_not_gap_up():
    candles = [
        _candle(0, open=100.0, high=110.5, low=99.5, close=110.0),
        _candle(1, open=109.0, high=110.0, low=108.5, close=108.0),  # overlaps a's close
        _candle(2, open=111.0, high=111.5, low=101.5, close=102.0),
    ]
    assert detect_evening_star(candles) is False


def test_morning_star_and_evening_star_intensity():
    a_body, half_body = 10.0, 5.0
    midpoint = 105.0
    # close exactly at a's own open (110) is a full reversal — intensity 1.0
    candles = [
        _candle(0, open=110.0, high=110.5, low=99.5, close=100.0),
        _candle(1, open=98.0, high=99.0, low=97.5, close=98.5),
        _candle(2, open=99.0, high=110.5, low=98.5, close=110.0),
    ]
    assert morning_star_intensity(candles) == pytest.approx((110.0 - midpoint) / half_body)

    candles_mirror = [
        _candle(0, open=100.0, high=110.5, low=99.5, close=110.0),
        _candle(1, open=111.5, high=112.5, low=111.0, close=112.0),
        _candle(2, open=111.0, high=111.5, low=99.5, close=100.0),
    ]
    assert evening_star_intensity(candles_mirror) == pytest.approx((midpoint - 100.0) / half_body)


# --------------------------------------------------------------------- swing high/low (pure)

def _swing_candles(highs=None, lows=None):
    # monotonic, non-flat defaults so overriding just one side (highs or
    # lows) for a given test never accidentally also satisfies the other
    # side's swing condition (a flat default would tie at every index)
    highs = highs or [95.0, 94.0, 93.0, 92.0, 91.0, 90.0, 89.0]
    lows = lows or [45.0, 44.0, 43.0, 42.0, 41.0, 40.0, 39.0]
    return [
        _candle(i, open=100.0, high=h, low=l, close=100.0)
        for i, (h, l) in enumerate(zip(highs, lows))
    ]


def test_detect_swing_high_true_for_a_peak_in_the_middle():
    candles = _swing_candles(highs=[100.0, 101.0, 102.0, 105.0, 102.0, 101.0, 100.0])
    assert detect_swing_high(candles) is True
    assert detect_swing_low(candles) is False


def test_detect_swing_high_false_when_middle_is_not_the_highest():
    candles = _swing_candles(highs=[100.0, 101.0, 102.0, 101.5, 103.0, 101.0, 100.0])
    assert detect_swing_high(candles) is False


def test_detect_swing_high_false_before_full_window():
    candles = _swing_candles(highs=[100.0, 101.0, 102.0, 105.0, 102.0, 101.0])[:6]
    assert detect_swing_high(candles) is False


def test_detect_swing_low_true_for_a_trough_in_the_middle():
    candles = _swing_candles(lows=[100.0, 99.0, 98.0, 95.0, 98.0, 99.0, 100.0])
    assert detect_swing_low(candles) is True
    assert detect_swing_high(candles) is False


def test_detect_swing_low_false_when_middle_is_not_the_lowest():
    candles = _swing_candles(lows=[100.0, 99.0, 98.0, 98.5, 97.0, 99.0, 100.0])
    assert detect_swing_low(candles) is False


def test_swing_high_intensity_grows_with_a_sharper_peak():
    mild = _swing_candles(highs=[100.0, 101.0, 102.0, 103.0, 102.0, 101.0, 100.0])
    sharp = _swing_candles(highs=[100.0, 101.0, 102.0, 110.0, 102.0, 101.0, 100.0])
    assert swing_high_intensity(mild) > 0
    assert swing_high_intensity(sharp) > swing_high_intensity(mild)


def test_swing_low_intensity_grows_with_a_sharper_trough():
    mild = _swing_candles(lows=[100.0, 99.0, 98.0, 97.0, 98.0, 99.0, 100.0])
    sharp = _swing_candles(lows=[100.0, 99.0, 98.0, 90.0, 98.0, 99.0, 100.0])
    assert swing_low_intensity(mild) > 0
    assert swing_low_intensity(sharp) > swing_low_intensity(mild)


# --------------------------------------------------------------------- double top/bottom (pure)

def _sp(kind, price):
    return SwingPoint(kind=kind, price=price, candle=_candle(0, open=price, high=price, low=price, close=price))


def test_detect_double_top_true_for_comparable_peaks_with_a_deep_valley():
    points = [_sp("high", 100.0), _sp("low", 98.0), _sp("high", 100.3)]
    assert detect_double_top(points) is True
    assert detect_double_bottom(points) is False


def test_detect_double_top_false_when_peaks_dont_match():
    points = [_sp("high", 100.0), _sp("low", 98.0), _sp("high", 103.0)]
    assert detect_double_top(points) is False


def test_detect_double_top_false_when_valley_is_too_shallow():
    points = [_sp("high", 100.0), _sp("low", 99.9), _sp("high", 100.05)]
    assert detect_double_top(points) is False


def test_detect_double_top_false_when_sequence_is_wrong():
    points = [_sp("high", 100.0), _sp("high", 100.3), _sp("low", 98.0)]
    assert detect_double_top(points) is False


def test_detect_double_top_false_before_three_points():
    assert detect_double_top([_sp("high", 100.0), _sp("low", 98.0)]) is False


def test_detect_double_bottom_true_for_comparable_troughs_with_a_tall_peak():
    points = [_sp("low", 100.0), _sp("high", 102.0), _sp("low", 99.7)]
    assert detect_double_bottom(points) is True
    assert detect_double_top(points) is False


def test_double_top_and_bottom_intensity_exceed_one():
    top_points = [_sp("high", 100.0), _sp("low", 95.0), _sp("high", 100.3)]
    bottom_points = [_sp("low", 100.0), _sp("high", 105.0), _sp("low", 99.7)]
    assert double_top_intensity(top_points) > 1.0
    assert double_bottom_intensity(bottom_points) > 1.0


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


# 20 widths, 19 consecutive steps. TREND_RATIO=0.7 needs >= 13.3, i.e. >= 14
# agreeing steps to qualify. _qualifying/_short below give exactly 14 and 13.
def _narrowing_widths(agreeing_steps):
    values = [1.0 - 0.01 * i for i in range(agreeing_steps + 1)]
    values += [values[-1]] * (20 - len(values))
    return values


def _widening_widths(agreeing_steps):
    return [1.0 - v for v in _narrowing_widths(agreeing_steps)]


def test_detect_bb_squeeze_true_when_enough_steps_narrow():
    widths = _narrowing_widths(14)
    assert detect_bb_squeeze(widths) is True
    assert detect_bb_widening(widths) is False


def test_detect_bb_squeeze_false_when_not_enough_steps_narrow():
    widths = _narrowing_widths(13)
    assert detect_bb_squeeze(widths) is False
    assert detect_bb_widening(widths) is False


def test_detect_bb_widening_true_when_enough_steps_widen():
    widths = _widening_widths(14)
    assert detect_bb_widening(widths) is True
    assert detect_bb_squeeze(widths) is False


def test_detect_bb_widening_false_when_not_enough_steps_widen():
    widths = _widening_widths(13)
    assert detect_bb_widening(widths) is False


def test_detect_bb_squeeze_false_before_full_lookback():
    assert detect_bb_squeeze(_narrowing_widths(14)[:19]) is False


def test_detect_bb_squeeze_and_widening_false_when_flat():
    assert detect_bb_squeeze([0.05] * 20) is False
    assert detect_bb_widening([0.05] * 20) is False


def test_bb_squeeze_and_widening_intensity_exceed_one():
    assert bb_squeeze_intensity(_narrowing_widths(19)) > 1.0
    assert bb_widening_intensity(_widening_widths(19)) > 1.0


def test_bb_squeeze_intensity_near_the_boundary_is_close_to_one():
    assert bb_squeeze_intensity(_narrowing_widths(14)) == pytest.approx((14 / 19) / 0.7)


# --------------------------------------------------------------------- VWAP divergence / gap-fill (pure)

def _widening_gaps(agreeing_steps):
    return [0.001 * v for v in _widening_widths(agreeing_steps)]


def _narrowing_gaps(agreeing_steps):
    return [0.001 * v for v in _narrowing_widths(agreeing_steps)]


def test_detect_price_vwap_divergence_true_when_enough_steps_widen():
    gaps = _widening_gaps(14)
    assert detect_price_vwap_divergence(gaps) is True
    assert detect_vwap_gap_fill(gaps) is False


def test_detect_price_vwap_divergence_false_when_not_enough_steps_widen():
    assert detect_price_vwap_divergence(_widening_gaps(13)) is False


def test_detect_price_vwap_divergence_false_before_full_lookback():
    assert detect_price_vwap_divergence(_widening_gaps(14)[:19]) is False


def test_detect_vwap_gap_fill_true_when_enough_steps_narrow():
    gaps = _narrowing_gaps(14)
    assert detect_vwap_gap_fill(gaps) is True
    assert detect_price_vwap_divergence(gaps) is False


def test_detect_vwap_gap_fill_false_when_not_enough_steps_narrow():
    assert detect_vwap_gap_fill(_narrowing_gaps(13)) is False


def test_vwap_divergence_and_gap_fill_intensity():
    assert vwap_divergence_intensity(_widening_gaps(19)) > 1.0
    assert vwap_gap_fill_intensity(_narrowing_gaps(19)) > 1.0


# --------------------------------------------------------------------- RSI / MACD (pure)

def test_update_rsi_none_before_seed_window_fills():
    state = RsiState()
    rsi = None
    for close in [100.0] * 14:  # 14 closes = only 13 changes, needs 14
        rsi = update_rsi(state, close)
    assert rsi is None


def test_update_rsi_100_for_a_monotonic_rise():
    state = RsiState()
    rsi = None
    for close in [100.0 + i for i in range(20)]:
        rsi = update_rsi(state, close)
    assert rsi == pytest.approx(100.0)


def test_update_rsi_0_for_a_monotonic_fall():
    state = RsiState()
    rsi = None
    for close in [100.0 - i for i in range(20)]:
        rsi = update_rsi(state, close)
    assert rsi == pytest.approx(0.0)


def test_update_macd_none_before_slow_ema_matures():
    state = MacdState()
    line = signal = None
    for close in [100.0 + i for i in range(25)]:  # < MACD_SLOW (26)
        line, signal = update_macd(state, close)
    assert line is None
    assert signal is None


def test_update_macd_line_available_before_signal():
    state = MacdState()
    line = signal = None
    for close in [100.0 + i for i in range(30)]:  # >= 26, < 26+9
        line, signal = update_macd(state, close)
    assert line is not None
    assert signal is None


def test_update_macd_positive_for_a_sustained_uptrend():
    state = MacdState()
    line = signal = None
    for close in [100.0 + 0.5 * i for i in range(40)]:
        line, signal = update_macd(state, close)
    assert line > 0
    assert signal > 0


def test_update_stochastic_none_before_fastk_window_fills():
    state = StochasticState()
    k = d = None
    for i in range(4):  # < STOCH_FASTK_PERIOD (5)
        k, d = update_stochastic(state, 100.0 + i + 1, 100.0 + i - 1, 100.0 + i)
    assert k is None
    assert d is None


def test_update_stochastic_k_before_d_for_a_steady_rise():
    state = StochasticState()
    k = d = None
    for i in range(8):  # k available at 7, d needs 9
        k, d = update_stochastic(state, 100.0 + i + 1, 100.0 + i - 1, 100.0 + i)
    assert k is not None
    assert d is None


def test_update_stochastic_both_available_and_matches_known_value():
    state = StochasticState()
    k = d = None
    for i in range(9):
        k, d = update_stochastic(state, 100.0 + i + 1, 100.0 + i - 1, 100.0 + i)
    # verified by direct simulation for this exact steady +1/candle rise
    assert k == pytest.approx(83.33333, rel=1e-4)
    assert d == pytest.approx(83.33333, rel=1e-4)


def test_crossed_above_true_on_a_genuine_cross():
    assert crossed_above(prev_a=10, prev_b=12, curr_a=13, curr_b=12) is True


def test_crossed_above_false_when_already_above():
    assert crossed_above(prev_a=13, prev_b=12, curr_a=14, curr_b=12) is False


def test_crossed_above_false_with_missing_data():
    assert crossed_above(None, 12, 13, 12) is False


def test_crossed_below_true_on_a_genuine_cross():
    assert crossed_below(prev_a=14, prev_b=12, curr_a=11, curr_b=12) is True


def test_crossed_below_false_when_already_below():
    assert crossed_below(prev_a=11, prev_b=12, curr_a=10, curr_b=12) is False


def test_cross_intensity_is_the_step_change_in_spread():
    # spread goes from -2 (10-12) to +1 (13-12) — a step change of 3
    assert cross_intensity(prev_a=10, prev_b=12, curr_a=13, curr_b=12) == pytest.approx(3.0)


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


def test_engine_detects_morning_star_across_calls(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    candles = [
        _candle(0, open=110.0, high=110.5, low=99.5, close=100.0),
        _candle(1, open=98.0, high=99.0, low=97.5, close=98.5),
        _candle(2, open=99.0, high=108.5, low=98.5, close=108.0),
    ]
    for c in candles:
        engine.on_candle_closed(SYMBOL, SEG, c)
    engine.flush()

    with session_factory() as session:
        activities = {row.activity for row in session.query(InstrumentActivity).all()}
    assert "morning_star" in activities


def test_engine_detects_swing_high_with_the_confirmed_candles_own_timestamp(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    highs = [100.0, 101.0, 102.0, 105.0, 102.0, 101.0, 100.0]
    candles = [_candle(i, open=100.0, high=h, low=99.0, close=100.0) for i, h in enumerate(highs)]
    for c in candles:
        engine.on_candle_closed(SYMBOL, SEG, c)
    engine.flush()

    with session_factory() as session:
        rows = session.query(InstrumentActivity).filter_by(activity="swing_high").all()
    assert len(rows) == 1
    # confirmed 3 candles after it happened (SWING_LOOKBACK=3) — the stored
    # ts/OHLC must be the peak candle's own, not the latest candle fed in
    # (DateTime columns come back naive, so compare against a naive value)
    assert rows[0].ts == candles[3].timestamp.replace(tzinfo=None)
    assert float(rows[0].high_price) == 105.0


def _zigzag_highs(checkpoints):
    """checkpoints: [(index, high), ...] — linearly interpolates highs
    between them. Verified by direct simulation (not just hoped to work)
    to actually produce two comparable swing highs with a deep valley
    between them under the real swing/double-top detectors."""
    highs = []
    for (i0, h0), (i1, h1) in zip(checkpoints, checkpoints[1:]):
        for j in range(i0, i1):
            highs.append(h0 + (h1 - h0) * (j - i0) / (i1 - i0))
    highs.append(checkpoints[-1][1])
    return highs


def test_engine_detects_double_top_through_on_candle_closed(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    # rise to a first peak (100.3), fall to a valley (95.0), rise to a
    # second comparable peak (100.2), fall off — two swing highs within
    # 0.5% of each other with a >0.3%-deep valley between them
    highs = _zigzag_highs([(0, 90.0), (6, 100.3), (12, 95.0), (18, 100.2), (24, 92.0)])
    candles = [_candle(i, open=h - 0.5, high=h, low=h - 1.0, close=h - 0.5) for i, h in enumerate(highs)]
    for c in candles:
        engine.on_candle_closed(SYMBOL, SEG, c)
    engine.flush()

    with session_factory() as session:
        activities = {row.activity for row in session.query(InstrumentActivity).all()}
    assert "swing_high" in activities
    assert "swing_low" in activities
    assert "double_top" in activities


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
    # A steady climb pulls price further and further ahead of the
    # (slower-moving) cumulative vwap — widening — then a gentler decline
    # (staying above vwap throughout, no overshoot to the other side) closes
    # most of that gap back up — narrowing.
    closes = [100.0 + 2 * i for i in range(1, 26)]
    closes += [150.0 - 0.8 * i for i in range(1, 26)]
    for i, close in enumerate(closes, start=1):
        engine.on_candle_closed(SYMBOL, SEG, _pc(i, close))
    engine.flush()

    with session_factory() as session:
        activities = {row.activity for row in session.query(InstrumentActivity).all()}
    assert "price_vwap_divergence" in activities
    assert "vwap_gap_fill" in activities


def test_engine_detects_rsi_macd_stoch_ma_crossovers_through_on_candle_closed(session_factory):
    with session_factory() as session:
        session.add(SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="2885", previous_close=100.0,
        ))
        session.commit()

    engine = ActivityEngine(session_factory)
    # A long, gently-declining zigzag (60 candles — enough to mature RSI's
    # seed, MACD's slow EMA + signal, and both MAs all in a clear downtrend
    # state) followed by a sustained rise, verified by direct simulation to
    # actually produce all three crossovers rather than the trend simply
    # starting past the crossing point unobserved.
    seed = []
    base = 100.0
    for i in range(60):
        base += -0.6 if i % 2 == 0 else 0.4
        seed.append(round(base, 4))
    rise = [round(seed[-1] + 0.5 * i, 4) for i in range(1, 40)]
    closes = seed + rise

    for i, close in enumerate(closes, start=1):
        engine.on_candle_closed(SYMBOL, SEG, _pc(i, close))
    engine.flush()

    with session_factory() as session:
        activities = {row.activity for row in session.query(InstrumentActivity).all()}
    assert "rsi_cross_above_60" in activities
    assert "macd_bullish_cross" in activities
    assert "ma_golden_cross" in activities
    assert "stoch_bullish_cross" in activities


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
