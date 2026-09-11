from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from db.models import BrokerAccount, BrokerToken, CandleToday, SubscribedSymbol
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


def test_broker_account_and_token_mirror_round_trip(session_factory):
    with session_scope(session_factory) as session:
        session.add(BrokerAccount(
            AccountID="DHAN_NEERAJ", Broker="DHAN", ClientID="1106451789",
        ))
        session.add(BrokerToken(
            TokenID=2, AccountID="DHAN_NEERAJ", TokenType=2,
            AccessToken="fake-fixed2-token",
        ))
        session.add(BrokerToken(
            TokenID=3, AccountID="DHAN_NEERAJ", TokenType=3,
            AccessToken="fake-fixed3-token",
        ))

    with session_scope(session_factory) as session:
        account = session.query(BrokerAccount).filter_by(AccountID="DHAN_NEERAJ").one()
        assert account.ClientID == "1106451789"
        assert account.IsActive is True

        fixed2 = session.query(BrokerToken).filter_by(TokenID=2).one()
        assert fixed2.TokenType == 2
        assert fixed2.AccessToken == "fake-fixed2-token"

        fixed3 = session.query(BrokerToken).filter_by(TokenID=3).one()
        assert fixed3.TokenType == 3


def test_broker_token_id_is_not_independently_generated(session_factory):
    """TokenID must mirror Trading's own value exactly, never autoincrement —
    otherwise a mirrored row's identity could drift from its source row."""
    with session_scope(session_factory) as session:
        session.add(BrokerAccount(AccountID="DHAN_NEERAJ", Broker="DHAN", ClientID="1106451789"))
        session.add(BrokerToken(TokenID=5, AccountID="DHAN_NEERAJ", TokenType=4, AccessToken="t"))

    with session_scope(session_factory) as session:
        row = session.query(BrokerToken).one()
        assert row.TokenID == 5  # explicitly set, not reassigned to 1
