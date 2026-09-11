from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event

from app import create_app
from brokers.models import BrokerAPIError, Quote
from db.models import Base
from db.session import build_session_factory


@pytest.fixture
def db_engine():
    engine = create_engine("sqlite:///:memory:")

    # pysqlite's own implicit transaction handling fights SQLAlchemy's
    # SAVEPOINT support (session.begin_nested(), used by
    # feed/candle_persistence.py to isolate one candle's insert-conflict from
    # the rest of a batch) unless disabled like this — the standard
    # documented workaround. Production never runs on SQLite (real DB is SQL
    # Server, which has no such quirk), so this only matters for tests.
    @event.listens_for(engine, "connect")
    def _do_connect(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _do_begin(conn):
        conn.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    return build_session_factory(db_engine)


class FakeBroker:
    """Shared fake broker for API-level tests — no real network/WS involved."""

    def __init__(self):
        self.connected = False
        self.subscribed_calls = []
        self.unsubscribed_calls = []
        self.callback = None
        self.quotes = {}  # security_id -> Quote, populate per-test as needed

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def subscribe_feed(self, instruments, on_tick):
        self.subscribed_calls.append(instruments)
        self.callback = on_tick

    def unsubscribe_feed(self, instruments):
        self.unsubscribed_calls.append(instruments)

    def get_quote(self, symbol, security_id, exchange_segment):
        if symbol == "BROKER-DOWN":
            raise BrokerAPIError("simulated broker outage")
        if security_id in self.quotes:
            return self.quotes[security_id]
        return Quote(
            symbol=symbol, ltp=100.0, open=99.0, high=101.0, low=98.0, close=97.5,
            volume=0, timestamp=datetime.now(timezone.utc),
        )


class FakeInstrumentMaster:
    """Resolves any symbol deterministically, no CSV/network involved."""

    def resolve(self, symbol, exchange="NSE", segment="EQUITY"):
        if symbol == "UNKNOWN":
            raise LookupError(f"Dhan instrument not found: {exchange}:{symbol} ({segment})")
        return {
            "symbol": symbol,
            "exchange": exchange,
            "segment": segment,
            "exchange_segment": f"{exchange}_EQ",
            "security_id": f"SEC-{symbol}",
            "raw": {},
        }

    def search(self, query, exchange="NSE", segment="EQUITY", limit=15):
        if not query:
            return []
        return [{
            "symbol": query.upper(), "custom_symbol": None, "company_name": None,
            "exchange": exchange, "segment": segment,
            "exchange_segment": f"{exchange}_EQ", "security_id": f"SEC-{query.upper()}",
        }]


@pytest.fixture
def fake_broker():
    return FakeBroker()


@pytest.fixture
def app(db_engine, session_factory, fake_broker, monkeypatch):
    # Real backfill spawns a background thread that outlives most tests (this
    # fixture's own db_engine gets disposed right after the test returns) —
    # default it to a no-op here so every test doesn't race a background
    # thread against teardown; feed/test_gap_fill.py tests the real function
    # directly and unpatched.
    monkeypatch.setattr("api.routes_symbols.spawn_backfill", lambda *a, **kw: None)
    monkeypatch.setattr("feed.bootstrap._backfill_all", lambda *a, **kw: None)

    flask_app = create_app(
        broker=fake_broker, engine=db_engine, session_factory=session_factory,
        instrument_master=FakeInstrumentMaster(), testing=True,
    )
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()
