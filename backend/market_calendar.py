"""NSE trading calendar for gap fill (ALGO-39): which days the market is
open, and which minutes a trading day has.

Closed days come from data/market_holidays.txt (every weekend plus NSE
holidays, one `YYYY-MM-DD  TYPE  note` per line). Weekends are closed even
for a year the file doesn't list yet. A closed day is never requested from
the broker and never counted as a gap -- that is what saves the calls.

Times are UTC throughout (09:15 IST = 03:45 UTC, 15:29 IST = 09:59 UTC), the
same convention as feed/candle_aggregator.py and feed/gap_fill.py."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

HOLIDAYS_FILE = Path(__file__).resolve().parent / "data" / "market_holidays.txt"
IST = timedelta(hours=5, minutes=30)
SESSION_OPEN_UTC = time(3, 45)    # 09:15 IST, the first 1-min candle
SESSION_LAST_UTC = time(9, 59)    # 15:29 IST, the last 1-min candle
SESSION_MINUTES = 375

_closed: Optional[Set[date]] = None
_exchange_years: Set[int] = set()
_warned_years: Set[int] = set()


def load_closed_days(path: Path = HOLIDAYS_FILE) -> Set[date]:
    """Every date listed in the holidays file (weekend or exchange)."""
    global _closed
    closed, years = set(), set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if not parts or parts[0].startswith("#"):
                continue
            try:
                d = date.fromisoformat(parts[0])
            except ValueError:
                continue
            closed.add(d)
            if len(parts) > 1 and parts[1] == "exchange":
                years.add(d.year)
    except OSError:
        logger.warning("Market calendar: %s not found -- only weekends count as closed", path)
    _closed = closed
    _exchange_years.clear()
    _exchange_years.update(years)
    return closed


def _closed_days() -> Set[date]:
    return _closed if _closed is not None else load_closed_days()


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    closed = _closed_days()
    if d.year not in _exchange_years and d.year not in _warned_years:
        _warned_years.add(d.year)
        logger.warning("Market calendar: no NSE holidays listed for %s in %s -- add them when NSE publishes the list",
                       d.year, HOLIDAYS_FILE.name)
    return d not in closed


def trading_days(start: date, end: date) -> List[date]:
    """Trading days from start to end, both included."""
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def ist_date(ts: datetime) -> date:
    ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
    return (ts + IST).date()


def session_minutes(d: date, until: Optional[datetime] = None) -> List[datetime]:
    """The 1-min candle start times (UTC) of trading day `d`, 09:15-15:29
    IST; with `until`, only minutes that have closed by then."""
    first = datetime.combine(d, SESSION_OPEN_UTC, tzinfo=timezone.utc)
    out = [first + timedelta(minutes=i) for i in range(SESSION_MINUTES)]
    if until is not None:
        until = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until
        out = [m for m in out if m + timedelta(minutes=1) <= until]
    return out


def missing_minutes(d: date, stored: Iterable[datetime], until: Optional[datetime] = None) -> List[datetime]:
    """Minutes of trading day `d` (closed by `until`) with no stored candle."""
    have = {(s.replace(tzinfo=timezone.utc) if s.tzinfo is None else s).replace(second=0, microsecond=0) for s in stored}
    return [m for m in session_minutes(d, until) if m not in have]
