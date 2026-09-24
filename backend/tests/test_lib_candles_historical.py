from datetime import date, datetime, timezone

from brokers.models import Candle
from db.models import CandleHistorical
from db.ops import LibCandlesHistorical

SYMBOL = "RELIANCE"
SEG = "NSE_EQ"


def _daily(day, close=100.0):
    return Candle(
        symbol=SYMBOL, timeframe="1day", timestamp=datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc),
        open=close - 1, high=close + 1, low=close - 2, close=close, volume=1000,
    )


def test_persist_bulk_writes_rows_and_get_range_reads_them_back(session_factory):
    candles = [_daily(date(2026, 1, d)) for d in (1, 2, 3)]
    LibCandlesHistorical.persist_bulk(session_factory, [(SYMBOL, SEG, c) for c in candles])

    with session_factory() as session:
        rows = LibCandlesHistorical.get_range(session, SYMBOL, SEG, "1day")
    assert [r.ts.date() for r in rows] == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]


def test_get_last_ts_returns_the_latest_candle(session_factory):
    candles = [_daily(date(2026, 1, d)) for d in (1, 5, 3)]
    LibCandlesHistorical.persist_bulk(session_factory, [(SYMBOL, SEG, c) for c in candles])

    with session_factory() as session:
        last_ts = LibCandlesHistorical.get_last_ts(session, SYMBOL, SEG, "1day")
    assert last_ts.date() == date(2026, 1, 5)


def test_get_last_ts_returns_none_when_no_data(session_factory):
    with session_factory() as session:
        assert LibCandlesHistorical.get_last_ts(session, SYMBOL, SEG, "1day") is None


def test_persist_bulk_recovers_from_a_conflicting_row(session_factory):
    original = _daily(date(2026, 1, 1), close=100.0)
    LibCandlesHistorical.persist_bulk(session_factory, [(SYMBOL, SEG, original)])

    updated = _daily(date(2026, 1, 1), close=105.0)  # same natural key, new price — an overlapping re-fetch
    LibCandlesHistorical.persist_bulk(session_factory, [(SYMBOL, SEG, updated)])

    with session_factory() as session:
        rows = session.query(CandleHistorical).filter_by(symbol=SYMBOL, timeframe="1day").all()
    assert len(rows) == 1
    assert float(rows[0].close_price) == 105.0  # upserted in place, not duplicated


def _naive_utc_pair(start, end):
    # SQLite round-trips DateTime columns as naive, dropping tzinfo — SQL
    # Server (production) doesn't have this quirk, but get_coverage itself
    # deliberately doesn't normalize (matching get_last_ts's own
    # convention — see historical_data_service.py for where that
    # normalization actually happens).
    return (start.replace(tzinfo=None), end.replace(tzinfo=None))


def test_coverage_round_trips(session_factory):
    with session_factory() as session:
        assert LibCandlesHistorical.get_coverage(session, SYMBOL, SEG, "1day") is None

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    LibCandlesHistorical.set_coverage(session_factory, SYMBOL, SEG, "1day", start, end)

    with session_factory() as session:
        coverage = LibCandlesHistorical.get_coverage(session, SYMBOL, SEG, "1day")
    assert coverage == _naive_utc_pair(start, end)


def test_set_coverage_overwrites_an_existing_row(session_factory):
    LibCandlesHistorical.set_coverage(
        session_factory, SYMBOL, SEG, "1day",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    new_start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    new_end = datetime(2026, 2, 10, tzinfo=timezone.utc)
    LibCandlesHistorical.set_coverage(session_factory, SYMBOL, SEG, "1day", new_start, new_end)

    with session_factory() as session:
        coverage = LibCandlesHistorical.get_coverage(session, SYMBOL, SEG, "1day")
    assert coverage == _naive_utc_pair(new_start, new_end)
