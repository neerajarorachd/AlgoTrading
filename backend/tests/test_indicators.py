"""indicators.py's pure per-candle state machines are otherwise only
exercised indirectly through test_activity_engine.py's RSI/MACD/ATR
coverage -- update_ema (added 2026-10-06 for the live chart's EMA overlay
lines, see memory: live_indicators_phase1_priority) gets its own direct
test since there's no existing home for it."""
from __future__ import annotations

import pytest

from indicators import EmaState, update_ema


def test_update_ema_withholds_a_value_until_the_period_has_elapsed():
    state = EmaState()
    closes = [100.0, 101.0, 99.0, 102.0]
    results = [update_ema(state, c, period=5) for c in closes]
    assert results == [None, None, None, None]

    results.append(update_ema(state, 103.0, period=5))
    assert results[-1] is not None


def test_update_ema_matches_the_standard_formula():
    closes = [100.0, 101.0, 99.5, 102.0, 103.0, 101.5, 104.0]
    period = 5
    state = EmaState()
    last = None
    for c in closes:
        last = update_ema(state, c, period=period)

    # Independent computation: seeded at the first close, then the
    # standard k = 2/(period+1) recursive update for every close after.
    k = 2 / (period + 1)
    expected = closes[0]
    for c in closes[1:]:
        expected = c * k + expected * (1 - k)
    assert last == pytest.approx(expected)


def test_update_ema_keeps_independent_state_per_period():
    # Mirrors how activity_engine.py uses this -- one EmaState per period,
    # fed the same close sequence, must never share state with each other.
    closes = [10.0, 10.5, 11.0, 10.8, 11.2, 11.5]
    state_short = EmaState()
    state_long = EmaState()
    short_last = long_last = None
    for c in closes:
        short_last = update_ema(state_short, c, period=3)
        long_last = update_ema(state_long, c, period=7)

    assert short_last is not None
    assert long_last is None  # period=7 needs 7 closes; only 6 fed so far

    long_last = update_ema(state_long, 11.9, period=7)
    assert long_last is not None
    assert short_last != long_last
