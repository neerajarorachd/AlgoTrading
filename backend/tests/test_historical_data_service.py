from datetime import datetime, timedelta, timezone

from brokers.models import Candle
from db.models import CandleHistorical
from db.ops import LibCandlesHistorical
from historical_data_service import ensure_data_available

SYMBOL = "RELIANCE"
SECURITY_ID = "1333"
SEG = "NSE_EQ"


class FakeRestBroker:
    def __init__(self, candles_per_call=1):
        self.calls = []
        self.candles_per_call = candles_per_call

    def get_historical_data(self, symbol, security_id, exchange_segment, timeframe, from_date, to_date):
        self.calls.append((symbol, security_id, exchange_segment, timeframe, from_date, to_date))
        return [
            Candle(
                symbol=symbol, timeframe=timeframe, timestamp=from_date + timedelta(minutes=i),
                open=100, high=101, low=99, close=100.5, volume=10,
            )
            for i in range(self.candles_per_call)
        ]


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def _seed_coverage(session_factory, covered_from, covered_to, timeframe="1day"):
    LibCandlesHistorical.set_coverage(session_factory, SYMBOL, SEG, timeframe, covered_from, covered_to)


def _naive(start, end):
    # get_coverage doesn't normalize tzinfo (matches get_last_ts's own
    # convention); SQLite (tests) round-trips DateTime as naive, unlike
    # SQL Server (production) — see LibCandlesHistorical tests for the
    # same pattern.
    return (start.replace(tzinfo=None), end.replace(tzinfo=None))


def test_fetches_full_range_when_no_existing_coverage(session_factory):
    broker = FakeRestBroker()
    start, end = _dt(2026, 1, 1), _dt(2026, 1, 5)

    result = ensure_data_available(
        session_factory, broker, SYMBOL, SECURITY_ID, SEG, "1day", start, end,
    )

    assert result["already_covered"] is False
    assert len(broker.calls) == 1
    _, _, _, timeframe, from_date, to_date = broker.calls[0]
    assert timeframe == "1day"
    assert from_date == start
    assert to_date == end
    assert result["fetched"] == 1

    with session_factory() as session:
        coverage = LibCandlesHistorical.get_coverage(session, SYMBOL, SEG, "1day")
    assert coverage == _naive(start, end)


def test_is_a_no_op_when_the_request_is_within_existing_coverage(session_factory):
    _seed_coverage(session_factory, _dt(2026, 1, 1), _dt(2026, 1, 10))

    broker = FakeRestBroker()
    result = ensure_data_available(
        session_factory, broker, SYMBOL, SECURITY_ID, SEG, "1day", _dt(2026, 1, 3), _dt(2026, 1, 7),
    )

    assert result["already_covered"] is True
    assert broker.calls == []


def test_fetches_only_the_gap_past_the_covered_range(session_factory):
    _seed_coverage(session_factory, _dt(2026, 1, 1), _dt(2026, 1, 3))

    broker = FakeRestBroker()
    result = ensure_data_available(
        session_factory, broker, SYMBOL, SECURITY_ID, SEG, "1day", _dt(2026, 1, 1), _dt(2026, 1, 10),
    )

    assert result["already_covered"] is False
    assert len(broker.calls) == 1
    assert broker.calls[0][4] == _dt(2026, 1, 4)  # the day after the covered range, not start_date
    assert broker.calls[0][5] == _dt(2026, 1, 10)

    with session_factory() as session:
        coverage = LibCandlesHistorical.get_coverage(session, SYMBOL, SEG, "1day")
    assert coverage == _naive(_dt(2026, 1, 1), _dt(2026, 1, 10))  # grown, not replaced


def test_fetches_only_the_gap_before_the_covered_range(session_factory):
    _seed_coverage(session_factory, _dt(2026, 1, 10), _dt(2026, 1, 15))

    broker = FakeRestBroker()
    result = ensure_data_available(
        session_factory, broker, SYMBOL, SECURITY_ID, SEG, "1day", _dt(2026, 1, 1), _dt(2026, 1, 15),
    )

    assert result["already_covered"] is False
    assert len(broker.calls) == 1
    assert broker.calls[0][4] == _dt(2026, 1, 1)
    assert broker.calls[0][5] == _dt(2026, 1, 9)  # up to the day before the covered range


def test_requesting_a_disjoint_range_replaces_coverage_rather_than_merging(session_factory):
    """The bug found via live verification: having RECENT coverage must not
    make a request for an OLDER, non-overlapping range look "already
    covered" (that range was never actually fetched) — and since only one
    interval is tracked, the fix REPLACES coverage with the new range
    rather than pretending the untouched gap between them is covered too."""
    _seed_coverage(session_factory, _dt(2026, 9, 1), _dt(2026, 9, 10))

    broker = FakeRestBroker()
    result = ensure_data_available(
        session_factory, broker, SYMBOL, SECURITY_ID, SEG, "1day", _dt(2026, 7, 16), _dt(2026, 7, 26),
    )

    assert result["already_covered"] is False
    assert len(broker.calls) == 1
    assert broker.calls[0][4] == _dt(2026, 7, 16)  # fetched the whole requested range, not "nothing to do"
    assert broker.calls[0][5] == _dt(2026, 7, 26)

    with session_factory() as session:
        coverage = LibCandlesHistorical.get_coverage(session, SYMBOL, SEG, "1day")
    assert coverage == _naive(_dt(2026, 7, 16), _dt(2026, 7, 26))  # replaced, not merged with September

    # re-requesting the now-abandoned September range safely re-fetches
    # rather than silently reporting the untouched July-August gap as covered
    result2 = ensure_data_available(
        session_factory, broker, SYMBOL, SECURITY_ID, SEG, "1day", _dt(2026, 9, 1), _dt(2026, 9, 10),
    )
    assert result2["already_covered"] is False


def test_persists_fetched_candles(session_factory):
    broker = FakeRestBroker(candles_per_call=3)
    ensure_data_available(
        session_factory, broker, SYMBOL, SECURITY_ID, SEG, "1min", _dt(2026, 1, 1), _dt(2026, 1, 1),
    )

    with session_factory() as session:
        rows = session.query(CandleHistorical).filter_by(symbol=SYMBOL, timeframe="1min").all()
    assert len(rows) == 3


def test_chunks_an_intraday_range_spanning_more_than_one_chunk(session_factory):
    broker = FakeRestBroker()
    start = _dt(2026, 1, 1)
    end = start + timedelta(days=200)  # > the 90-day intraday chunk size

    result = ensure_data_available(session_factory, broker, SYMBOL, SECURITY_ID, SEG, "1min", start, end)

    assert len(broker.calls) == 3
    assert broker.calls[0][4] == start  # first chunk starts at start_date
    assert broker.calls[-1][5] == end  # last chunk ends at end_date exactly
    # consecutive chunks are contiguous, no gap or overlap
    assert broker.calls[1][4] == broker.calls[0][5] + timedelta(days=1)
    assert broker.calls[2][4] == broker.calls[1][5] + timedelta(days=1)
    assert result["fetched"] == 3


def test_chunks_a_daily_range_spanning_more_than_one_chunk(session_factory):
    broker = FakeRestBroker()
    start = _dt(2020, 1, 1)
    end = start + timedelta(days=800)  # > the 365-day daily chunk size

    result = ensure_data_available(session_factory, broker, SYMBOL, SECURITY_ID, SEG, "1day", start, end)

    assert len(broker.calls) == 3
    assert broker.calls[0][4] == start
    assert broker.calls[-1][5] == end
    assert result["fetched"] == 3
