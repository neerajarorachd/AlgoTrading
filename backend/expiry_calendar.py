"""NSE stock F&O monthly expiry, for the "expiry element" on every trade
(rulebook 3b): which expiry a trading day belongs to and how far away it is.

Rule (NSE circular, SEBI approval 2025-06-17): monthly stock derivatives
expire on the last Thursday of the month up to August 2025 and on the last
Tuesday from September 2025. When that day is a holiday, the contract
expires on the previous trading day. Holidays come from market_calendar
(backend/data/market_holidays.txt) -- a year missing from that file only
knows weekends, so its holiday-shifted expiries can be off by a day.

Pure functions, no DB. Dates are IST calendar dates.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Optional

from market_calendar import is_trading_day

TUESDAY_FROM = date(2025, 9, 1)   # first month on the Tuesday rule
_THU, _TUE = 3, 1


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


@lru_cache(maxsize=512)
def monthly_expiry(year: int, month: int) -> date:
    """The month's stock F&O expiry day (holiday-shifted to the trading day before)."""
    weekday = _TUE if date(year, month, 1) >= TUESDAY_FROM else _THU
    d = _last_weekday(year, month, weekday)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def current_expiry(d: date) -> date:
    """The nearest expiry on or after d (this month's, or next month's once it has passed)."""
    exp = monthly_expiry(d.year, d.month)
    if d > exp:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        exp = monthly_expiry(y, m)
    return exp


def trading_days_between(start: date, end: date) -> int:
    """Trading days after start up to and including end (0 when start == end)."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n


def expiry_context(d: Optional[date]) -> dict:
    """The expiry fields stored on a trade. expiry_week = the last five
    trading days up to and including expiry day (days_to_expiry 0-4)."""
    if d is None:
        return {"expiry_date": None, "days_to_expiry": None, "is_expiry_day": None, "is_expiry_week": None}
    exp = current_expiry(d)
    n = trading_days_between(d, exp)
    return {"expiry_date": exp, "days_to_expiry": n, "is_expiry_day": n == 0, "is_expiry_week": n <= 4}
