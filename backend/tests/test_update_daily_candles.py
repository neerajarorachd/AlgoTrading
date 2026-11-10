from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from brokers.models import BrokerAPIError, Candle
from db.models import Base
from db.models_daily import CandleDaily
from db.ops import LibCandlesDaily as ops
from db.session import build_session_factory, session_scope
from scripts import update_daily_candles as u

IST = ops.IST
ITEM = {"symbol": "X", "security_id": "1", "exchange_segment": "NSE_EQ"}
AFTER_CLOSE = datetime(2026, 10, 30, 16, 30, tzinfo=IST)
QUIET = lambda m: None  # noqa: E731


def _candle(day: date, close=100.0, volume=1000):
    ts = datetime(day.year, day.month, day.day, tzinfo=IST).astimezone(timezone.utc)
    return Candle(symbol="X", timeframe="1day", timestamp=ts, open=99.0, high=101.0,
                  low=98.0, close=close, volume=volume)


class FakeBroker:
    """Returns the days in [from, to) -- Dhan's daily toDate is exclusive."""

    def __init__(self, days, fail_for=()):
        self.days, self.fail_for = days, set(fail_for)

    def get_historical_data(self, symbol, security_id, segment, timeframe, frm, to):
        if symbol in self.fail_for:
            raise RuntimeError("boom")
        return [_candle(d, close=100 + d.day) for d in self.days if frm.date() <= d < to.date()]


@pytest.fixture
def sf():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def _dates(sf):
    with session_scope(sf) as s:
        return [r.trade_date for r in s.query(CandleDaily).order_by(CandleDaily.trade_date)]


def test_first_run_fetches_history(sf):
    days = [date(2026, 10, 26) + timedelta(d) for d in range(5)]  # 26..30
    u.run(sf, FakeBroker(days), [ITEM], AFTER_CLOSE, 10, log=QUIET)
    assert _dates(sf) == days


def test_missed_days_are_filled(sf):
    days = [date(2026, 10, 26) + timedelta(d) for d in range(5)]
    u.run(sf, FakeBroker(days[:2]), [ITEM], datetime(2026, 10, 27, 16, 30, tzinfo=IST), 10, log=QUIET)
    assert _dates(sf) == days[:2]
    u.run(sf, FakeBroker(days), [ITEM], AFTER_CLOSE, 10, log=QUIET)  # 3 days later
    assert _dates(sf) == days


def test_rerun_is_idempotent(sf):
    days = [date(2026, 10, 29), date(2026, 10, 30)]
    b = FakeBroker(days)
    u.run(sf, b, [ITEM], AFTER_CLOSE, 10, log=QUIET)
    r = u.run(sf, b, [ITEM], AFTER_CLOSE, 10, log=QUIET)
    assert r["rows_changed"] == 0 and len(_dates(sf)) == 2


def test_partial_today_not_stored_before_close(sf):
    days = [date(2026, 10, 29), date(2026, 10, 30)]
    u.run(sf, FakeBroker(days), [ITEM], datetime(2026, 10, 30, 12, 0, tzinfo=IST), 10, log=QUIET)
    assert _dates(sf) == [date(2026, 10, 29)]


def test_changed_candle_is_refreshed(sf):
    d = date(2026, 10, 30)
    ops.upsert_candles(sf, "X", "NSE_EQ", [_candle(d, close=100.0)])
    assert ops.upsert_candles(sf, "X", "NSE_EQ", [_candle(d, close=105.0)]) == 1
    with session_scope(sf) as s:
        assert float(s.query(CandleDaily).one().close_price) == 105.0


def test_one_failing_stock_does_not_stop_others(sf):
    items = [{**ITEM, "symbol": "BAD"}, ITEM]
    r = u.run(sf, FakeBroker([date(2026, 10, 30)], fail_for=["BAD"]), items, AFTER_CLOSE, 10, log=QUIET)
    assert (r["ok"], r["failed"]) == (1, 1) and len(_dates(sf)) == 1


def test_plan_range_moves_off_weekends_and_holidays():
    # Tue 2026-11-10 is an NSE holiday (Diwali-Balipratipada); 11-07/08 a weekend
    holiday_evening = datetime(2026, 11, 10, 16, 30, tzinfo=IST)
    # newest = Mon 11-09 (stepping back); toDate = Wed 11-11 (stepping on over the holiday)
    assert u.plan_range(date(2026, 11, 6), holiday_evening, 10) == (date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 11))
    # Saturday: newest = Fri 11-06, toDate = Mon 11-09 (over the weekend)
    saturday = datetime(2026, 11, 7, 10, 0, tzinfo=IST)
    assert u.plan_range(date(2026, 11, 5), saturday, 10) == (date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9))
    # before the close today does not count yet: Mon 11-09 noon -> newest Fri 11-06
    assert u.plan_range(date(2026, 11, 5), datetime(2026, 11, 9, 12, 0, tzinfo=IST), 10)[1] == date(2026, 11, 6)
    # a new stock: 400 trading days (+5% margin) back from the newest day, on a trading day
    start = u.plan_range(None, holiday_evening)[0]
    from market_calendar import is_trading_day, trading_days
    assert is_trading_day(start) and len(trading_days(start, date(2026, 11, 9))) == 420


def test_new_stock_gets_400_trading_days(sf):
    from market_calendar import trading_days
    days = trading_days(date(2024, 1, 1), date(2026, 11, 9))
    u.run(sf, FakeBroker(days), [ITEM], datetime(2026, 11, 10, 16, 30, tzinfo=IST), log=QUIET)
    assert len(_dates(sf)) >= 400 and _dates(sf)[-1] == date(2026, 11, 9)


def test_up_to_date_stock_makes_no_call(sf):
    class CountingBroker(FakeBroker):
        calls = 0

        def get_historical_data(self, *a):
            CountingBroker.calls += 1
            return super().get_historical_data(*a)

    ops.upsert_candles(sf, "X", "NSE_EQ", [_candle(date(2026, 11, 9))])
    # holiday evening and the weekend before: nothing newer can exist yet
    assert u.plan_range(date(2026, 11, 9), datetime(2026, 11, 10, 16, 30, tzinfo=IST), 10) is None
    r = u.run(sf, CountingBroker([date(2026, 11, 9)]), [ITEM], datetime(2026, 11, 10, 16, 30, tzinfo=IST), 10, log=QUIET)
    assert CountingBroker.calls == 0 and r["ok"] == 1


def test_only_newer_days_are_stored(sf):
    days = [date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9)]
    ops.upsert_candles(sf, "X", "NSE_EQ", [_candle(d, close=100 + d.day) for d in days[:2]])
    r = u.update_symbol(sf, FakeBroker(days), ITEM, datetime(2026, 11, 10, 16, 30, tzinfo=IST), 10)
    assert r["fetched"] == 2 and r["changed"] == 2 and not r["adjusted"]
    assert _dates(sf) == days


def test_back_adjusted_history_is_replaced(sf):
    # a 1:1 bonus: Dhan now returns every old close halved
    days = [date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9)]
    ops.upsert_candles(sf, "X", "NSE_EQ", [_candle(d, close=200.0) for d in days[:4]])

    class HalvedBroker(FakeBroker):
        def get_historical_data(self, symbol, security_id, segment, timeframe, frm, to):
            return [_candle(d, close=100.0) for d in self.days if frm.date() <= d < to.date()]

    r = u.update_symbol(sf, HalvedBroker(days), ITEM, datetime(2026, 11, 10, 16, 30, tzinfo=IST), 10)
    assert r["adjusted"] and r["fetched"] == 5
    with session_scope(sf) as s:
        assert {float(x.close_price) for x in s.query(CandleDaily)} == {100.0}
    assert _dates(sf) == days


def test_no_data_range_is_not_an_error_when_history_exists(sf):
    class NoDataBroker(FakeBroker):
        def get_historical_data(self, *a):
            raise BrokerAPIError('Dhan API error 400: {"errorCode":"DH-907"}')

    ops.upsert_candles(sf, "X", "NSE_EQ", [_candle(date(2026, 10, 30))])
    r = u.run(sf, NoDataBroker([]), [ITEM], datetime(2026, 10, 31, 16, 30, tzinfo=IST), 10, log=QUIET)
    assert (r["ok"], r["failed"]) == (1, 0)


def test_no_data_for_brand_new_stock_is_a_failure(sf):
    class NoDataBroker(FakeBroker):
        def get_historical_data(self, *a):
            raise BrokerAPIError('Dhan API error 400: {"errorCode":"DH-907"}')

    r = u.run(sf, NoDataBroker([]), [ITEM], AFTER_CLOSE, 10, log=QUIET)
    assert r["failed"] == 1
