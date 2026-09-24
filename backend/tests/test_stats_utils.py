import pytest

from stats_utils import wilson_lower_bound


def test_wilson_lower_bound_matches_the_users_own_worked_example():
    """"30 historical wins with 75% will win over 2 historical wins with
    90% win rate" — the exact scenario this function exists to solve."""
    big_sample = wilson_lower_bound(22.5, 30, 0.95)  # 30 occurrences, 75% win rate
    small_sample = wilson_lower_bound(1.8, 2, 0.95)  # 2 occurrences, 90% win rate
    assert big_sample == pytest.approx(0.5735, abs=0.001)
    assert small_sample == pytest.approx(0.2787, abs=0.001)
    assert big_sample > small_sample


def test_wilson_lower_bound_with_integer_win_counts():
    """Real callers always pass integer win counts — same scenario, exact
    integers instead of the pre-multiplied win_pct*total from the docstring
    example."""
    assert wilson_lower_bound(23, 30, 0.95) > wilson_lower_bound(2, 2, 0.95)


def test_wilson_lower_bound_zero_total_is_zero():
    assert wilson_lower_bound(0, 0) == 0.0
    assert wilson_lower_bound(0, -5) == 0.0


def test_wilson_lower_bound_zero_wins_is_low_but_nonnegative():
    result = wilson_lower_bound(0, 10, 0.95)
    assert result >= 0.0
    assert result < 0.3


def test_wilson_lower_bound_all_wins_is_high_but_strictly_below_one():
    result = wilson_lower_bound(10, 10, 0.95)
    assert 0.6 < result < 1.0


def test_wilson_lower_bound_higher_confidence_gives_a_lower_bound():
    """A wider confidence interval (higher confidence) pulls the LOWER
    bound down, not up — monotonic in the expected direction."""
    low_confidence = wilson_lower_bound(15, 20, 0.80)
    high_confidence = wilson_lower_bound(15, 20, 0.99)
    assert high_confidence < low_confidence
