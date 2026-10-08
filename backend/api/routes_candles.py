from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request

from db.models import CandleHistorical, CandleToday
from db.ops import LibActivities as ops_activities
from db.ops import LibCandleIndicators as ops_indicators
from db.ops import LibCandles as ops_candles
from db.ops import LibCandlesHistorical as ops_candles_historical
from db.ops import LibSymbols as ops_symbols
from pivot_points import classic_pivot_points
from prediction_tracker import BEARISH_PATTERNS, BULLISH_PATTERNS

candles_bp = Blueprint("candles", __name__)

_VALID_TIMEFRAMES = {"1min", "3min", "5min"}
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _ist_today():
    # NSE always trades in IST -- "today" must mean the IST calendar date,
    # not whatever the server's own timezone happens to be. Same convention
    # already used on the frontend (SymbolTable.jsx's formatEventTs).
    return (datetime.now(timezone.utc) + _IST_OFFSET).date()


def _ist_date(ts: datetime):
    # Stored candle timestamps are UTC (naive or aware) throughout this
    # project -- shift to IST before taking the calendar date, or a candle
    # from market open (9:15 IST = 3:45 UTC) would compare against the
    # previous UTC day and get wrongly treated as "not today."
    aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return (aware + _IST_OFFSET).date()


@candles_bp.get("/api/candles")
def get_candles():
    symbol = request.args.get("symbol")
    exchange_segment = request.args.get("exchange_segment")
    timeframe = request.args.get("timeframe", "1min")

    if not symbol or not exchange_segment:
        return jsonify({"error": "symbol and exchange_segment are required"}), 400
    if timeframe not in _VALID_TIMEFRAMES:
        return jsonify({"error": f"timeframe must be one of {sorted(_VALID_TIMEFRAMES)}"}), 400

    from_param = request.args.get("from")
    to_param = request.args.get("to")
    ts_from = _parse_ts(from_param) if from_param else None
    ts_to = _parse_ts(to_param) if to_param else None

    rows = ops_candles.get_range(
        g.db_session, symbol, exchange_segment, timeframe, ts_from=ts_from, ts_to=ts_to,
    )

    # An explicit from/to range is a caller asking for exactly that period
    # (e.g. a backtest-style lookup) -- return it as-is, empty or not, and
    # skip the "is this actually today" / fallback logic entirely below,
    # which only makes sense for "give me whatever's current."
    if ts_from is not None or ts_to is not None:
        return jsonify({"candles": [_serialize(row) for row in rows], "live": True, "as_of_date": None})

    if rows:
        latest_date = max(_ist_date(row.ts) for row in rows)
        if latest_date == _ist_today():
            # Today's session only -- candles_today isn't reliably cleared
            # between sessions, so returning every row put weeks of stale
            # rows (and, found live 2026-10-06, a bogus epoch-zero candle)
            # on one chart, squashing its time axis. Same single-day rule
            # the not-live fallback below already applies.
            todays = [row for row in rows if _ist_date(row.ts) == latest_date]
            return jsonify({"candles": [_serialize(row) for row in todays], "live": True, "as_of_date": None})

    # Not live (either candles_today was empty, or its rows are all stale --
    # e.g. the daily flush didn't run and scattered leftover rows from many
    # old sessions are sitting there, see comment below). Compare against
    # candles_historical's own latest day and use whichever is actually more
    # complete: candles_today's "latest date" is whatever date its single
    # newest row happens to carry, which can be a near-empty, orphaned
    # session (observed live, 2026-10-05: 2400+ total candles_today rows
    # scattered across many dates, but only 1 row on the single most-recent
    # date) -- a one-candle "latest day" is not a useful chart just because
    # it's chronologically newer than a full session sitting in
    # candles_historical. Recency alone is the wrong tiebreaker here;
    # row count (session completeness) is what actually matters for a chart.
    candles_today_latest_day = (
        [row for row in rows if _ist_date(row.ts) == latest_date] if rows else []
    )
    historical_latest_day = ops_candles_historical.get_latest_day_range(
        g.db_session, symbol, exchange_segment, timeframe,
    )
    chosen_rows = (
        historical_latest_day
        if len(historical_latest_day) > len(candles_today_latest_day)
        else candles_today_latest_day
    )
    if chosen_rows:
        as_of_date = _ist_date(chosen_rows[0].ts).isoformat()
        return jsonify({
            "candles": [_serialize(row) for row in chosen_rows],
            "live": False,
            "as_of_date": as_of_date,
        })

    return jsonify({"candles": [], "live": True, "as_of_date": None})


@candles_bp.get("/api/candles/indicators")
def get_candle_indicators():
    """VWAP/BB/RSI/MACD/Stochastic/MA/EMA aligned to each closed candle,
    for the live chart's overlay/sub-panel series (see memory:
    live_indicators_phase1_priority) -- activity_engine.py already
    computes and persists every one of these per candle close, this is
    just the first endpoint that serves them back out. Deliberately
    separate from GET /api/candles (not merged into it): most callers of
    the candles endpoint don't want the extra payload, and the two tables
    are keyed differently (CandleToday/Historical by symbol string,
    CandleIndicators by instrument_id) so joining them server-side here
    keeps that translation out of the frontend.

    Same query contract as GET /api/candles (symbol, exchange_segment,
    timeframe, optional from/to) except the from/to default is "today"
    (IST), not unbounded -- this table isn't pruned the way candles_today
    is, so an unbounded read would grow without limit over the life of
    the instrument.
    """
    symbol = request.args.get("symbol")
    exchange_segment = request.args.get("exchange_segment")
    timeframe = request.args.get("timeframe", "1min")

    if not symbol or not exchange_segment:
        return jsonify({"error": "symbol and exchange_segment are required"}), 400
    if timeframe not in _VALID_TIMEFRAMES:
        return jsonify({"error": f"timeframe must be one of {sorted(_VALID_TIMEFRAMES)}"}), 400

    instrument_id = ops_symbols.get_instrument_id(g.db_session, symbol, exchange_segment)
    if instrument_id is None:
        return jsonify({"indicators": []})

    ts_from, ts_to = _resolve_range()
    stored = ops_indicators.get_for_instrument_range(
        g.db_session, instrument_id, timeframe, ts_from=ts_from, ts_to=ts_to,
    )
    rows = _merge_pending(stored, _pending("indicators", instrument_id, timeframe), ts_from, ts_to,
                          key=lambda r: _naive_utc(_field(r, "ts")))
    return jsonify({"indicators": [_serialize_indicators(row) for row in rows]})


@candles_bp.get("/api/candles/pivots")
def get_candle_pivots():
    """Classic pivot points (P/R1-3/S1-3) for the live chart's price pane,
    computed from the most recent COMPLETE prior trading day's "1day"
    candle (see pivot_points.py). Day-level, not timeframe-specific --
    the same levels apply whether the chart is showing 1min/3min/5min.
    `pivots` is null (not an error) when no prior-day candle exists yet.
    """
    symbol = request.args.get("symbol")
    exchange_segment = request.args.get("exchange_segment")
    if not symbol or not exchange_segment:
        return jsonify({"error": "symbol and exchange_segment are required"}), 400

    today_start = datetime.combine(_ist_today(), datetime.min.time()) - _IST_OFFSET
    prev = ops_candles_historical.get_previous_day_ohlc(g.db_session, symbol, exchange_segment, before=today_start)
    if prev is None:
        return jsonify({"pivots": None})
    prev_high, prev_low, prev_close = prev
    return jsonify({"pivots": classic_pivot_points(prev_high, prev_low, prev_close)})


@candles_bp.get("/api/candles/markers")
def get_candle_markers():
    """Today's fired patterns/indicator-crossover signals for one symbol,
    shaped for the live chart's price-pane markers (see memory:
    live_indicators_phase1_priority) -- reads the same instrument_activity
    rows the Market Watch score grid and recommendation pipeline already
    use, just re-shaped and time-bounded for a chart overlay rather than a
    bull/bear score. `direction` reuses prediction_tracker's own
    BULLISH_PATTERNS/BEARISH_PATTERNS classification (the same one
    watch_scoring.py already keys off of) rather than inventing a second
    one here -- an activity not in either set (a pattern with no defined
    directional bias yet) comes back with direction: null.
    """
    symbol = request.args.get("symbol")
    exchange_segment = request.args.get("exchange_segment")
    timeframe = request.args.get("timeframe", "1min")
    if not symbol or not exchange_segment:
        return jsonify({"error": "symbol and exchange_segment are required"}), 400
    if timeframe not in _VALID_TIMEFRAMES:
        return jsonify({"error": f"timeframe must be one of {sorted(_VALID_TIMEFRAMES)}"}), 400

    instrument_id = ops_symbols.get_instrument_id(g.db_session, symbol, exchange_segment)
    if instrument_id is None:
        return jsonify({"markers": []})

    ts_from, ts_to = _resolve_range()
    stored = ops_activities.get_for_instrument_range(
        g.db_session, instrument_id, timeframe, ts_from=ts_from, ts_to=ts_to,
    )
    rows = _merge_pending(stored, _pending("activities", instrument_id, timeframe), ts_from, ts_to,
                          key=lambda r: (_naive_utc(_field(r, "ts")), _field(r, "activity")))
    return jsonify({"markers": [_serialize_marker(row) for row in rows]})


def _resolve_range():
    """Explicit from/to when given (the chart passes the day it's actually
    showing, which isn't today when it fell back to a previous session);
    otherwise today (IST), unbounded above."""
    from_param = request.args.get("from")
    to_param = request.args.get("to")
    if from_param or to_param:
        return (_parse_ts(from_param) if from_param else None, _parse_ts(to_param) if to_param else None)
    return datetime.combine(_ist_today(), datetime.min.time()) - _IST_OFFSET, None


def _pending(kind: str, instrument_id: int, timeframe: str) -> list:
    """The running ActivityEngine's not-yet-flushed rows -- it only writes to
    the DB once a day (feed/bootstrap.py's _schedule_daily_flush), so during
    market hours today's indicators/activities exist only in memory. Empty
    when there's no live engine (tests, scripts)."""
    engine = current_app.extensions.get("activity_engine")
    if engine is None:
        return []
    if kind == "indicators":
        return engine.pending_indicator_rows(instrument_id, timeframe)
    return engine.pending_activities(instrument_id, timeframe)


def _merge_pending(stored: list, pending: list, ts_from, ts_to, key) -> list:
    seen = {key(r) for r in stored}
    merged = list(stored)
    for r in pending:
        ts = _naive_utc(r["ts"])
        if (ts_from is not None and ts < ts_from) or (ts_to is not None and ts > ts_to):
            continue
        if key(r) not in seen:
            seen.add(key(r))
            merged.append(r)
    merged.sort(key=lambda r: _naive_utc(_field(r, "ts")))
    return merged


def _field(row, name):
    return row[name] if isinstance(row, dict) else getattr(row, name)


def _naive_utc(ts: datetime) -> datetime:
    # DB rows come back naive-UTC; the engine's in-memory rows carry the
    # candle's own (aware, UTC) timestamp -- normalize so they compare.
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def _serialize_marker(row) -> dict:
    activity = _field(row, "activity")
    direction = "bull" if activity in BULLISH_PATTERNS else "bear" if activity in BEARISH_PATTERNS else None
    return {
        "ts": _naive_utc(_field(row, "ts")).isoformat() + "Z",
        "activity_type": _field(row, "activity_type"),
        "activity": activity,
        "direction": direction,
    }


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _serialize(row: CandleToday | CandleHistorical) -> dict:
    ts = row.ts.replace(tzinfo=None) if row.ts.tzinfo else row.ts
    return {
        "ts": ts.isoformat() + "Z",
        "open": float(row.open_price),
        "high": float(row.high_price),
        "low": float(row.low_price),
        "close": float(row.close_price),
        "volume": row.volume,
    }


def _num(value) -> float | None:
    return float(value) if value is not None else None


_INDICATOR_FIELDS = (
    "rsi", "macd_line", "macd_signal", "stoch_k", "stoch_d", "vwap", "ma21", "ma50",
    "ema5", "ema14", "ema21", "ema50", "atr", "bb_upper", "bb_middle", "bb_lower",
)


def _serialize_indicators(row) -> dict:
    """A stored CandleIndicators row or the engine's in-memory dict for one
    not-yet-flushed (same keys -- it's the dict persist_bulk would write)."""
    out = {"ts": _naive_utc(_field(row, "ts")).isoformat() + "Z"}
    for name in _INDICATOR_FIELDS:
        out[name] = _num(row.get(name) if isinstance(row, dict) else getattr(row, name))
    return out
