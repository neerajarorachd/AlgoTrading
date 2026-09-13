from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request

from brokers.models import BrokerAPIError, BrokerConnectionError
from db.models import SubscribedSymbol
from db.ops import LibSymbols as ops_symbols
from feed.gap_fill import spawn_backfill_then_subscribe

symbols_bp = Blueprint("symbols", __name__)


def _serialize(row: SubscribedSymbol) -> dict:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "exchange": row.exchange,
        "segment": row.segment,
        "exchange_segment": row.exchange_segment,
        "security_id": row.security_id,
        "previous_close": float(row.previous_close) if row.previous_close is not None else None,
        "active": row.active,
    }


@symbols_bp.get("/api/instruments/search")
def search_instruments():
    query = request.args.get("q", "")
    exchange = request.args.get("exchange", "NSE")
    segment = request.args.get("segment", "EQUITY")
    instrument_master = current_app.extensions["instrument_master"]

    results = instrument_master.search(query, exchange, segment)
    return jsonify(results)


@symbols_bp.get("/api/symbols")
def list_symbols():
    rows = ops_symbols.get_active(g.db_session)
    return jsonify([_serialize(row) for row in rows])


@symbols_bp.post("/api/symbols")
def add_symbol():
    body = request.get_json(silent=True) or {}
    symbol = body.get("symbol")
    exchange = body.get("exchange", "NSE")
    segment = body.get("segment", "EQUITY")
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    instrument_master = current_app.extensions["instrument_master"]
    broker = current_app.extensions["broker"]
    market_feed = current_app.extensions["market_feed"]

    row = ops_symbols.get_by_natural_key(g.db_session, symbol, exchange, segment)

    if row is not None and row.active:
        # idempotent: already live, no duplicate subscribe
        return jsonify(_serialize(row)), 200

    try:
        resolved = instrument_master.resolve(symbol, exchange, segment)
    except LookupError:
        return jsonify({"error": f"Unknown instrument: {exchange}:{symbol} ({segment})"}), 404

    try:
        quote = broker.get_quote(resolved["symbol"], resolved["security_id"], resolved["exchange_segment"])
    except (BrokerAPIError, BrokerConnectionError) as exc:
        return jsonify({"error": f"Broker quote lookup failed: {exc}"}), 502

    if row is not None:
        # reactivate a previously-unregistered symbol rather than violating the
        # (symbol, exchange, segment) unique constraint with a fresh insert
        row.active = True
        row.removed_at = None
        row.exchange_segment = resolved["exchange_segment"]
        row.security_id = resolved["security_id"]
        row.previous_close = quote.close
        status = 200
    else:
        row = ops_symbols.create(
            g.db_session,
            symbol=resolved["symbol"], exchange=exchange, segment=segment,
            exchange_segment=resolved["exchange_segment"], security_id=resolved["security_id"],
            previous_close=quote.close,
        )
        status = 201

    g.db_session.flush()  # populate row.id before it's serialized into the response

    # backfill first, THEN subscribe to the live feed — not the other way
    # around, see backfill_then_subscribe's docstring. The row is already
    # visible via GET /api/symbols at this point (added/reactivated above),
    # so the frontend can show it immediately with a spinner in the ticker
    # cell until the first live tick actually arrives.
    spawn_backfill_then_subscribe(
        {
            "security_id": row.security_id,
            "exchange_segment": row.exchange_segment,
            "symbol": row.symbol,
            "exchange": row.exchange,
            "segment": row.segment,
            "previous_close": float(row.previous_close) if row.previous_close is not None else None,
            "open": float(quote.open),
            "ltp": float(quote.ltp),
        },
        broker, current_app.extensions["db_session_factory"],
        current_app.extensions["candle_aggregator"], market_feed,
    )

    return jsonify(_serialize(row)), status


@symbols_bp.delete("/api/symbols/<int:symbol_id>")
def remove_symbol(symbol_id):
    row = ops_symbols.get_by_id(g.db_session, symbol_id)
    if row is None:
        return "", 404
    if not row.active:
        # idempotent: already removed
        return "", 204

    market_feed = current_app.extensions["market_feed"]
    row.active = False
    row.removed_at = datetime.now(timezone.utc)
    g.db_session.flush()

    market_feed.unsubscribe({
        "security_id": row.security_id,
        "exchange_segment": row.exchange_segment,
        "symbol": row.symbol,
    })

    return "", 204
