"""Watch grid signal filter (ALGO-37): today's signals, patterns and strategy
hits for every active instrument in one call, so the grid can keep only the
stocks where a picked item formed, and the filter panel can show per-item
counts.

`?window=day` (default) counts the whole trading day; `?window=last10` only
the last 10 candles of each instrument's own timeframe (cut-off = that
timeframe's latest candle minus 9 candles). Read-only: rows come from the
live engine's in-memory store, which holds all of today (restored from the
DB after a restart); without an engine (scripts) the DB's today rows are used.

Response: {"window": ..., "instruments": {id: {timeframe: {"signals":
{code: n}, "patterns": {key: n}, "strategies": {id: n}}}}}."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request

from db.models import InstrumentActivity
from db.ops import LibSymbols as ops_symbols

watch_events_bp = Blueprint("watch_events", __name__)

WINDOWS = {"day": None, "last10": 10}
_TF_MINUTES = {"1min": 1, "3min": 3, "5min": 5}
_IST = timedelta(hours=5, minutes=30)


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _kind(activity_type: str) -> str:
    if activity_type == "pattern":
        return "patterns"
    if activity_type == "strategy":
        return "strategies"
    return "signals"


def _db_today_rows(session):
    now = datetime.now(timezone.utc)
    day_start = datetime.combine((now + _IST).date(), datetime.min.time()) - _IST  # 00:00 IST in naive UTC
    rows = session.query(InstrumentActivity).filter(InstrumentActivity.ts >= day_start).all()
    acts = [{"instrument_id": r.instrument_id, "timeframe": r.timeframe, "ts": _utc(r.ts),
             "activity_type": r.activity_type, "activity": r.activity} for r in rows]
    last = {}
    for r in acts:
        k = (r["instrument_id"], r["timeframe"])
        if k not in last or r["ts"] > last[k]:
            last[k] = r["ts"]
    return acts, last


def count_events(acts, last_ts, candles=None, instrument_ids=None):
    """{instrument_id: {timeframe: {kind: {key: n}}}}; `candles` = only the
    last N candles of each (instrument, timeframe), else the whole day."""
    out = {}
    seen = set()
    for r in acts:
        iid, tf = r["instrument_id"], r["timeframe"]
        if instrument_ids is not None and iid not in instrument_ids:
            continue
        ts = _utc(r["ts"])
        key = (iid, tf, ts, r["activity"])
        if key in seen:
            continue
        seen.add(key)
        if candles is not None:
            last = last_ts.get((iid, tf))
            minutes = _TF_MINUTES.get(tf)
            if last is None or minutes is None or ts < _utc(last) - timedelta(minutes=minutes * (candles - 1)):
                continue
        kinds = out.setdefault(iid, {}).setdefault(tf, {"signals": {}, "patterns": {}, "strategies": {}})
        bucket = kinds[_kind(r.get("activity_type") or "")]
        name = str(r["activity"])
        bucket[name] = bucket.get(name, 0) + 1
    return out


@watch_events_bp.get("/api/watch-events")
def get_watch_events():
    window = request.args.get("window", "day")
    if window not in WINDOWS:
        return jsonify({"error": f"window must be one of {sorted(WINDOWS)}"}), 400
    engine = current_app.extensions.get("activity_engine")
    acts, last = engine.today_rows() if engine is not None else _db_today_rows(g.db_session)
    ids = {i.id for i in ops_symbols.get_active(g.db_session)}
    counts = count_events(acts, last, WINDOWS[window], ids)
    return jsonify({"window": window, "instruments": {str(k): v for k, v in counts.items()}})
