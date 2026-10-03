import pandas as pd
import pytest

from condition_evaluator import (
    ALIASES, TRADING_DATE_COLUMN, Field, FormulaError, evaluate_condition, evaluate_tree, parse_and_evaluate,
)


@pytest.fixture
def df():
    return pd.DataFrame({
        "open": [99.5, 100.5, 98.5, 101.5, 97.5],
        "high": [101.0, 102.0, 100.0, 103.0, 99.0],
        "low": [99.0, 100.0, 98.0, 101.0, 97.0],
        "close": [100.0, 101.0, 99.0, 102.0, 98.0],
        "vwap": [100.5, 100.6, 100.7, 100.8, 100.9],
        "rsi": [50.0, 55.0, 40.0, 60.0, 35.0],
        "stoch_k": [10.0, 20.0, 30.0, 40.0, 50.0],
    })


def test_field_enum_values_are_the_dataframe_column_names(df):
    for field in Field:
        assert field.value in df.columns or field.value in ("macd_line", "macd_signal", "stoch_d",
                                                              "ma21", "ma50", "atr", "bb_upper",
                                                              "bb_middle", "bb_lower", "volume")


def test_bare_field_reads_the_current_candle(df):
    assert list(parse_and_evaluate("close", df)) == list(df["close"])


def test_arithmetic_between_two_fields(df):
    result = parse_and_evaluate("vwap - close", df)
    expected = [v - c for v, c in zip(df["vwap"], df["close"])]
    assert list(result) == pytest.approx(expected)


def test_candle_offset_shifts_one_back(df):
    result = parse_and_evaluate("close[-1]", df)
    assert pd.isna(result.iloc[0])
    assert list(result.iloc[1:]) == list(df["close"].iloc[:-1])


def test_candle_offset_two_back(df):
    result = parse_and_evaluate("close[-2]", df)
    assert pd.isna(result.iloc[0]) and pd.isna(result.iloc[1])
    assert list(result.iloc[2:]) == list(df["close"].iloc[:-2])


def test_explicit_offset_zero_is_the_current_candle(df):
    assert list(parse_and_evaluate("close[0]", df)) == list(df["close"])


def test_positive_offset_is_rejected(df):
    with pytest.raises(FormulaError, match="0 or negative"):
        parse_and_evaluate("close[1]", df)


def test_compound_formula_matches_the_chartink_example(df):
    # "[last candle] - [high] < VWAP" from the Chartink-style spec
    result = evaluate_condition("close[-1] - high[-1]", "<", "vwap", df)
    expected = [(df["close"][i - 1] - df["high"][i - 1]) < df["vwap"][i] if i > 0 else None
                for i in range(len(df))]
    assert bool(result.iloc[1]) == expected[1]
    assert bool(result.iloc[2]) == expected[2]


def test_comparison_operator_cannot_bind_tighter_than_arithmetic_on_either_side(df):
    # Builder concern, 2026-10-03: in a formula like
    # "slope(vwap, 3) > open[-1] - open[-2]", could ">" end up binding
    # before "-" and silently evaluate as "(slope(...) > open[-1]) -
    # open[-2]"? No -- left_formula and right_formula are each parsed and
    # evaluated as PURE arithmetic (no comparison token ever appears in
    # either string being ast.parse'd), and operator_symbol is applied
    # only AFTERWARDS, outside any parsing (see evaluate_condition). So
    # there is no expression in which ">" could ever bind before "-" in
    # the first place -- this is safe by construction, not by precedence
    # rules, and this test locks that in.
    result = evaluate_condition("vwap", ">", "open[-1] - open[-2]", df)
    expected = [df["vwap"][i] > (df["open"][i - 1] - df["open"][i - 2]) if i >= 2 else None
                for i in range(len(df))]
    assert bool(result.iloc[2]) == expected[2]
    assert bool(result.iloc[3]) == expected[3]
    assert bool(result.iloc[4]) == expected[4]


def test_simple_indicator_vs_static_number(df):
    result = evaluate_condition("rsi", ">=", "45", df)
    assert list(result) == [True, True, False, True, False]


def test_a_formula_that_is_already_a_comparison_needs_no_operator(df):
    result = evaluate_condition("rsi >= 45", None, None, df)
    assert list(result) == [True, True, False, True, False]


def test_a_pure_arithmetic_formula_with_no_operator_is_rejected(df):
    with pytest.raises(FormulaError, match="boolean formula"):
        evaluate_condition("rsi - 45", None, None, df)


def test_short_alias_resolves_to_the_canonical_field(df):
    assert list(parse_and_evaluate("sk", df)) == list(df["stoch_k"])
    assert ALIASES["sk"] == Field.STOCH_K.value


def test_unknown_field_is_rejected(df):
    with pytest.raises(FormulaError, match="unknown field"):
        parse_and_evaluate("not_a_real_field", df)


def test_function_calls_are_rejected(df):
    with pytest.raises(FormulaError, match="only the built-in slope"):
        parse_and_evaluate("abs(close)", df)


def test_chained_comparisons_are_rejected(df):
    with pytest.raises(FormulaError, match="chained comparisons"):
        parse_and_evaluate("0 < rsi < 100", df)


def test_a_field_missing_from_the_dataframe_raises_clearly(df):
    with pytest.raises(FormulaError, match="no data"):
        parse_and_evaluate("atr", df)  # atr not in this fixture's columns


def test_unknown_operator_symbol_is_rejected(df):
    with pytest.raises(FormulaError, match="unknown operator"):
        evaluate_condition("rsi", "=>", "45", df)


def _cond(left_formula, operator=None, right_formula=None):
    return {"left_formula": left_formula, "operator": operator, "right_formula": right_formula}


def test_evaluate_tree_ands_two_plain_conditions(df):
    tree = {"operator": "AND", "conditions": [_cond("rsi", ">=", "45"), _cond("close", ">", "99")], "groups": []}
    result = evaluate_tree(tree, df)
    expected = [(r >= 45) and (c > 99) for r, c in zip(df["rsi"], df["close"])]
    assert list(result) == expected


def test_evaluate_tree_ors_two_plain_conditions(df):
    tree = {"operator": "OR", "conditions": [_cond("rsi", ">=", "60"), _cond("close", "<", "98.5")], "groups": []}
    result = evaluate_tree(tree, df)
    expected = [(r >= 60) or (c < 98.5) for r, c in zip(df["rsi"], df["close"])]
    assert list(result) == expected


def test_evaluate_tree_handles_nested_and_or_groups(df):
    # AND(rsi>=45, OR(close>101, close<99))
    tree = {
        "operator": "AND",
        "conditions": [_cond("rsi", ">=", "45")],
        "groups": [{
            "operator": "OR",
            "conditions": [_cond("close", ">", "101"), _cond("close", "<", "99")],
            "groups": [],
        }],
    }
    result = evaluate_tree(tree, df)
    expected = [(r >= 45) and (c > 101 or c < 99) for r, c in zip(df["rsi"], df["close"])]
    assert list(result) == expected


def test_evaluate_tree_rejects_an_event_leaf_with_no_left_formula(df):
    tree = {"operator": "AND", "conditions": [{"element_code": "double_top"}], "groups": []}
    with pytest.raises(FormulaError, match="event-pattern leaves"):
        evaluate_tree(tree, df)


def test_evaluate_tree_rejects_an_empty_group(df):
    with pytest.raises(FormulaError, match="empty condition group"):
        evaluate_tree({"operator": "AND", "conditions": [], "groups": []}, df)


def test_slope_computes_change_over_n_candles(df):
    # slope(close, 2) at row i = (close[i] - close[i-2]) / 2
    result = parse_and_evaluate("slope(close, 2)", df)
    assert pd.isna(result.iloc[0]) and pd.isna(result.iloc[1])
    assert result.iloc[2] == pytest.approx((df["close"][2] - df["close"][0]) / 2)
    assert result.iloc[4] == pytest.approx((df["close"][4] - df["close"][2]) / 2)


def test_slope_usable_in_a_comparison(df):
    result = evaluate_condition("slope(rsi, 1)", ">", "0", df)
    # rsi = [50, 55, 40, 60, 35] -> slope(rsi,1) = [nan, +5, -15, +20, -25]
    # a NaN > 0 comparison is False (not NaN) in pandas -- standard behavior
    assert list(result) == [False, True, False, True, False]


def test_slope_rejects_a_non_positive_lookback(df):
    with pytest.raises(FormulaError, match="positive integer"):
        parse_and_evaluate("slope(close, 0)", df)
    with pytest.raises(FormulaError, match="positive integer"):
        parse_and_evaluate("slope(close, -1)", df)


def test_slope_rejects_a_non_field_first_argument(df):
    with pytest.raises(FormulaError, match="bare field name"):
        parse_and_evaluate("slope(close - open, 1)", df)


def test_mean_computes_rolling_average_over_n_candles(df):
    # mean(close, 3) at row i = average of close[i-2..i]
    result = parse_and_evaluate("mean(close, 3)", df)
    assert pd.isna(result.iloc[0]) and pd.isna(result.iloc[1])
    assert result.iloc[2] == pytest.approx((df["close"][0] + df["close"][1] + df["close"][2]) / 3)
    assert result.iloc[4] == pytest.approx((df["close"][2] + df["close"][3] + df["close"][4]) / 3)


def test_mean_usable_in_a_comparison(df):
    result = evaluate_condition("mean(rsi, 2)", ">", "45", df)
    # rsi = [50, 55, 40, 60, 35] -> mean(rsi,2) = [nan, 52.5, 47.5, 50, 47.5]
    assert list(result) == [False, True, True, True, True]


def test_mean_rejects_a_non_positive_lookback(df):
    with pytest.raises(FormulaError, match="positive integer"):
        parse_and_evaluate("mean(close, 0)", df)


def test_mean_rejects_a_non_field_first_argument(df):
    with pytest.raises(FormulaError, match="bare field name"):
        parse_and_evaluate("mean(close - open, 2)", df)


def test_mean_does_not_leak_across_a_trading_day_boundary():
    import datetime as dt
    df = pd.DataFrame({
        "volume": [100.0, 200.0, 300.0, 400.0],
        TRADING_DATE_COLUMN: [dt.date(2026, 1, 1), dt.date(2026, 1, 1), dt.date(2026, 1, 1), dt.date(2026, 1, 2)],
    })
    result = parse_and_evaluate("mean(volume, 3)", df)
    assert result.iloc[2] == pytest.approx((100.0 + 200.0 + 300.0) / 3)
    # day 2's own first candle -- only 1 value available in ITS OWN day, not
    # a 3-candle window reaching back into day 1
    assert pd.isna(result.iloc[3])


def test_context_constant_is_usable_in_a_formula(df):
    result = evaluate_condition("rsi", ">", "threshold", df, context={"threshold": 45})
    assert list(result) == [True, True, False, True, False]


def test_context_constant_never_shadows_a_real_field(df):
    # "rsi" is a real Field -- a context dict that also has an "rsi" key
    # must be ignored, the real field always wins
    result = parse_and_evaluate("rsi", df, context={"rsi": 999})
    assert list(result) == list(df["rsi"])


def test_unknown_name_error_lists_context_keys_when_provided(df):
    with pytest.raises(FormulaError, match="order_size"):
        parse_and_evaluate("not_a_field", df, context={"order_size": 5000})


def test_offset_does_not_leak_across_a_trading_day_boundary():
    # day 1: two candles, day 2: one candle -- close[-1] on day 2's first
    # candle must be NaN, not day 1's last close
    import datetime as dt
    df = pd.DataFrame({
        "close": [10.0, 11.0, 20.0],
        TRADING_DATE_COLUMN: [dt.date(2026, 1, 1), dt.date(2026, 1, 1), dt.date(2026, 1, 2)],
    })
    result = parse_and_evaluate("close[-1]", df)
    assert pd.isna(result.iloc[0])  # first candle of day 1 -- no prior candle at all
    assert result.iloc[1] == 10.0  # second candle of day 1 -- day 1's own first close
    assert pd.isna(result.iloc[2])  # first candle of day 2 -- must NOT see day 1's 11.0
