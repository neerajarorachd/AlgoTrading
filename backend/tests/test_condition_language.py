"""ALGO-30: the condition language's new keywords -- derived fields, crossovers,
events, time words and/or/not. Each is checked on a hand-made frame where the
right answer is obvious, and every windowed one across a day boundary (nothing
may reach into the previous session)."""
import math

import pandas as pd
import pytest

from condition_evaluator import (
    EVENTS_COLUMN, TRADING_DATE_COLUMN, FormulaError, describe_grammar, parse_and_evaluate,
    required_lookback, validate_condition,
)


def frame(**cols):
    n = len(next(iter(cols.values())))
    data = {"open": [10.0] * n, "high": [11.0] * n, "low": [9.0] * n, "close": [10.0] * n, "volume": [100.0] * n}
    data.update(cols)
    data.setdefault(TRADING_DATE_COLUMN, [0] * n)
    return pd.DataFrame(data)


def as_list(series):
    return [None if (isinstance(v, float) and math.isnan(v)) else (bool(v) if isinstance(v, (bool,)) or str(series.dtype) == "bool" else v)
            for v in series.tolist()]


# ---- derived fields ----

def test_body_wicks_and_range():
    df = frame(open=[10.0], high=[13.0], low=[8.0], close=[12.0])
    assert parse_and_evaluate("BODY", df).iloc[0] == 2.0
    assert parse_and_evaluate("UPPER_WICK", df).iloc[0] == 1.0
    assert parse_and_evaluate("LOWER_WICK", df).iloc[0] == 2.0
    assert parse_and_evaluate("RANGE", df).iloc[0] == 5.0


def test_macd_hist_and_bb_width():
    df = frame(macd_line=[1.5], macd_signal=[1.0], bb_upper=[102.0], bb_middle=[100.0], bb_lower=[98.0])
    assert parse_and_evaluate("MACD_HIST", df).iloc[0] == 0.5
    assert parse_and_evaluate("BB_WIDTH", df).iloc[0] == pytest.approx(4.0)
    assert parse_and_evaluate("BB_WIDTH[-0] > 3", df).iloc[0]


def test_hammer_strength_reads_naturally():
    # long lower wick (3x body), meaningful range, above-average volume
    df = frame(open=[10.0, 10.0], high=[10.6, 10.6], low=[9.0, 9.0], close=[10.5, 10.5], volume=[100.0, 300.0])
    assert as_list(parse_and_evaluate("LOWER_WICK >= 2 * BODY and VOLUME > mean(VOLUME, 2)", df)) == [False, True]


# ---- crossovers ----

def test_crossed_up_fires_only_on_the_crossing_candle():
    df = frame(macd_line=[1.0, 2.0, 3.0, 2.0], macd_signal=[2.0, 2.0, 2.0, 2.5])
    assert as_list(parse_and_evaluate("CROSSED_UP(MACD_LINE, MACD_SIGNAL)", df)) == [False, False, True, False]
    assert as_list(parse_and_evaluate("CROSSED_DOWN(MACD_LINE, MACD_SIGNAL)", df)) == [False, False, False, True]


def test_crossed_up_against_a_number():
    df = frame(rsi=[40.0, 44.0, 46.0, 50.0])
    assert as_list(parse_and_evaluate("CROSSED_UP(RSI, 45)", df)) == [False, False, True, False]


def test_crossing_does_not_reach_into_yesterday():
    # day 0 ends above, day 1 opens above: no "cross" at day 1's first candle
    df = frame(rsi=[40.0, 50.0, 50.0], **{TRADING_DATE_COLUMN: [0, 0, 1]})
    assert as_list(parse_and_evaluate("CROSSED_UP(RSI, 45)", df)) == [False, True, False]


# ---- events ----

def test_event_reads_the_candles_events():
    df = frame(close=[1.0, 2.0, 3.0], **{EVENTS_COLUMN: [frozenset(), frozenset({"hammer", "doji"}), frozenset({"doji"})]})
    assert as_list(parse_and_evaluate("EVENT(hammer)", df)) == [False, True, False]


def test_event_without_loaded_events_says_so():
    with pytest.raises(FormulaError, match="needs the candles' events"):
        parse_and_evaluate("EVENT(hammer)", frame(close=[1.0]))


# ---- time words ----

def test_recently_keeps_a_cross_alive_for_n_candles():
    df = frame(rsi=[40.0, 50.0, 50.0, 50.0, 50.0])  # crosses 45 on candle 1
    assert as_list(parse_and_evaluate("RECENTLY(CROSSED_UP(RSI, 45), 3)", df)) == [False, True, True, True, False]


def test_recently_default_n_comes_from_the_pattern_lookback():
    df = frame(rsi=[40.0, 50.0, 50.0, 50.0, 50.0])
    got = parse_and_evaluate("RECENTLY(CROSSED_UP(RSI, 45))", df, {"lookback": 2})
    assert as_list(got) == [False, True, True, False, False]


def test_recently_stops_at_the_day_boundary():
    df = frame(rsi=[40.0, 50.0, 50.0], **{TRADING_DATE_COLUMN: [0, 0, 1]})
    assert as_list(parse_and_evaluate("RECENTLY(CROSSED_UP(RSI, 45), 10)", df)) == [False, True, False]


def test_for_needs_every_candle_in_the_window():
    df = frame(bb_upper=[102, 103, 104, 105, 104.5], bb_middle=[100] * 5, bb_lower=[98, 97, 96, 95, 95.5])
    got = parse_and_evaluate("FOR(BB_WIDTH > BB_WIDTH[-1], 3)", df)
    assert as_list(got) == [False, False, False, True, False]  # expanding on candles 1-3, then narrows


def test_count_counts_true_candles():
    df = frame(open=[10.0] * 5, close=[11.0, 9.0, 11.0, 11.0, 11.0])  # green, red, green, green, green
    assert parse_and_evaluate("COUNT(CLOSE > OPEN, 5)", df).tolist() == [1.0, 1.0, 2.0, 3.0, 4.0]
    assert as_list(parse_and_evaluate("COUNT(CLOSE > OPEN, 5) >= 4", df)) == [False, False, False, False, True]


def test_bars_since():
    ev = [frozenset(), frozenset({"hammer"}), frozenset(), frozenset()]
    df = frame(close=[1.0] * 4, **{EVENTS_COLUMN: ev})
    got = parse_and_evaluate("BARS_SINCE(EVENT(hammer))", df).tolist()
    assert math.isnan(got[0]) and got[1:] == [0.0, 1.0, 2.0]
    assert as_list(parse_and_evaluate("BARS_SINCE(EVENT(hammer)) <= 1", df)) == [False, True, True, False]


def test_then_needs_a_before_b_within_n():
    ev = [frozenset({"hammer"}), frozenset(), frozenset(), frozenset(), frozenset()]
    df = frame(stoch_k=[10.0, 10.0, 30.0, 30.0, 30.0], stoch_d=[20.0] * 5, **{EVENTS_COLUMN: ev})
    # hammer on 0, stoch crosses up on 2 -> within 3: yes
    assert as_list(parse_and_evaluate("THEN(EVENT(hammer), CROSSED_UP(STOCH_K, STOCH_D), 3)", df)) == [False, False, True, False, False]
    # within 1: no
    assert not parse_and_evaluate("THEN(EVENT(hammer), CROSSED_UP(STOCH_K, STOCH_D), 1)", df).any()


def test_highest_breakout():
    df = frame(high=[10.0, 12.0, 11.0, 11.5], close=[10.0, 11.0, 11.0, 12.5])
    assert as_list(parse_and_evaluate("CLOSE > HIGHEST(HIGH[-1], 2)", df)) == [False, False, False, True]


# ---- and / or / not ----

def test_and_or_not_inside_a_formula():
    df = frame(rsi=[40.0, 50.0, 60.0], volume=[100.0, 50.0, 200.0])
    assert as_list(parse_and_evaluate("RSI > 45 and VOLUME > 100", df)) == [False, False, True]
    assert as_list(parse_and_evaluate("RSI > 55 OR VOLUME < 60", df)) == [False, True, True]
    assert as_list(parse_and_evaluate("NOT RSI > 45", df)) == [True, False, False]


def test_lowercase_for_parses():
    df = frame(rsi=[50.0, 50.0, 50.0])
    assert as_list(parse_and_evaluate("for(RSI > 45, 2)", df)) == [False, True, True]


def test_time_words_reject_a_number():
    with pytest.raises(FormulaError, match="needs a condition"):
        parse_and_evaluate("RECENTLY(RSI, 3)", frame(rsi=[50.0]))


# ---- the reckoner's worked example, end to end ----

def test_bullish_momentum_example():
    df = frame(
        rsi=[40, 44, 46, 48, 50],
        macd_line=[0.0, 0.5, 1.2, 1.6, 2.2], macd_signal=[0.4, 0.6, 0.8, 1.0, 1.2],
        bb_upper=[101, 101.5, 102, 102.6, 103.4], bb_middle=[100] * 5, bb_lower=[99, 98.5, 98, 97.4, 96.6],
    )
    conditions = [
        "RSI > 45", "RSI > RSI[-1]", "RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL))",
        "MACD_HIST > MACD_HIST[-1]", "BB_WIDTH > BB_WIDTH[-1]", "BB_WIDTH > 1",
    ]
    met = pd.concat([parse_and_evaluate(c, df) for c in conditions], axis=1).all(axis=1)
    assert as_list(met) == [False, False, True, True, True]


# ---- validation, lookback, grammar ----

def test_validate_accepts_the_new_words_and_rejects_bad_ones():
    assert validate_condition("RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL), 5)", None, None) is None
    assert validate_condition("EVENT(hammer) and VOLUME > mean(VOLUME, 20)", None, None) is None
    assert "unknown function" in validate_condition("WHENEVER(RSI > 45)", None, None)
    assert "takes" in validate_condition("RECENTLY(RSI > 45, 3, 4)", None, None)


def test_required_lookback_counts_the_wrappers():
    assert required_lookback("RSI > 45") == 1
    assert required_lookback("CROSSED_UP(MACD_LINE, MACD_SIGNAL)") == 2
    assert required_lookback("RECENTLY(CROSSED_UP(MACD_LINE, MACD_SIGNAL), 10)") == 11
    assert required_lookback("RECENTLY(RSI > 45)") == 10
    assert required_lookback("FOR(BB_WIDTH > BB_WIDTH[-1], 4)") == 5
    # EVENT(hammer) checks strength, whose volume_ratio reads mean(VOLUME, 20): 5 + 20
    assert required_lookback("THEN(EVENT(hammer), RSI > 45, 5)") == 25
    assert required_lookback("THEN(EVENT_ANY(rsi_cross_above_60), RSI > 45, 5)") == 6


def test_grammar_lists_new_functions_and_derived_fields():
    g = describe_grammar()
    names = {f["name"] for f in g["functions"]}
    assert {"recently", "for", "count", "bars_since", "then", "crossed_up", "event", "highest"} <= names
    assert {"bb_width", "body", "lower_wick", "macd_hist"} <= {f["value"] for f in g["derived_fields"]}
    assert "ema21" in {f["value"] for f in g["fields"]}


def test_macd_departure_pattern():
    """User's pattern (2026-10-14): MACD crossed up recently, lines moving apart
    for 3 candles, BB gap over 1%."""
    from patterns_catalog import SYSTEM_PATTERNS
    conditions = next(p[8] for p in SYSTEM_PATTERNS if p[0] == "macd_departure_bull")
    df = frame(
        # cross up on candle 1; gap widens on 2, 3, 4; shrinks on 5
        macd_line=[0.0, 0.6, 0.9, 1.3, 1.8, 1.9], macd_signal=[0.5, 0.5, 0.6, 0.7, 0.8, 1.2],
        bb_upper=[101.2] * 6, bb_middle=[100.0] * 6, bb_lower=[98.8] * 6,  # 2.4% wide
    )
    met = pd.concat([parse_and_evaluate(c, df) for c in conditions], axis=1).all(axis=1)
    assert as_list(met) == [False, False, False, True, True, False]
    narrow = df.assign(bb_upper=[100.4] * 6, bb_lower=[99.6] * 6)  # 0.8% wide: no
    assert not pd.concat([parse_and_evaluate(c, narrow) for c in conditions], axis=1).all(axis=1).any()
