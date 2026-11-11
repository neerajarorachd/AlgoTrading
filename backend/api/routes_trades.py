"""Manual trades API (rulebook 3b). Per user; no login yet, so owner_id=1.

POST   /api/trades                      place (paper; live -> 400 "coming soon")
GET    /api/trades?scope=active|all&days=N   active = open + pending (with running P&L)
PATCH  /api/trades/<id>                 move stop / target
POST   /api/trades/<id>/exit            exit: {type: market | limit, limit_price} (cancel when pending)
DELETE /api/trades/<id>/exit            cancel a waiting limit exit
GET    /api/trades/orders?status=pending|trade_id=N   the order book (entry, stop / target legs, exits)
DELETE /api/trades/orders/<id>          cancel one pending order (Pending grid)
GET    /api/trades/<id>                  full details (+ exact expenses for an open trade)
GET    /api/trades/<id>/snapshots       indicator values at entry / exit
GET    /api/trades/account?mode=paper   capital, used, free, realised / unrealised P&L
GET    /api/trades/summary?days=N       totals before / after expenses, by expiry bucket
Switched off by the system setting `manual_trades_enabled` (0 = off).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, g, jsonify, request

from db.models_trades import Trade, TradeIndicatorSnapshot
from db.ops import LibSymbols, LibSystemSettings
from trades.engine import OrderError, to_dict

trades_bp = Blueprint("trades", __name__)
OWNER_ID = 1


def _iso(v):
    if isinstance(v, datetime):
        return v.isoformat() + "Z"
    if isinstance(v, date):
        return v.isoformat()
    return v


def serialize(t: dict) -> dict:
    out = {k: _iso(v) for k, v in t.items() if not k.startswith("_")}
    for k in ("running", "exact"):
        if isinstance(t.get(k), dict):
            out[k] = dict(t[k])
    return out


def _engine():
    return current_app.extensions.get("trade_engine")


def _enabled() -> bool:
    try:
        return bool(int(LibSystemSettings.load_by_keys(g.db_session, ["manual_trades_enabled"])
                        .get("manual_trades_enabled", 1)))
    except Exception:
        return True


def _bad(msg, code=400):
    return jsonify({"error": msg}), code


@trades_bp.post("/api/trades")
def place_trade():
    if not _enabled():
        return _bad("manual trades are switched off", 403)
    eng = _engine()
    if eng is None:
        return _bad("trade engine not running", 503)
    body = request.get_json(silent=True) or {}
    if body.get("mode") == "live":
        return _bad("Live trading coming soon")
    try:
        inst_id = int(body.get("instrument_id"))
    except (TypeError, ValueError):
        return _bad("instrument_id is required")
    inst = LibSymbols.get_by_id(g.db_session, inst_id)
    if inst is None or not inst.active:
        return _bad("unknown or inactive instrument")
    try:
        trade = eng.place_order(
            owner_id=OWNER_ID, instrument=inst, direction=body.get("direction"),
            quantity=body.get("quantity"), product=body.get("product", "intraday"),
            entry_type=body.get("entry_type", "market"), limit_price=body.get("limit_price"),
            stop_price=body.get("stop_price"), target_price=body.get("target_price"),
            mode=body.get("mode", "paper"), source=body.get("source", "grid"), note=body.get("note"),
            screen_signal=body.get("screen_signal"))
    except OrderError as e:
        return _bad(str(e))
    return jsonify(serialize(trade)), 201


@trades_bp.get("/api/trades")
def list_trades():
    eng = _engine()
    scope = request.args.get("scope", "active")
    if scope == "active":
        return jsonify([serialize(t) for t in (eng.active_trades(OWNER_ID) if eng else [])])
    try:
        days = max(1, min(3650, int(request.args.get("days", 30))))
    except ValueError:
        return _bad("days must be a number")
    since = datetime.utcnow() - timedelta(days=days)
    rows = (g.db_session.query(Trade).filter(Trade.owner_id == OWNER_ID, Trade.placed_ts >= since)
            .order_by(Trade.placed_ts.desc()).limit(2000).all())
    active = {t["id"]: t for t in (eng.active_trades(OWNER_ID) if eng else [])}
    return jsonify([serialize(active.get(r.id) or to_dict(r)) for r in rows])


@trades_bp.patch("/api/trades/<int:trade_id>")
def modify_trade(trade_id: int):
    eng = _engine()
    body = request.get_json(silent=True) or {}
    kwargs = {k: body[k] for k in ("stop_price", "target_price") if k in body}
    try:
        return jsonify(serialize(eng.modify(OWNER_ID, trade_id, **kwargs)))
    except OrderError as e:
        return _bad(str(e))


@trades_bp.post("/api/trades/<int:trade_id>/exit")
def exit_trade(trade_id: int):
    """Body (optional): {"type": "market" | "limit", "limit_price": n}. No body = market."""
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(serialize(_engine().exit_now(OWNER_ID, trade_id, body.get("type", "market"),
                                                    body.get("limit_price"))))
    except OrderError as e:
        return _bad(str(e))


@trades_bp.delete("/api/trades/<int:trade_id>/exit")
def cancel_exit_limit(trade_id: int):
    try:
        return jsonify(serialize(_engine().cancel_exit_limit(OWNER_ID, trade_id)))
    except OrderError as e:
        return _bad(str(e))


@trades_bp.get("/api/trades/orders")
def list_orders():
    """The order book: ?status=pending for the Pending grid (each with its trade's
    direction / product / instrument), or every order of ?trade_id=N."""
    eng = _engine()
    if eng is None:
        return jsonify([])
    trade_id = request.args.get("trade_id", type=int)
    orders = eng.orders_for(OWNER_ID, trade_id=trade_id, status=request.args.get("status"))
    ids = {o["trade_id"] for o in orders}
    trades = {r.id: r for r in g.db_session.query(Trade).filter(Trade.id.in_(ids)).all()} if ids else {}
    out = []
    for o in orders:
        t = trades.get(o["trade_id"])
        out.append({**{k: _iso(v) for k, v in o.items()},
                    "trade_direction": t.direction if t else None, "product": t.product if t else None,
                    "instrument_id": t.instrument_id if t else None,
                    "exchange_segment": t.exchange_segment if t else None,
                    "trade_status": t.status if t else None})
    return jsonify(out)


@trades_bp.delete("/api/trades/orders/<int:order_id>")
def cancel_order(order_id: int):
    try:
        return jsonify(serialize(_engine().cancel_order(OWNER_ID, order_id)))
    except OrderError as e:
        return _bad(str(e))


@trades_bp.get("/api/trades/<int:trade_id>")
def trade_detail(trade_id: int):
    """Full order details. An open trade also gets `exact`: P&L with the real
    expenses at the current price (the list only shows gross - est_costs)."""
    eng = _engine()
    active = eng.detail(OWNER_ID, trade_id) if eng else None
    if active is not None:
        return jsonify(serialize(active))
    row = g.db_session.get(Trade, trade_id)
    if row is None or row.owner_id != OWNER_ID:
        return _bad("not found", 404)
    return jsonify(serialize(to_dict(row)))


@trades_bp.get("/api/trades/<int:trade_id>/snapshots")
def trade_snapshots(trade_id: int):
    row = g.db_session.get(Trade, trade_id)
    if row is None or row.owner_id != OWNER_ID:
        return _bad("not found", 404)
    snaps = (g.db_session.query(TradeIndicatorSnapshot).filter_by(trade_id=trade_id)
             .order_by(TradeIndicatorSnapshot.event.desc(), TradeIndicatorSnapshot.timeframe).all())
    cols = [c.name for c in TradeIndicatorSnapshot.__table__.columns]
    return jsonify([{c: _iso(getattr(s, c)) for c in cols} for s in snaps])


@trades_bp.get("/api/trades/account")
def trade_account():
    eng = _engine()
    if eng is None:
        return _bad("trade engine not running", 503)
    return jsonify(eng.account(OWNER_ID, request.args.get("mode", "paper")))


@trades_bp.get("/api/trades/summary")
def trade_summary():
    """Closed trades: count, wins, gross (before expenses), costs, net (after),
    split by expiry bucket (expiry day / rest of expiry week / other days)."""
    try:
        days = max(1, min(3650, int(request.args.get("days", 30))))
    except ValueError:
        return _bad("days must be a number")
    since = datetime.utcnow() - timedelta(days=days)
    rows = g.db_session.query(Trade).filter(Trade.owner_id == OWNER_ID, Trade.status == "closed",
                                            Trade.placed_ts >= since).all()

    def agg(rs):
        priced = [r for r in rs if r.net_pnl is not None]
        return {"trades": len(priced), "wins": sum(1 for r in priced if r.net_pnl > 0),
                "gross_pnl": round(sum(r.gross_pnl or 0 for r in priced), 2),
                "costs": round(sum(r.costs or 0 for r in priced), 2),
                "net_pnl": round(sum(r.net_pnl for r in priced), 2)}

    def bucket(r):
        if r.is_expiry_day:
            return "expiry_day"
        return "expiry_week" if r.is_expiry_week else "other"

    by = {b: agg([r for r in rows if bucket(r) == b]) for b in ("expiry_day", "expiry_week", "other")}
    return jsonify({"days": days, "all": agg(rows), "by_expiry": by})
