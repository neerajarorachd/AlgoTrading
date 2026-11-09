import pandas as pd

from activity_engine import SwingPoint, swing_leg_pcts
from condition_evaluator import DERIVED_FIELDS


def _p(kind, price):
    return SwingPoint(kind, price, None)


def test_ll_hh_hl_legs():
    # LH 105, LL 100, HH 102, HL 100.98
    up, down = swing_leg_pcts([_p("high", 105), _p("low", 100), _p("high", 102), _p("low", 100.98)])
    assert up == 2.0 and down == 1.0


def test_legs_none_until_formed():
    assert swing_leg_pcts([]) == (None, None)
    assert swing_leg_pcts([_p("low", 100)]) == (None, None)


def test_fields_read_row_values_or_nan():
    df = pd.DataFrame({"up_leg_pct": [None, 1.6]})
    assert DERIVED_FIELDS["up_leg_pct"](df).iloc[-1] == 1.6
    assert DERIVED_FIELDS["down_leg_pct"](df).isna().all()
