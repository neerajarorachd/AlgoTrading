from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from db.models import CandleToday, SubscribedSymbol
from db.session import session_scope


def test_subscribed_symbol_round_trip(session_factory):
    with session_scope(session_factory) as session:
        session.add(SubscribedSymbol(
            symbol="RELIANCE", exchange="NSE", segment="EQUITY",
            exchange_segment="NSE_EQ", security_id="1333", previous_close=2828.4,
        ))

    with session_scope(session_factory) as session:
        row = session.query(SubscribedSymbol).filter_by(symbol="RELIANCE").one()
        assert row.exchange_segment == "NSE_EQ"
        assert row.security_id == "1333"
        assert float(row.previous_close) == 2828.4
        assert row.active is True
        assert row.removed_at is None


def test_subscribed_symbol_unique_constraint(session_factory):
    with session_scope(session_factory) as session:
        session.add(SubscribedSymbol(
            symbol="TCS", exchange="NSE", segment="EQUITY",
            exchange_segment="NSE_EQ", security_id="11536",
        ))

    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as session:
            session.add(SubscribedSymbol(
                symbol="TCS", exchange="NSE", segment="EQUITY",
                exchange_segment="NSE_EQ", security_id="11536",
            ))


def test_candle_today_round_trip_open_close_columns(session_factory):
    ts = datetime(2026, 9, 10, 9, 15, tzinfo=timezone.utc)
    with session_scope(session_factory) as session:
        session.add(CandleToday(
            symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1min", ts=ts,
            open_price=2850.0, high_price=2855.0, low_price=2848.0, close_price=2854.65,
            volume=1200,
        ))

    with session_scope(session_factory) as session:
        row = session.query(CandleToday).filter_by(symbol="RELIANCE", timeframe="1min").one()
        assert float(row.open_price) == 2850.0
        assert float(row.close_price) == 2854.65
        assert row.volume == 1200


def test_candle_today_unique_constraint(session_factory):
    ts = datetime(2026, 9, 10, 9, 15, tzinfo=timezone.utc)
    with session_scope(session_factory) as session:
        session.add(CandleToday(
            symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1min", ts=ts,
            open_price=2850.0, high_price=2855.0, low_price=2848.0, close_price=2854.65,
            volume=1200,
        ))

    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as session:
            session.add(CandleToday(
                symbol="RELIANCE", exchange_segment="NSE_EQ", timeframe="1min", ts=ts,
                open_price=2851.0, high_price=2856.0, low_price=2849.0, close_price=2853.0,
                volume=500,
            ))
