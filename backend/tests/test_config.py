from config import DHAN_TOKEN_TYPE_FEED, DHAN_TOKEN_TYPE_REST, load_dhan_tokens
from db.models import BrokerToken


def test_load_dhan_tokens_returns_feed_and_rest(session_factory):
    with session_factory() as session:
        session.add(BrokerToken(TokenID=2, AccountID="DHAN_NEERAJ", TokenType=2, AccessToken="feed-token"))
        session.add(BrokerToken(TokenID=3, AccountID="DHAN_NEERAJ", TokenType=3, AccessToken="rest-token"))
        session.commit()

    with session_factory() as session:
        tokens = load_dhan_tokens(session)

    assert tokens == {
        DHAN_TOKEN_TYPE_FEED: "feed-token",
        DHAN_TOKEN_TYPE_REST: "rest-token",
    }


def test_load_dhan_tokens_ignores_other_token_types_and_accounts(session_factory):
    with session_factory() as session:
        session.add(BrokerToken(TokenID=1, AccountID="DHAN_NEERAJ", TokenType=0, AccessToken="general"))
        session.add(BrokerToken(TokenID=2, AccountID="DHAN_NEERAJ", TokenType=2, AccessToken="feed-token"))
        session.add(BrokerToken(TokenID=6, AccountID="OTHER_ACCOUNT", TokenType=2, AccessToken="other-account"))
        session.commit()

    with session_factory() as session:
        tokens = load_dhan_tokens(session)

    assert tokens == {DHAN_TOKEN_TYPE_FEED: "feed-token"}


def test_load_dhan_tokens_ignores_inactive_rows(session_factory):
    with session_factory() as session:
        session.add(BrokerToken(TokenID=2, AccountID="DHAN_NEERAJ", TokenType=2, AccessToken="stale", IsActive=False))
        session.commit()

    with session_factory() as session:
        tokens = load_dhan_tokens(session)

    assert tokens == {}


def test_load_dhan_tokens_empty_when_nothing_mirrored_yet(session_factory):
    with session_factory() as session:
        tokens = load_dhan_tokens(session)

    assert tokens == {}


def test_build_dhan_brokers_uses_mirrored_tokens_when_present(session_factory, monkeypatch):
    from app import _build_dhan_brokers

    monkeypatch.setenv("DHAN_CLIENT_ID", "1106451789")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "fallback-token")

    with session_factory() as session:
        session.add(BrokerToken(TokenID=2, AccountID="DHAN_NEERAJ", TokenType=2, AccessToken="feed-token"))
        session.add(BrokerToken(TokenID=3, AccountID="DHAN_NEERAJ", TokenType=3, AccessToken="rest-token"))
        session.commit()

    feed_broker, rest_broker = _build_dhan_brokers(session_factory)

    assert feed_broker.access_token == "feed-token"
    assert rest_broker.access_token == "rest-token"
    assert feed_broker is not rest_broker  # two distinct instances, never share a rate limit


def test_build_dhan_brokers_falls_back_to_env_when_nothing_mirrored(session_factory, monkeypatch):
    from app import _build_dhan_brokers

    monkeypatch.setenv("DHAN_CLIENT_ID", "1106451789")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "fallback-token")

    feed_broker, rest_broker = _build_dhan_brokers(session_factory)

    assert feed_broker.access_token == "fallback-token"
    assert rest_broker.access_token == "fallback-token"
