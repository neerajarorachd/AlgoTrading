from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, current_app, g, jsonify, request

from brokers.models import BrokerAPIError, BrokerConnectionError
from db.models import SubscribedSymbol
from feed.gap_fill import spawn_backfill

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
    rows = (
        g.db_session.query(SubscribedSymbol)
        .filter_by(active=True)
        .order_by(SubscribedSymbol.symbol)
        .all()
    )
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

    row = (
        g.db_session.query(SubscribedSymbol)
        .filter_by(symbol=symbol, exchange=exchange, segment=segment)
        .one_or_none()
    )

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
        row = SubscribedSymbol(
            symbol=resolved["symbol"], exchange=exchange, segment=segment,
            exchange_segment=resolved["exchange_segment"], security_id=resolved["security_id"],
            previous_close=quote.close,
        )
        g.db_session.add(row)
        status = 201

    g.db_session.flush()  # populate row.id before it's serialized into the response

    market_feed.subscribe({
        "security_id": row.security_id,
        "exchange_segment": row.exchange_segment,
        "symbol": row.symbol,
        "exchange": row.exchange,
        "segment": row.segment,
        "previous_close": float(row.previous_close) if row.previous_close is not None else None,
        "open": float(quote.open),
        "ltp": float(quote.ltp),
    })

    spawn_backfill(
        row.symbol, row.exchange_segment, row.security_id, broker,
        current_app.extensions["db_session_factory"], current_app.extensions["candle_aggregator"],
    )

    return jsonify(_serialize(row)), status


@symbols_bp.delete("/api/symbols/<int:symbol_id>")
def remove_symbol(symbol_id):
    row = g.db_session.get(SubscribedSymbol, symbol_id)
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
