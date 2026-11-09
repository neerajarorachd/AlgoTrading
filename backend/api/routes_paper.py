"""Paper trading API (rulebook 3a). Per user; no login yet, so owner_id=1.

Live:     GET/PUT /api/paper/selections, DELETE /api/paper/selections/<id>,
          GET /api/paper/live/trades
Backtest: POST/GET /api/paper/backtests, GET /api/paper/backtests/<id>
          (a master + its runs; each run has its own items and settings).
Never calls a broker.
"""
from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, g, jsonify, request

from db.models_paper import (PaperBacktestMaster, PaperBacktestRun, PaperBacktestTrade,
                             PaperSelection, PaperTradeLive)

paper_bp = Blueprint("paper", __name__)
USER_ID = 1

_MODES = {"stop_mode": {"atr", "pct", "points"}, "target_mode": {"atr", "pct", "points"},
          "size_mode": {"amount", "qty"}, "atr_timeframe": {"hit", "1min", "3min", "5min"}}
_NUMS = ("stop_value", "target_value", "amount_per_trade", "slippage_pct")
_INTS = ("atr_period", "quantity_per_trade", "entry_from", "entry_until", "day_end")
SETTING_FIELDS = tuple(_MODES) + _NUMS + _INTS


class BadRequest(ValueError):
    pass


def _settings_from(body: dict) -> dict:
    """Validated settings from a request body (only the fields given)."""
    out = {}
    for f, allowed in _MODES.items():
        if f in body:
            if body[f] not in allowed:
                raise BadRequest(f"{f} must be one of {sorted(allowed)}")
            out[f] = body[f]
    for f in _NUMS + _INTS:
        if f in body:
            try:
                v = float(body[f]) if f in _NUMS else int(body[f])
            except (TypeError, ValueError):
                raise BadRequest(f"{f} must be a number")
            if v < 0 or (f in ("stop_value", "target_value", "quantity_per_trade", "atr_period") and v <= 0):
                raise BadRequest(f"{f} must be positive")
            out[f] = v
    return out


def _settings_of(row) -> dict:
    return {f: getattr(row, f) for f in SETTING_FIELDS}


def _iso(ts):
    return ts.isoformat() + "Z" if ts else None


def _trade(t) -> dict:
    cols = [c.name for c in t.__table__.columns]
    d = {c: getattr(t, c) for c in cols}
    for c in cols:
        if isinstance(d[c], datetime):
            d[c] = _iso(d[c])
    return d


def _selection(s: PaperSelection) -> dict:
    return {"id": s.id, "item_type": s.item_type, "item_key": s.item_key, "enabled": s.enabled,
            **_settings_of(s)}


@paper_bp.errorhandler(BadRequest)
def _bad(e):
    return jsonify({"error": str(e)}), 400


# ------------------------------------------------------------------ live
@paper_bp.get("/api/paper/selections")
def list_selections():
    rows = g.db_session.query(PaperSelection).filter_by(owner_id=USER_ID).order_by(PaperSelection.item_key).all()
    return jsonify([_selection(s) for s in rows])


@paper_bp.put("/api/paper/selections")
def upsert_selection():
    body = request.get_json(force=True) or {}
    key = (body.get("item_key") or "").strip()
    item_type = body.get("item_type", "pattern")
    if not key:
        raise BadRequest("item_key is required")
    if item_type != "pattern":
        raise BadRequest("only item_type 'pattern' is supported for now")
    settings = _settings_from(body)
    s = g.db_session.query(PaperSelection).filter_by(owner_id=USER_ID, item_type=item_type, item_key=key).first()
    if s is None:
        s = PaperSelection(owner_id=USER_ID, item_type=item_type, item_key=key)
        g.db_session.add(s)
    for f, v in settings.items():
        setattr(s, f, v)
    if "enabled" in body:
        s.enabled = bool(body["enabled"])
    g.db_session.commit()
    return jsonify(_selection(s))


@paper_bp.delete("/api/paper/selections/<int:sel_id>")
def delete_selection(sel_id: int):
    s = g.db_session.get(PaperSelection, sel_id)
    if s is None or s.owner_id != USER_ID:
        return jsonify({"error": "not found"}), 404
    g.db_session.delete(s)
    g.db_session.commit()
    return jsonify({"deleted": sel_id})


@paper_bp.get("/api/paper/live/trades")
def live_trades():
    q = g.db_session.query(PaperTradeLive).filter_by(owner_id=USER_ID)
    if request.args.get("status"):
        q = q.filter_by(status=request.args["status"])
    limit = min(int(request.args.get("limit", 500)), 2000)
    return jsonify([_trade(t) for t in q.order_by(PaperTradeLive.hit_ts.desc()).limit(limit)])


# -------------------------------------------------------------- backtest
def _date(body, key):
    try:
        return datetime.fromisoformat(str(body[key])[:10])
    except (KeyError, ValueError):
        raise BadRequest(f"{key} must be a date YYYY-MM-DD")


def _master(m: PaperBacktestMaster, runs=None) -> dict:
    d = {"id": m.id, "name": m.name, "symbols": json.loads(m.symbols),
         "date_from": m.date_from.date().isoformat(), "date_to": m.date_to.date().isoformat(),
         "status": m.status, "progress_pct": m.progress_pct, "error": m.error,
         "created_at": _iso(m.created_at), "finished_at": _iso(m.finished_at)}
    if runs is not None:
        d["runs"] = [{"id": r.id, "label": r.label, "items": json.loads(r.items),
                      "summary": json.loads(r.summary) if r.summary else None, **_settings_of(r)}
                     for r in runs]
    return d


@paper_bp.post("/api/paper/backtests")
def create_backtest():
    """Body: {name, symbols: [instrument ids], date_from, date_to,
    runs: [{label, items: [pattern keys], ...settings}]}. Queued; the runner
    picks it up."""
    body = request.get_json(force=True) or {}
    symbols = body.get("symbols") or []
    runs = body.get("runs") or []
    if not symbols or not all(isinstance(s, int) for s in symbols):
        raise BadRequest("symbols must be a non-empty list of instrument ids")
    if not runs:
        raise BadRequest("at least one run is required")
    date_from, date_to = _date(body, "date_from"), _date(body, "date_to")
    if date_from > date_to:
        raise BadRequest("date_from is after date_to")
    parsed = []
    for i, r in enumerate(runs, 1):
        items = [k for k in (r.get("items") or []) if isinstance(k, str) and k.strip()]
        if not items:
            raise BadRequest(f"run {i} has no patterns")
        parsed.append((r.get("label") or f"Run {i}", items, _settings_from(r)))
    m = PaperBacktestMaster(owner_id=USER_ID, name=body.get("name") or "Comparison",
                            symbols=json.dumps(symbols), date_from=date_from, date_to=date_to)
    g.db_session.add(m)
    g.db_session.flush()
    for label, items, settings in parsed:
        g.db_session.add(PaperBacktestRun(
            master_id=m.id, owner_id=USER_ID, label=label,
            items=json.dumps([{"item_type": "pattern", "item_key": k} for k in items]), **settings))
    g.db_session.commit()
    return jsonify(_master(m, _runs(m.id))), 201


def _runs(master_id):
    return g.db_session.query(PaperBacktestRun).filter_by(master_id=master_id).order_by(PaperBacktestRun.id).all()


@paper_bp.get("/api/paper/backtests")
def list_backtests():
    rows = (g.db_session.query(PaperBacktestMaster).filter_by(owner_id=USER_ID)
            .order_by(PaperBacktestMaster.created_at.desc()).limit(100).all())
    return jsonify([_master(m) for m in rows])


@paper_bp.get("/api/paper/backtests/<int:master_id>")
def get_backtest(master_id: int):
    m = g.db_session.get(PaperBacktestMaster, master_id)
    if m is None or m.owner_id != USER_ID:
        return jsonify({"error": "not found"}), 404
    return jsonify(_master(m, _runs(m.id)))


@paper_bp.get("/api/paper/backtests/runs/<int:run_id>/trades")
def backtest_trades(run_id: int):
    r = g.db_session.get(PaperBacktestRun, run_id)
    if r is None or r.owner_id != USER_ID:
        return jsonify({"error": "not found"}), 404
    rows = (g.db_session.query(PaperBacktestTrade).filter_by(run_id=run_id)
            .order_by(PaperBacktestTrade.hit_ts).limit(5000).all())
    return jsonify([_trade(t) for t in rows])
