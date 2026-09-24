import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import QueuePool

from app import create_app
from brokers.models import BrokerAPIError, Quote
from db.models import Base
from db.session import build_session_factory
from feed.gap_fill import backfill_then_subscribe


@pytest.fixture
def wait_until():
    """POST /api/symbols now backfills-then-subscribes on a real background
    thread (see feed/gap_fill.py's backfill_then_subscribe) instead of
    subscribing synchronously — tests that need to observe the resulting
    subscribe_feed call poll for it instead of asserting immediately after
    the request returns."""
    def _wait(predicate, timeout=2.0, interval=0.01):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()
    return _wait


@pytest.fixture
def db_engine():
    # Shared-cache URI, not plain ":memory:": a route handler's own
    # g.db_session transaction is still open (uncommitted) while its request
    # runs, and backfill_then_subscribe (called synchronously within that
    # same request — see the app fixture below) opens its OWN session at
    # the same time. Plain ":memory:" defaults to SingletonThreadPool (one
    # shared connection per thread) — and SQLAlchemy picks that same pool
    # for THIS url too by default (verified directly: `mode=memory` in the
    # query string is enough to trigger it, shared-cache or not), so every
    # session on this thread was still handed the exact same DBAPI
    # connection, hitting "cannot start a transaction within a transaction"
    # the moment a second session tried to BEGIN while the first's was still
    # open.
    #
    # poolclass=QueuePool (explicit — NullPool was tried first and made
    # things worse: it closes each connection immediately after use, and an
    # in-memory shared-cache SQLite DB is garbage-collected the moment zero
    # connections reference it, so the schema/data vanished between uses).
    # QueuePool keeps a real pool of open connections, handing out a
    # genuinely different one for a concurrent/nested checkout while never
    # letting the connection count hit zero — cache=shared is what keeps all
    # of those separate connections seeing the same in-memory data —
    # separate transactions, same data, matching how production's real
    # connection pool (SQL Server) behaves.
    #
    # A unique DB name per fixture invocation matters here, not just
    # ":memory:" — shared-cache in-memory SQLite DBs are identified by name
    # and stay alive as long as ANY connection to that name exists anywhere
    # in the process; reusing the same bare name across test runs would risk
    # leaking a previous test's rows into a "fresh" fixture.
    db_name = f"test_{uuid.uuid4().hex}"
    engine = create_engine(
        f"sqlite:///file:{db_name}?mode=memory&cache=shared&uri=true",
        connect_args={"check_same_thread": False}, poolclass=QueuePool, pool_size=5,
    )

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
        self.historical_candles = []  # populate per-test; returned by every get_historical_data call
        self.historical_calls = []

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

    def get_historical_data(self, symbol, security_id, exchange_segment, timeframe, from_date, to_date):
        self.historical_calls.append((symbol, security_id, exchange_segment, timeframe, from_date, to_date))
        return self.historical_candles


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
    # spawn_backfill_then_subscribe normally spawns a real background thread
    # (production behavior) — deliberately NOT done here. A daemon thread
    # left running past a test's own teardown, touching SQLite (not
    # thread-safe at the C level for every access pattern even with
    # check_same_thread=False) right as the *next* test's db_engine fixture
    # is being created/disposed, produced a genuine interpreter crash
    # (not a catchable exception) roughly 1 run in 8 — a real flake risk
    # worth avoiding in the shared fixture rather than chasing further.
    # Running backfill_then_subscribe directly instead (no thread) exercises
    # the exact same logic synchronously; db_engine's shared-cache setup
    # already gives it a genuinely separate connection from the request's
    # own g.db_session (which is what actually fixed the original "cannot
    # start a transaction within a transaction" error — not the threading).
    monkeypatch.setattr("api.routes_symbols.spawn_backfill_then_subscribe", backfill_then_subscribe)
    # Same fix, same reason, for routes_watchlists.py's own separately-imported
    # reference (added 2026-09-16 for POST /api/watchlists/<id>/subscribe) —
    # each module's `from feed.gap_fill import spawn_backfill_then_subscribe`
    # binds its own local name, so patching routes_symbols's alone doesn't
    # cover this one too.
    monkeypatch.setattr("api.routes_watchlists.spawn_backfill_then_subscribe", backfill_then_subscribe)

    # _hydrate/_backfill_and_subscribe_all never actually run in any test
    # using this fixture (testing=True skips start_feed entirely in
    # create_app), so nothing to patch there; feed/test_bootstrap.py's own
    # aggregator tests call _hydrate directly, unpatched.

    flask_app = create_app(
        broker=fake_broker, engine=db_engine, session_factory=session_factory,
        instrument_master=FakeInstrumentMaster(), testing=True,
    )
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()
