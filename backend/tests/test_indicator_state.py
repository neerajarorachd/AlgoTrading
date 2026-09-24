from indicators import classify_macd, classify_rsi, classify_series_trend, classify_stochastic


def test_classify_rsi_bands():
    assert classify_rsi(None) is None
    assert classify_rsi(30.0) == "oversold"
    assert classify_rsi(29.9) == "oversold"
    assert classify_rsi(70.0) == "overbought"
    assert classify_rsi(70.1) == "overbought"
    assert classify_rsi(50.0) == "neutral"


def test_classify_macd_bullish_bearish():
    assert classify_macd(None, 1.0) is None
    assert classify_macd(1.0, None) is None
    assert classify_macd(1.0, 1.0) is None  # exactly equal — no label
    assert classify_macd(1.5, 1.0) == "bullish"
    assert classify_macd(0.5, 1.0) == "bearish"


def test_classify_stochastic_bands():
    assert classify_stochastic(None) is None
    assert classify_stochastic(20.0) == "oversold"
    assert classify_stochastic(19.9) == "oversold"
    assert classify_stochastic(80.0) == "overbought"
    assert classify_stochastic(80.1) == "overbought"
    assert classify_stochastic(50.0) == "neutral"


def test_classify_series_trend_needs_at_least_two_values():
    assert classify_series_trend([]) is None
    assert classify_series_trend([5.0]) is None


def test_classify_series_trend_increasing():
    assert classify_series_trend([10.0, 20.0, 30.0, 40.0]) == "increasing"


def test_classify_series_trend_decreasing():
    assert classify_series_trend([40.0, 30.0, 20.0, 10.0]) == "decreasing"


def test_classify_series_trend_flat_when_no_strong_majority():
    assert classify_series_trend([10.0, 20.0, 15.0, 25.0]) == "flat"


def test_classify_series_trend_uses_signed_values_not_magnitude():
    """A MACD-line-style series crossing zero (-0.5 -> 0.3) is genuinely
    RISING — classify_series_trend must not treat it like classify_trend's
    own abs()-based magnitude comparison would (which would see 0.5 -> 0.3
    as decreasing)."""
    assert classify_series_trend([-0.5, -0.1, 0.3]) == "increasing"
