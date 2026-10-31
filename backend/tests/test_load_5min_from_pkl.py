from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from db.models import Base, CandleHistorical
from db.ops import LibCandlesHistorical as ops
from db.session import build_session_factory, session_scope
from scripts import load_5min_from_pkl as L

T0 = 1_760_000_000  # any epoch, multiple of nothing in particular


@pytest.fixture
def sf():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def _rows(n, start=T0):
    return [(start + 300 * i, 10.0, 11.0, 9.0, 10.5, 100 + i) for i in range(n)]


def _count(sf):
    with session_scope(sf) as s:
        return s.query(CandleHistorical).count()


def test_inserts_and_sets_coverage(sf):
    r = L.load_symbol(sf, "X", "NSE_EQ", _rows(5))
    assert r["inserted"] == 5 and _count(sf) == 5
    with session_scope(sf) as s:
        cov = ops.get_coverage(s, "X", "NSE_EQ", "5min")
    assert cov is not None


def test_rerun_inserts_nothing_and_existing_rows_untouched(sf):
    L.load_symbol(sf, "X", "NSE_EQ", _rows(5))
    changed = [(T0, 99.0, 99.0, 99.0, 99.0, 1)] + _rows(5)[1:]
    r = L.load_symbol(sf, "X", "NSE_EQ", changed)
    assert r["inserted"] == 0 and _count(sf) == 5
    with session_scope(sf) as s:
        first = s.query(CandleHistorical).order_by(CandleHistorical.ts).first()
        assert float(first.open_price) == 10.0


def test_overlapping_load_adds_only_missing(sf):
    L.load_symbol(sf, "X", "NSE_EQ", _rows(5))
    r = L.load_symbol(sf, "X", "NSE_EQ", _rows(8))
    assert r["inserted"] == 3 and _count(sf) == 8
