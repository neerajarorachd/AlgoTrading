"""Market story API (plan P2). Read-only. Empty answers with "enabled": false
while the engine setting story_enabled is 0.

GET /api/story/now?timeframe=5min
    every stock's current setup: its open runs (state + how long).
GET /api/story/<symbol>?exchange_segment=NSE_EQ&timeframe=5min[&date=YYYY-MM-DD]
    one stock's story for a day, oldest first: today from the engine's
    memory (closed + open runs), an earlier day from the DB (today table,
    else archive).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request

from db.ops import LibStory, LibSymbols
from db.session import session_scope

story_bp = Blueprint("story", __name__)

TIMEFRAMES = ("1min", "3min", "5min")


def _engine_story():
    engine = current_app.extensions.get("activity_engine")
    return getattr(engine, "story", None) if engine is not None else None


def _timeframe():
    tf = request.args.get("timeframe", "5min")
    return tf if tf in TIMEFRAMES else None


def _iso(ts: datetime) -> str:
    return (ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts).isoformat() + "Z"


@story_bp.get("/api/story/now")
def story_now():
    tf = _timeframe()
    if tf is None:
        return jsonify({"error": f"timeframe must be one of {', '.join(TIMEFRAMES)}"}), 400
    story = _engine_story()
    if story is None:
        return jsonify({"enabled": False, "timeframe": tf, "stocks": []})
    from story.live import run_json
    engine = current_app.extensions["activity_engine"]
    with engine._lock:
        setup = story.setup_now(tf)
        stocks = [{"symbol": k[0], "exchange_segment": k[1],
                   "setup": {name: run_json(r) for name, r in sorted(runs.items())}}
                  for k, runs in sorted(setup.items())]
    return jsonify({"enabled": True, "timeframe": tf, "stocks": stocks})


@story_bp.get("/api/story/<symbol>")
def story_for(symbol: str):
    tf = _timeframe()
    if tf is None:
        return jsonify({"error": f"timeframe must be one of {', '.join(TIMEFRAMES)}"}), 400
    segment = request.args.get("exchange_segment", "NSE_EQ")
    asked = request.args.get("date")
    try:
        want = date.fromisoformat(asked) if asked else None
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400
    story = _engine_story()
    out = {"enabled": story is not None, "symbol": symbol, "exchange_segment": segment, "timeframe": tf}
    if story is not None:
        from story.live import run_json
        engine = current_app.extensions["activity_engine"]
        with engine._lock:
            day, runs = story.runs((symbol, segment, tf))
            if runs and (want is None or want == day):
                return jsonify({**out, "day": day.isoformat(), "source": "live", "runs": [run_json(r) for r in runs]})
    if want is None:
        want = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()
    with session_scope(current_app.extensions["db_session_factory"]) as session:
        iid = LibSymbols.get_instrument_id(session, symbol, segment)
        rows = LibStory.get_day(session, iid, tf, want) if iid is not None else []
    runs = [{"subject": r["subject"], "state": r["state"], "prev_state": r["prev_state"],
             "start_ts": _iso(r["start_ts"]), "end_ts": _iso(r["end_ts"]), "candles": r["candles"],
             "value_start": r["value_start"], "value_end": r["value_end"], "rank": r["rank"],
             "vol_ratio": r["vol_ratio"], "details": json.loads(r["details"]) if r["details"] else {},
             "is_open": bool(r["is_open"])} for r in rows]
    return jsonify({**out, "day": want.isoformat(), "source": "db", "runs": runs})
