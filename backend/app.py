from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, g, send_from_directory
from flask_cors import CORS

logger = logging.getLogger(__name__)

from activity_engine import PATTERN_CATALOG, ActivityEngine, seed_pattern_definitions
from config import DHAN_TOKEN_TYPE_FEED, DHAN_TOKEN_TYPE_REST, cors_origins, load_dhan_tokens
from db.models import Base, Recommendation
from db.ops.LibCandles import persist_one as persist_candle
from db.ops.LibRecommendationSystems import ensure_parent_strategies
from db.ops.LibRecommendationSystems import seed_defaults as seed_recommendation_systems
from db.ops.LibStrategyElements import seed_strategy_elements
from db.ops.LibSystemSettings import seed_defaults as seed_system_settings
from db.session import build_engine, build_session_factory
from feed.candle_aggregator import CandleAggregator
from pattern_defaults import ensure_fallback_defaults
from instrument_master import InstrumentMaster
from market_feed import MarketFeed
from recommendation_engine import RECOMMENDATION_SYSTEM_SEED, SYSTEM_SETTING_SEED
from recommendation_engine import on_activities as recommendation_on_activities

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
    seed_strategy_elements(session_factory, PATTERN_CATALOG)
    seed_system_settings(session_factory, SYSTEM_SETTING_SEED)
    seed_recommendation_systems(session_factory, RECOMMENDATION_SYSTEM_SEED)
    ensure_parent_strategies(session_factory)
    ensure_fallback_defaults(session_factory)
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

    from api.routes_activities import activities_bp
    from api.routes_backtests import backtests_bp
    from api.routes_candles import candles_bp
    from api.routes_engine_settings import engine_settings_bp
    from api.routes_historical_data import historical_data_bp
    from api.routes_recommendation_systems import recommendation_systems_bp
    from api.routes_recommendations import recommendations_bp
    from api.routes_strategies import strategies_bp
    from api.routes_symbols import symbols_bp
    from api.routes_watch_scores import watch_scores_bp
    from api.routes_watch_selection import watch_selection_bp
    from api.routes_watchlists import watchlists_bp
    app.register_blueprint(symbols_bp)
    app.register_blueprint(candles_bp)
    app.register_blueprint(engine_settings_bp)
    app.register_blueprint(strategies_bp)
    app.register_blueprint(watchlists_bp)
    app.register_blueprint(watch_selection_bp)
    app.register_blueprint(watch_scores_bp)
    app.register_blueprint(historical_data_bp)
    app.register_blueprint(activities_bp)
    app.register_blueprint(backtests_bp)
    app.register_blueprint(recommendations_bp)
    app.register_blueprint(recommendation_systems_bp)

    app.extensions["socketio"] = ws_live.init_app(app)

    if not app.testing:
        from feed.bootstrap import start_feed
        start_feed(
            feed_broker, market_feed, session_factory, aggregator,
            rest_broker=rest_broker, activity_engine=activity_engine,
        )

        from scenario_scheduler import start_guiding_scenario_scheduler
        start_guiding_scenario_scheduler(session_factory)

        from recommendation_outcomes import start_outcome_scanner
        start_outcome_scanner(session_factory)

    _register_frontend_static(app)

    return app


def _register_frontend_static(app: Flask) -> None:
    """Serves the built React frontend directly from this Flask app when
    one is present — added 2026-09-16 for the VM deployment, so viewing the
    app needs only the one SSH tunnel already used for everything else here
    (SQL Server, this backend), not a separately-running local Vite dev
    server. A no-op for local dev: `frontend_dist/` is a deploy-time-only
    copy (see ops/vm_deploy/deploy.md), never created by `npm run dev`
    (which serves the frontend itself and proxies to this backend instead —
    see frontend/vite.config.js) — so this route simply doesn't match
    anything locally. Registered last so it never shadows the /api/* and
    /socket.io routes already registered above (Werkzeug matches the most
    specific rule regardless of registration order, but last keeps intent
    obvious to a reader)."""
    dist_dir = Path(__file__).resolve().parent / "frontend_dist"
    if not dist_dir.is_dir():
        return

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def _serve_frontend(path):
        target = dist_dir / path if path else None
        if target is not None and target.is_file():
            return send_from_directory(dist_dir, path)
        # React Router's client-side routes (e.g. /recommendations) aren't
        # real files — always fall back to index.html so the SPA's own
        # router can take over, matching standard SPA-hosting convention.
        return send_from_directory(dist_dir, "index.html")


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
        new_activities = activity_engine.on_candle_closed(symbol, exchange_segment, candle)
        # First real live consumer of on_candle_closed's return value
        # (2026-09-16) — recommendation_engine.on_activities is guarded
        # with its own try/except internally, but a second belt-and-
        # suspenders guard here ensures nothing about wiring a NEW live
        # subsystem in for the first time can ever break candle persistence
        # or the WS broadcast below, which existed and worked before this.
        try:
            queued = recommendation_on_activities(
                session_factory, symbol, exchange_segment, new_activities,
                rule_rows_provider=activity_engine.recent_rows,
            )
        except Exception:
            logger.exception("recommendation_engine.on_activities failed for %s (%s)", symbol, exchange_segment)
            queued = []
        try:
            _broadcast_new_recommendations(session_factory, queued)
        except Exception:
            logger.exception("broadcasting new recommendations failed for %s (%s)", symbol, exchange_segment)
        ws_live.broadcast_candle_closed(symbol, exchange_segment, candle)
    return _on_candle_closed


def _broadcast_new_recommendations(session_factory, queued: list) -> None:
    """Fetches and serializes each just-queued Recommendation row (reusing
    routes_recommendations._serialize, the same shape GET /api/recommendations/
    pending already returns — one source of truth) and pushes it to the
    "recommendations" WebSocket room. Lives here, not in recommendation_
    engine.py, deliberately — that module has no Flask/SocketIO dependency,
    this is the one place with real DB session access outside a request
    context AND the ws_live import already in scope."""
    if not queued:
        return
    from api.routes_recommendations import _serialize
    with session_factory() as session:
        for result in queued:
            row = session.get(Recommendation, result["id"])
            if row is not None:
                ws_live.broadcast_recommendation_created(_serialize(row))


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
