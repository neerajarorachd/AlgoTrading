from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from brokers.models import BrokerAPIError, BrokerConnectionError
from db.models import SubscribedSymbol, Watchlist, WatchlistInstrument
from db.ops import LibWatchlists as ops_watchlists
from feed.gap_fill import spawn_backfill_then_subscribe

watchlists_bp = Blueprint("watchlists", __name__)


def _serialize(row: Watchlist, member_count: int | None = None) -> dict:
    payload = {
        "id": row.id, "name": row.name, "description": row.description, "active": row.active,
    }
    if member_count is not None:
        payload["member_count"] = member_count
    return payload


def _serialize_member(membership: WatchlistInstrument, symbol_row: SubscribedSymbol) -> dict:
    return {
        "instrument_id": symbol_row.id,
        "symbol": symbol_row.symbol,
        "exchange": symbol_row.exchange,
        "segment": symbol_row.segment,
        "exchange_segment": symbol_row.exchange_segment,
        "added_at": membership.added_at.isoformat() if membership.added_at else None,
    }


@watchlists_bp.get("/api/watchlists")
def list_watchlists():
    rows = ops_watchlists.get_all(g.db_session)
    counts = ops_watchlists.get_member_counts(g.db_session, [row.id for row in rows])
    return jsonify([_serialize(row, counts.get(row.id, 0)) for row in rows])


@watchlists_bp.post("/api/watchlists")
def create_watchlist():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if not name:
        return jsonify({"error": "name is required"}), 400

    fields = {"name": name, "description": body.get("description")}
    instrument_ids = body.get("instrument_ids") or []
    watchlist_id = ops_watchlists.create(g.db_session, fields, instrument_ids)
    g.db_session.flush()

    row = ops_watchlists.get_by_id(g.db_session, watchlist_id)
    return jsonify(_serialize(row, len(instrument_ids))), 201


@watchlists_bp.get("/api/watchlists/<int:watchlist_id>")
def get_watchlist(watchlist_id):
    row = ops_watchlists.get_by_id(g.db_session, watchlist_id)
    if row is None or not row.active:
        return "", 404
    members = ops_watchlists.get_members(g.db_session, watchlist_id)
    payload = _serialize(row, len(members))
    payload["members"] = [_serialize_member(m, s) for m, s in members]
    return jsonify(payload)


@watchlists_bp.put("/api/watchlists/<int:watchlist_id>")
def update_watchlist(watchlist_id):
    row = ops_watchlists.get_by_id(g.db_session, watchlist_id)
    if row is None or not row.active:
        return "", 404

    body = request.get_json(silent=True) or {}
    fields = {k: body[k] for k in ("name", "description") if k in body}
    ops_watchlists.update_fields(g.db_session, watchlist_id, fields)

    updated = ops_watchlists.get_by_id(g.db_session, watchlist_id)
    members = ops_watchlists.get_members(g.db_session, watchlist_id)
    payload = _serialize(updated, len(members))
    payload["members"] = [_serialize_member(m, s) for m, s in members]
    return jsonify(payload)


@watchlists_bp.delete("/api/watchlists/<int:watchlist_id>")
def delete_watchlist(watchlist_id):
    row = ops_watchlists.get_by_id(g.db_session, watchlist_id)
    if row is None:
        return "", 404
    if not row.active:
        return "", 204  # idempotent: already removed
    ops_watchlists.delete(g.db_session, watchlist_id)
    return "", 204


@watchlists_bp.post("/api/watchlists/<int:watchlist_id>/instruments")
def add_watchlist_instrument(watchlist_id):
    row = ops_watchlists.get_by_id(g.db_session, watchlist_id)
    if row is None or not row.active:
        return "", 404

    body = request.get_json(silent=True) or {}
    instrument_id = body.get("instrument_id")
    if not instrument_id:
        return jsonify({"error": "instrument_id is required"}), 400

    symbol_row = g.db_session.get(SubscribedSymbol, instrument_id)
    if symbol_row is None:
        return jsonify({"error": f"Unknown instrument_id: {instrument_id}"}), 404

    ops_watchlists.add_member(g.db_session, watchlist_id, instrument_id)
    g.db_session.flush()
    members = ops_watchlists.get_members(g.db_session, watchlist_id)
    return jsonify([_serialize_member(m, s) for m, s in members]), 201


@watchlists_bp.delete("/api/watchlists/<int:watchlist_id>/instruments/<int:instrument_id>")
def remove_watchlist_instrument(watchlist_id, instrument_id):
    row = ops_watchlists.get_by_id(g.db_session, watchlist_id)
    if row is None or not row.active:
        return "", 404
    ops_watchlists.remove_member(g.db_session, watchlist_id, instrument_id)
    return "", 204


@watchlists_bp.post("/api/watchlists/<int:watchlist_id>/subscribe")
def subscribe_watchlist(watchlist_id):
    """Registers every member of this watchlist into Market Watch's live feed
    — the user's own framing: "register current watchlist as the
    registration of watchlist for watch, which will fetch candles from
    tickers." Reactivates any member whose SubscribedSymbol was later
    unregistered (routes_symbols.py's DELETE doesn't touch WatchlistInstrument
    rows, so a watchlist can accumulate stale, inactive members over time)
    and (re)subscribes it, mirroring routes_symbols.add_symbol's own
    reactivate-then-backfill-then-subscribe flow exactly. Members already
    active are left alone — already subscribed via startup hydration or
    their own original registration, nothing to do.

    MarketWatch.jsx reads from the exact same SubscribedSymbol table
    (GET /api/symbols -> LibSymbols.get_active) this reactivates, so a
    member shows up there automatically — no separate Market-Watch-side
    change needed, the two pages already share this table.
    """
    row = ops_watchlists.get_by_id(g.db_session, watchlist_id)
    if row is None or not row.active:
        return "", 404

    broker = current_app.extensions["broker"]
    market_feed = current_app.extensions["market_feed"]

    members = ops_watchlists.get_members(g.db_session, watchlist_id)
    subscribed, already_active, failed = [], [], []
    for _membership, symbol_row in members:
        if symbol_row.active:
            already_active.append(symbol_row.symbol)
            continue
        try:
            quote = broker.get_quote(symbol_row.symbol, symbol_row.security_id, symbol_row.exchange_segment)
        except (BrokerAPIError, BrokerConnectionError) as exc:
            failed.append({"symbol": symbol_row.symbol, "error": str(exc)})
            continue

        symbol_row.active = True
        symbol_row.removed_at = None
        symbol_row.previous_close = quote.close
        g.db_session.flush()

        # backfill first, THEN subscribe — same ordering, same reason, as
        # routes_symbols.add_symbol (see backfill_then_subscribe's own
        # docstring: a live tick landing before backfill catches up breaks
        # the aggregator's chronological-order assumption).
        spawn_backfill_then_subscribe(
            {
                "security_id": symbol_row.security_id, "exchange_segment": symbol_row.exchange_segment,
                "symbol": symbol_row.symbol, "exchange": symbol_row.exchange, "segment": symbol_row.segment,
                "previous_close": float(symbol_row.previous_close) if symbol_row.previous_close is not None else None,
                "open": float(quote.open), "ltp": float(quote.ltp),
            },
            broker, current_app.extensions["db_session_factory"],
            current_app.extensions["candle_aggregator"], market_feed,
        )
        subscribed.append(symbol_row.symbol)

    return jsonify({"subscribed": subscribed, "already_active": already_active, "failed": failed}), 200
