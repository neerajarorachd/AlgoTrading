from datetime import timedelta

import pytest

from timeframes import candle_duration


def test_candle_duration_known_timeframes():
    assert candle_duration("1min") == timedelta(minutes=1)
    assert candle_duration("3min") == timedelta(minutes=3)
    assert candle_duration("5min") == timedelta(minutes=5)
    assert candle_duration("1day") == timedelta(days=1)


def test_candle_duration_unknown_timeframe_raises():
    with pytest.raises(KeyError):
        candle_duration("2min")
