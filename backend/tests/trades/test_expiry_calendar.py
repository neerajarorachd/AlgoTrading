from datetime import date

from expiry_calendar import current_expiry, expiry_context, monthly_expiry


def test_thursday_rule_up_to_august_2025():
    assert monthly_expiry(2025, 8) == date(2025, 8, 28)   # last Thursday
    assert monthly_expiry(2024, 11) == date(2024, 11, 28)


def test_tuesday_rule_from_september_2025():
    assert monthly_expiry(2025, 9) == date(2025, 9, 30)   # last Tuesday
    assert monthly_expiry(2026, 10) == date(2026, 10, 27)


def test_holiday_moves_expiry_to_previous_trading_day():
    # 2026-03-31 (Mahavir Jayanti) and 2026-11-24 (Guru Nanak) are Tuesday holidays
    assert monthly_expiry(2026, 3) == date(2026, 3, 30)
    assert monthly_expiry(2026, 11) == date(2026, 11, 23)


def test_context_counts_trading_days():
    ctx = expiry_context(date(2026, 11, 11))
    assert ctx["expiry_date"] == date(2026, 11, 23)
    # 12, 13, 16, 17, 18, 19, 20, 23 -> 8 trading days
    assert ctx["days_to_expiry"] == 8 and ctx["is_expiry_day"] is False and ctx["is_expiry_week"] is False
    ctx = expiry_context(date(2026, 11, 23))
    assert ctx["days_to_expiry"] == 0 and ctx["is_expiry_day"] and ctx["is_expiry_week"]
    ctx = expiry_context(date(2026, 11, 17))  # 17,18,19,20,23 -> 4 days after today
    assert ctx["days_to_expiry"] == 4 and ctx["is_expiry_week"] and not ctx["is_expiry_day"]


def test_after_expiry_rolls_to_next_month():
    assert current_expiry(date(2026, 11, 25)) == date(2026, 12, 29)
    assert current_expiry(date(2026, 12, 30)).month == 1
    assert expiry_context(None)["expiry_date"] is None
