from datetime import datetime, timezone

from db.models import InstrumentActivity, SubscribedSymbol
from db.ops import LibActivities

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"


def _dt(d, h=9, mi=15):
    return datetime(2026, 1, d, h, mi, tzinfo=timezone.utc)


def _register(session_factory) -> int:
    with session_factory() as session:
        row = SubscribedSymbol(
            symbol=SYMBOL, exchange="NSE", segment="EQUITY", exchange_segment=SEG,
            security_id="1333", previous_close=100.0,
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
