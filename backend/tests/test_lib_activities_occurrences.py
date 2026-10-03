from datetime import date, datetime, timezone

from db.models import InstrumentActivity, SubscribedSymbol
from db.ops import LibActivities

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"


def _dt(d, h=9, mi=15):
    return datetime(2026, 1, d, h, mi, tzinfo=timezone.utc)


def _register(session_factory, symbol=SYMBOL) -> int:
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=symbol, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id=f"SEC-{symbol}", previous_close=100.0,
        )
        session.add(row)
        session.commit()
        return row.id


def _activity(instrument_id, ts, activity_type, activity, timeframe="1min", intensity=1.0):
    return InstrumentActivity(
        instrument_id=instrument_id, timeframe=timeframe, ts=ts,
        activity_type=activity_type, activity=activity, intensity=intensity,
        open_price=100, high_price=101, low_price=99, close_price=100.5,
    )


def test_count_by_pattern_groups_correctly(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([
            _activity(instrument_id, _dt(1), "candle_pattern", "doji"),
            _activity(instrument_id, _dt(2), "candle_pattern", "doji"),
            _activity(instrument_id, _dt(3), "candle_pattern", "hammer"),
            _activity(instrument_id, _dt(4), "indicator", "rsi_cross_above_60"),
        ])
        session.commit()

    with session_factory() as session:
        rows = LibActivities.count_by_pattern(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0),
        )
    counts = {(t, a): c for t, a, c in rows}
    assert counts[("candle_pattern", "doji")] == 2
    assert counts[("candle_pattern", "hammer")] == 1
    assert counts[("indicator", "rsi_cross_above_60")] == 1


def test_count_by_pattern_filters_by_date_range(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([
            _activity(instrument_id, _dt(1), "candle_pattern", "doji"),
            _activity(instrument_id, _dt(20), "candle_pattern", "doji"),
        ])
        session.commit()

    with session_factory() as session:
        rows = LibActivities.count_by_pattern(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(10, 0, 0),
        )
    counts = {(t, a): c for t, a, c in rows}
    assert counts[("candle_pattern", "doji")] == 1  # only the in-range one


def test_count_by_pattern_filters_by_timeframe(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([
            _activity(instrument_id, _dt(1), "candle_pattern", "doji", timeframe="1min"),
            _activity(instrument_id, _dt(1), "candle_pattern", "doji", timeframe="5min"),
        ])
        session.commit()

    with session_factory() as session:
        rows = LibActivities.count_by_pattern(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0),
        )
    assert sum(c for _, _, c in rows) == 1


def test_count_by_pattern_restricts_to_requested_patterns(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([
            _activity(instrument_id, _dt(1), "candle_pattern", "doji"),
            _activity(instrument_id, _dt(2), "candle_pattern", "hammer"),
        ])
        session.commit()

    with session_factory() as session:
        rows = LibActivities.count_by_pattern(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), patterns=["doji"],
        )
    assert [(a, c) for _, a, c in rows] == [("doji", 1)]


# --------------------------------------------------------------------- intensity_values_by_pattern

def test_intensity_values_by_pattern_groups_and_excludes_nulls(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([
            _activity(instrument_id, _dt(1), "candle_pattern", "hammer", intensity=2.5),
            _activity(instrument_id, _dt(2), "candle_pattern", "hammer", intensity=3.5),
            _activity(instrument_id, _dt(3), "multi_candle", "three_white_soldiers", intensity=None),
        ])
        session.commit()

    with session_factory() as session:
        values = LibActivities.intensity_values_by_pattern(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0),
        )
    assert sorted(values["hammer"]) == [2.5, 3.5]
    assert "three_white_soldiers" not in values  # its only occurrence had a null intensity


def test_intensity_values_by_pattern_restricts_to_requested_patterns(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        session.add_all([
            _activity(instrument_id, _dt(1), "candle_pattern", "hammer", intensity=2.0),
            _activity(instrument_id, _dt(2), "candle_pattern", "doji", intensity=0.1),
        ])
        session.commit()

    with session_factory() as session:
        values = LibActivities.intensity_values_by_pattern(
            session, instrument_id, ["1min"], _dt(1, 0, 0), _dt(28, 0, 0), patterns=["hammer"],
        )
    assert list(values.keys()) == ["hammer"]


# --------------------------------------------------------------------- InstrumentActivityDailyCount

def _row(instrument_id, ts, activity_type, activity, timeframe="1min"):
    return {
        "instrument_id": instrument_id, "timeframe": timeframe, "ts": ts,
        "activity_type": activity_type, "activity": activity, "intensity": None,
        "open_price": 100, "high_price": 101, "low_price": 99, "close_price": 100.5,
    }


def test_persist_bulk_bumps_the_daily_count_rollup(session_factory):
    instrument_id = _register(session_factory)
    LibActivities.persist_bulk(session_factory, [
        _row(instrument_id, _dt(5, 9, 15), "candle_pattern", "doji"),
        _row(instrument_id, _dt(5, 9, 20), "candle_pattern", "hammer", timeframe="3min"),
        _row(instrument_id, _dt(5, 9, 25), "indicator", "macd_bullish_cross"),
    ])
    with session_factory() as session:
        assert LibActivities.get_daily_counts(session, instrument_id, date(2026, 1, 5)) == {
            "candle_pattern": 2, "indicator": 1,
        }


def test_persist_bulk_keeps_separate_instruments_and_days_separate(session_factory):
    a = _register(session_factory, symbol="RELIANCE")
    b = _register(session_factory, symbol="TCS")
    LibActivities.persist_bulk(session_factory, [
        _row(a, _dt(5, 9, 15), "candle_pattern", "doji"),
        _row(a, _dt(6, 9, 15), "candle_pattern", "doji"),  # a different day -- separate bucket
        _row(b, _dt(5, 9, 15), "indicator", "macd_bullish_cross"),
    ])
    with session_factory() as session:
        assert LibActivities.get_daily_counts(session, a, date(2026, 1, 5)) == {"candle_pattern": 1, "indicator": 0}
        assert LibActivities.get_daily_counts(session, a, date(2026, 1, 6)) == {"candle_pattern": 1, "indicator": 0}
        assert LibActivities.get_daily_counts(session, b, date(2026, 1, 5)) == {"candle_pattern": 0, "indicator": 1}


def test_categories_outside_the_tracked_two_are_not_counted(session_factory):
    instrument_id = _register(session_factory)
    LibActivities.persist_bulk(session_factory, [
        _row(instrument_id, _dt(5, 9, 15), "graph_formation", "double_top"),
        _row(instrument_id, _dt(5, 9, 15), "structure", "swing_high"),
        _row(instrument_id, _dt(5, 9, 16), "price_action", "vwap_gap_fill"),
    ])
    with session_factory() as session:
        assert LibActivities.get_daily_counts(session, instrument_id, date(2026, 1, 5)) == {
            "candle_pattern": 0, "indicator": 0,
        }


def test_daily_count_bucket_follows_the_ist_calendar_day_not_utc(session_factory):
    instrument_id = _register(session_factory)
    # 18:29 UTC = 23:59 IST (still 5-Jan); 18:30 UTC = 00:00 IST (rolls to 6-Jan)
    LibActivities.persist_bulk(session_factory, [
        _row(instrument_id, datetime(2026, 1, 5, 18, 29, tzinfo=timezone.utc), "candle_pattern", "doji"),
        _row(instrument_id, datetime(2026, 1, 5, 18, 30, tzinfo=timezone.utc), "candle_pattern", "hammer"),
    ])
    with session_factory() as session:
        assert LibActivities.get_daily_counts(session, instrument_id, date(2026, 1, 5))["candle_pattern"] == 1
        assert LibActivities.get_daily_counts(session, instrument_id, date(2026, 1, 6))["candle_pattern"] == 1


def test_persist_one_bumps_the_rollup_and_never_double_counts_a_duplicate(session_factory):
    instrument_id = _register(session_factory)
    row = _row(instrument_id, _dt(5, 9, 15), "candle_pattern", "doji")
    LibActivities.persist_one(session_factory, row)
    LibActivities.persist_one(session_factory, dict(row))  # exact duplicate -- must not double-count

    with session_factory() as session:
        assert LibActivities.get_daily_counts(session, instrument_id, date(2026, 1, 5))["candle_pattern"] == 1


def test_get_daily_counts_is_zero_for_an_untouched_day(session_factory):
    instrument_id = _register(session_factory)
    with session_factory() as session:
        assert LibActivities.get_daily_counts(session, instrument_id, date(2026, 1, 1)) == {
            "candle_pattern": 0, "indicator": 0,
        }
