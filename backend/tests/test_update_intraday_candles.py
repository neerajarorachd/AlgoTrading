from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from brokers.models import BrokerAPIError, Candle
from db.models import Base, CandleHistorical
from db.ops import LibCandlesHistorical as ops
from db.ops import LibCandlesHistoricalSync as sync
from db.session import build_session_factory, session_scope
from scripts import update_intraday_candles as u

IST = sync.IST
ITEM = {"symbol": "X", "security_id": "1", "exchange_segment": "NSE_EQ"}
AFTER_CLOSE = datetime(2026, 10, 30, 16, 45, tzinfo=IST)
QUIET = lambda m: None  # noqa: E731


def _ts(day: date, hh: int, mm: int) -> datetime:
    """IST wall-clock time -> naive UTC."""
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST).astimezone(timezone.utc).replace(tzinfo=None)


def _candles(day: date, tf: str, minutes: int, n: int, base=100.0):
    out = []
    for i in range(n):
        ts = _ts(day, 9, 15) + timedelta(minutes=minutes * i)
        out.append(Candle(symbol="X", timeframe=tf, timestamp=ts.replace(tzinfo=timezone.utc),
                          open=base + i, high=base + i + 2, low=base + i - 1, close=base + i + 1, volume=10))
    return out


class FakeBroker:
    """Serves candles per timeframe from {tf: [Candle,...]}, honouring [from, to)."""

    def __init__(self, data, fail_for=()):
        self.data, self.fail_for, self.calls = data, set(fail_for), []

    def get_historical_data(self, symbol, security_id, segment, timeframe, frm, to):
        self.calls.append(timeframe)
        if symbol in self.fail_for:
            raise RuntimeError("boom")
        got = [c for c in self.data.get(timeframe, [])
               if frm.date() <= c.timestamp.astimezone(IST).date() < to.date()]
        if not got:
            raise BrokerAPIError('Dhan API error 400: {"errorCode":"DH-907"}')
        return got


@pytest.fixture
def sf():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def _count(sf, tf):
    with session_scope(sf) as s:
        return s.query(CandleHistorical).filter_by(timeframe=tf).count()


def _seed(sf, tf, day, minutes, n):
    sync.insert_missing(sf, "X", "NSE_EQ", tf, [u._row(c) for c in _candles(day, tf, minutes, n)])


D29, D30 = date(2026, 10, 29), date(2026, 10, 30)


def test_stock_without_1min_gets_5min_directly_and_fills_gap(sf):
    _seed(sf, "5min", date(2026, 10, 27), 5, 75)
    data = {"5min": _candles(date(2026, 10, 27), "5min", 5, 75) + _candles(D29, "5min", 5, 75)
            + _candles(D30, "5min", 5, 75)}
    b = FakeBroker(data)
    u.run(sf, b, [ITEM], AFTER_CLOSE, log=QUIET)
    assert _count(sf, "5min") == 75 * 3 and set(b.calls) == {"5min"}
    assert _count(sf, "1min") == 0


def test_rerun_inserts_nothing(sf):
    data = {"5min": _candles(D30, "5min", 5, 75)}
    u.run(sf, FakeBroker(data), [ITEM], AFTER_CLOSE, log=QUIET)
    u.run(sf, FakeBroker(data), [ITEM], AFTER_CLOSE, log=QUIET)
    assert _count(sf, "5min") == 75


def test_today_not_stored_before_close(sf):
    _seed(sf, "5min", D29, 5, 75)
    data = {"5min": _candles(D30, "5min", 5, 40)}
    u.run(sf, FakeBroker(data), [ITEM], datetime(2026, 10, 30, 12, 0, tzinfo=IST), log=QUIET)
    assert _count(sf, "5min") == 75


def test_1min_stock_rolls_up_3_and_5_from_own_watermarks(sf):
    _seed(sf, "1min", D29, 1, 375)
    _seed(sf, "3min", D29, 3, 125)   # 3-min fully caught up to D29
    _seed(sf, "5min", D29, 5, 30)    # 5-min only half of D29: own, older watermark
    data = {"1min": _candles(D29, "1min", 1, 375) + _candles(D30, "1min", 1, 375)}
    r = u.run(sf, FakeBroker(data), [ITEM], AFTER_CLOSE, log=QUIET)
    assert r["failed"] == 0
    assert _count(sf, "1min") == 750
    assert _count(sf, "3min") == 250          # +125 for D30
    assert _count(sf, "5min") == 150          # rest of D29 (45) + D30 (75), no duplicates


def test_rollup_bucket_values():
    rows = [u._row(c) for c in _candles(D30, "1min", 1, 5)]
    (b,) = u.rollup(rows, 5)
    assert (b["open"], b["close"], b["high"], b["low"], b["volume"]) == (100.0, 105.0, 106.0, 99.0, 50)


def test_coverage_is_recorded(sf):
    u.run(sf, FakeBroker({"5min": _candles(D30, "5min", 5, 75)}), [ITEM], AFTER_CLOSE, log=QUIET)
    with session_scope(sf) as s:
        assert ops.get_coverage(s, "X", "NSE_EQ", "5min") is not None


def test_new_stock_with_no_data_is_a_failure_but_others_continue(sf):
    other = {**ITEM, "symbol": "Y"}
    r = u.run(sf, FakeBroker({"5min": []}, fail_for=["Y"]), [other, ITEM], AFTER_CLOSE, log=QUIET)
    assert (r["ok"], r["failed"]) == (0, 2)  # Y raised; X got nothing back and has no history
