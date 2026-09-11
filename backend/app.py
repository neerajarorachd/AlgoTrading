from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from flask import Flask, g
from flask_cors import CORS

from activity_engine import ActivityEngine, seed_pattern_definitions
from config import DHAN_TOKEN_TYPE_FEED, DHAN_TOKEN_TYPE_REST, cors_origins, load_dhan_tokens
from db.models import Base
from db.session import build_engine, build_session_factory
from feed.candle_aggregator import CandleAggregator
from feed.candle_persistence import persist_candle
from instrument_master import InstrumentMaster
from market_feed import MarketFeed

import api.ws_live as ws_live


def create_app(broker=None, engine=None, session_factory=None, instrument_master=None, testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.testing = testing
    CORS(app, origins=cors_origins())

    engine = build_engine(engine=engine)
    session_factory = session_factory or build_session_factory(engine)
    Base.metadata.create_all(engine)

    if broker is not None:
        # test/injected broker — same instance covers both duties, matching the
        # existing FakeBroker-based test suite unchanged
        feed_broker, rest_broker = broker, broker
    else:
        feed_broker, rest_broker = _build_dhan_brokers(session_factory)

    instrument_master = instrument_master or InstrumentMaster()

    seed_pattern_definitions(session_factory)
    activity_engine = ActivityEngine(session_factory)
    aggregator = CandleAggregator(on_candle_closed=_make_on_candle_closed(session_factory, activity_engine))
    market_feed = MarketFeed(
        feed_broker,
        on_tick=ws_live.broadcast_tick,
        on_depth=ws_live.broadcast_depth,
        on_candle_tick=_make_on_candle_tick(aggregator),
        candle_lookup=aggregator.get_last_closed_1min,
    )

    app.extensions["db_session_factory"] = session_factory
    app.extensions["broker"] = rest_broker  # REST routes (get_quote, etc.) use this
    app.extensions["feed_broker"] = feed_broker
    app.extensions["market_feed"] = market_feed
    app.extensions["candle_aggregator"] = aggregator
    app.extensions["instrument_master"] = instrument_master
    app.extensions["activity_engine"] = activity_engine

    _register_db_session_hooks(app, session_factory)

    from api.routes_candles import candles_bp
    from api.routes_symbols import symbols_bp
    app.register_blueprint(symbols_bp)
    app.register_blueprint(candles_bp)

    app.extensions["socketio"] = ws_live.init_app(app)

    if not app.testing:
        from feed.bootstrap import start_feed
        start_feed(feed_broker, market_feed, session_factory, aggregator, rest_broker=rest_broker)

    return app


def _build_dhan_brokers(session_factory):
    """One DhanBroker for the live feed (FIXED2), one for REST calls (FIXED3) —
    each gets its own token so they never contend for the same rate limit.
    Falls back to the single .env token for whichever (or both) aren't yet
    mirrored into SQL Server, so this is never a hard startup dependency.
    """
    from brokers.dhan_broker import DhanBroker

    tokens = {}
    try:
        with session_factory() as session:
            tokens = load_dhan_tokens(session)
    except Exception:
        tokens = {}

    client_id = os.environ.get("DHAN_CLIENT_ID", "")
    fallback_token = os.environ.get("DHAN_ACCESS_TOKEN", "")
    feed_token = tokens.get(DHAN_TOKEN_TYPE_FEED, fallback_token)
    rest_token = tokens.get(DHAN_TOKEN_TYPE_REST, fallback_token)

    feed_broker = DhanBroker(client_id=client_id, access_token=feed_token)
    rest_broker = DhanBroker(client_id=client_id, access_token=rest_token)
    return feed_broker, rest_broker


def _make_on_candle_closed(session_factory, activity_engine: ActivityEngine):
    def _on_candle_closed(symbol: str, exchange_segment: str, candle) -> None:
        persist_candle(session_factory, symbol, exchange_segment, candle)
        activity_engine.on_candle_closed(symbol, exchange_segment, candle)
        ws_live.broadcast_candle_closed(symbol, exchange_segment, candle)
    return _on_candle_closed


def _make_on_candle_tick(aggregator: CandleAggregator):
    def _on_candle_tick(instrument: dict, tick: dict) -> None:
        aggregator.on_tick(
            instrument["symbol"], instrument["exchange_segment"],
            tick["ltp"], tick["volume"],
            datetime.fromisoformat(tick["ts"].replace("Z", "+00:00")),
        )
    return _on_candle_tick


def _register_db_session_hooks(app: Flask, session_factory) -> None:
    @app.before_request
    def _open_db_session():
        g.db_session = session_factory()

    @app.teardown_appcontext
    def _close_db_session(exc: Optional[BaseException]):
        session = g.pop("db_session", None)
        if session is not None:
            if exc is not None:
                session.rollback()
            else:
                session.commit()
            session.close()
