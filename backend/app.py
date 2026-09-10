from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from flask import Flask, g
from flask_cors import CORS

from config import cors_origins
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

    if broker is None:
        from brokers.dhan_broker import DhanBroker
        broker = DhanBroker(
            client_id=os.environ.get("DHAN_CLIENT_ID", ""),
            access_token=os.environ.get("DHAN_ACCESS_TOKEN", ""),
        )

    engine = build_engine(engine=engine)
    session_factory = session_factory or build_session_factory(engine)
    Base.metadata.create_all(engine)

    instrument_master = instrument_master or InstrumentMaster()

    aggregator = CandleAggregator(on_candle_closed=_make_on_candle_closed(session_factory))
    market_feed = MarketFeed(
        broker,
        on_tick=ws_live.broadcast_tick,
        on_depth=ws_live.broadcast_depth,
        on_candle_tick=_make_on_candle_tick(aggregator),
    )

    app.extensions["db_session_factory"] = session_factory
    app.extensions["broker"] = broker
    app.extensions["market_feed"] = market_feed
    app.extensions["candle_aggregator"] = aggregator
    app.extensions["instrument_master"] = instrument_master

    _register_db_session_hooks(app, session_factory)

    from api.routes_candles import candles_bp
    from api.routes_symbols import symbols_bp
    app.register_blueprint(symbols_bp)
    app.register_blueprint(candles_bp)

    app.extensions["socketio"] = ws_live.init_app(app)

    if not app.testing:
        from feed.bootstrap import start_feed
        start_feed(broker, market_feed, session_factory, aggregator)

    return app


def _make_on_candle_closed(session_factory):
    def _on_candle_closed(symbol: str, exchange_segment: str, candle) -> None:
        persist_candle(session_factory, symbol, exchange_segment, candle)
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
