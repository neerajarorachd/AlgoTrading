"""BacktestRunMaster/BacktestRun launch + read API — the UI-facing side of
backend/scripts/run_backtest.py's DB-driven execution wiring (see
[[backtest_run_execution_wiring]]). Launching a run is synchronous (small/
manual scope for now, same convention as /api/historical-data/backfill —
no background job) and can take anywhere from under a second to a while for
a long date range or a large watchlist, since every instrument in scope is
run in sequence within the one request.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from db.models import CandleIndicators  # noqa: E402
from db.ops import LibBacktestRuns as ops_runs  # noqa: E402
from db.ops import LibCandlesHistorical as ops_candles  # noqa: E402
from db.ops import LibStrategies as ops_strategies  # noqa: E402
from db.ops import LibSymbols as ops_symbols  # noqa: E402
from db.ops import LibWatchlists as ops_watchlists  # noqa: E402
from occurrence_backtest import record_occurrence_backtest  # noqa: E402
from run_backtest import run_backtest  # noqa: E402
from timeframes import candle_duration  # noqa: E402

backtests_bp = Blueprint("backtests", __name__)


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _num(value):
    """SQL Numeric columns come back as Decimal (real SQL Server/pyodbc) or
    float (SQLite in tests) — normalize either to a plain JSON-safe float."""
    return None if value is None else float(value)


def _serialize_master(row) -> dict:
    return {
        "id": row.id, "name": row.name, "mode": row.mode, "source": row.source,
        "instrument_id": row.instrument_id, "watchlist_id": row.watchlist_id,
        "date_from": row.date_from.isoformat(), "date_to": row.date_to.isoformat(),
        "status": row.status, "run_count": row.run_count, "best_run_id": row.best_run_id,
        "time_elapsed_seconds": _num(row.time_elapsed_seconds),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _serialize_run(row, result=None, symbol: str = None) -> dict:
    payload = {
        "id": row.id, "run_master_id": row.run_master_id, "instrument_id": row.instrument_id,
        "symbol": symbol, "strategy_id": row.strategy_id, "pattern": row.pattern, "timeframe": row.timeframe,
        "status": row.status, "error_message": row.error_message,
        "time_elapsed_seconds": _num(row.time_elapsed_seconds),
    }
    if result is not None:
        payload["result"] = _serialize_result(result)
    return payload


def _serialize_result(row) -> dict:
    return {
        "total_trades": row.total_trades, "wins": row.wins, "losses": row.losses,
        "win_ratio": _num(row.win_ratio),
        "total_gross_pnl": _num(row.total_gross_pnl), "total_expenses": _num(row.total_expenses),
        "total_net_pnl": _num(row.total_net_pnl),
        "pre_run_capital": _num(row.pre_run_capital), "post_run_capital": _num(row.post_run_capital),
        "winning_days": row.winning_days, "losing_days": row.losing_days, "flat_days": row.flat_days,
        "days_with_trades": row.days_with_trades,
    }


_INDICATOR_FIELDS = (
    "rsi", "macd_line", "macd_signal", "stoch_k", "stoch_d", "vwap", "ma21", "ma50", "atr",
    "bb_upper", "bb_middle", "bb_lower", "rsi_state", "macd_state", "stoch_state",
    "rsi_trend", "macd_trend", "stoch_trend",
)


def _serialize_trade(row) -> dict:
    payload = {
        "id": row.id, "instrument_id": row.instrument_id, "timeframe": row.timeframe,
        "pattern": row.pattern, "direction": row.direction,
        "entry_ts": row.entry_ts.isoformat(), "entry_price": _num(row.entry_price),
        "stop_loss": _num(row.stop_loss), "target": _num(row.target),
        "exit_ts": row.exit_ts.isoformat(), "exit_price": _num(row.exit_price),
        "exit_reason": row.exit_reason, "quantity": row.quantity, "margin_used": _num(row.margin_used),
        "gross_pnl": _num(row.gross_pnl), "expenses": _num(row.expenses), "net_pnl": _num(row.net_pnl),
    }
    # entry_*/exit_* indicator value+state+trend columns -- None on every
    # trade persisted before this feature existed (an older BacktestTrade
    # row with no indicator snapshot), same "None until ready" convention
    # as everywhere else this data appears. state/trend are already plain
    # strings ("oversold"/"increasing"/etc, decoded by the ORM's TypeDecorator) --
    # only the raw value columns need _num()'s Decimal->float normalization.
    for prefix in ("entry_", "exit_"):
        for field in _INDICATOR_FIELDS:
            key = f"{prefix}{field}"
            value = getattr(row, key, None)
            is_label = field.endswith("_state") or field.endswith("_trend")
            payload[key] = value if is_label else _num(value)
    return payload


def _serialize_day_result(row) -> dict:
    return {
        "date": row.date.isoformat(), "trade_count": row.trade_count,
        "win_count": row.win_count, "loss_count": row.loss_count, "net_pnl": _num(row.net_pnl),
    }


@backtests_bp.get("/api/backtests/runs")
def list_run_masters():
    rows = ops_runs.get_all_run_masters(g.db_session)
    return jsonify([_serialize_master(r) for r in rows])


@backtests_bp.get("/api/backtests/runs/<int:run_master_id>")
def get_run_master(run_master_id):
    master = ops_runs.get_run_master(g.db_session, run_master_id)
    if master is None:
        return "", 404
    runs = ops_runs.get_runs_for_master(g.db_session, run_master_id)
    results = ops_runs.get_run_results_for_master(g.db_session, run_master_id)
    symbols = {
        s.id: s.symbol
        for s in (ops_symbols.get_by_id(g.db_session, r.instrument_id) for r in runs)
        if s is not None
    }
    payload = _serialize_master(master)
    payload["runs"] = [_serialize_run(r, results.get(r.id), symbols.get(r.instrument_id)) for r in runs]
    return jsonify(payload)


@backtests_bp.get("/api/backtests/runs/<int:run_master_id>/runs/<int:run_id>/trades")
def get_run_trades(run_master_id, run_id):
    run = ops_runs.get_run(g.db_session, run_id)
    if run is None or run.run_master_id != run_master_id:
        return "", 404
    trades = ops_runs.get_trades_for_run(g.db_session, run_id)
    return jsonify([_serialize_trade(t) for t in trades])


@backtests_bp.get("/api/backtests/runs/<int:run_master_id>/runs/<int:run_id>/trades/<int:trade_id>/path")
def get_trade_execution_path(run_master_id, run_id, trade_id):
    """Candle-by-candle OHLC path from entry to exit (plus a few candles of
    padding on each side), for the trade drilldown's execution-path chart —
    the wavy price line between the SL/TG bars the user sketched, meant to
    "help find edges of sl and tg." padding: candles of context before
    entry_ts / after exit_ts, default 5."""
    run = ops_runs.get_run(g.db_session, run_id)
    if run is None or run.run_master_id != run_master_id:
        return "", 404
    trade = ops_runs.get_trade(g.db_session, trade_id)
    if trade is None or trade.run_id != run_id:
        return "", 404
    symbol_row = ops_symbols.get_by_id(g.db_session, trade.instrument_id)
    if symbol_row is None:
        return "", 404

    padding = request.args.get("padding", default=5, type=int)
    pad = candle_duration(trade.timeframe) * padding
    candles = ops_candles.get_range(
        g.db_session, symbol_row.symbol, symbol_row.exchange_segment, trade.timeframe,
        trade.entry_ts - pad, trade.exit_ts + pad,
    )
    return jsonify({
        "trade": _serialize_trade(trade),
        "candles": [
            {
                "ts": c.ts.isoformat(), "open": _num(c.open_price), "high": _num(c.high_price),
                "low": _num(c.low_price), "close": _num(c.close_price), "volume": c.volume,
            }
            for c in candles
        ],
    })


@backtests_bp.get("/api/backtests/runs/<int:run_master_id>/runs/<int:run_id>/day-chart")
def get_run_day_chart(run_master_id, run_id):
    """The WHOLE trading day's OHLC (plus VWAP/Bollinger Bands where
    CandleIndicators has them), for the day-level chart in the Years ->
    Months -> Days -> Trades drill-down -- explicit instruction, 2026-09-18:
    "I want full day chart, not the trade chart... also wants BB and vwap."
    Trade entry/exit markers are drawn frontend-side from the day's already-
    fetched trades (getBacktestTrades), not returned here -- this endpoint
    is candles only, matching /trades/<id>/path's own narrow scope."""
    run = ops_runs.get_run(g.db_session, run_id)
    if run is None or run.run_master_id != run_master_id:
        return "", 404
    date_s = request.args.get("date")
    if not date_s:
        return jsonify({"error": "date is required (YYYY-MM-DD)"}), 400
    symbol_row = ops_symbols.get_by_id(g.db_session, run.instrument_id)
    if symbol_row is None:
        return "", 404

    day_start = _parse_date(date_s)
    day_end = day_start + timedelta(days=1)
    candles = ops_candles.get_range(
        g.db_session, symbol_row.symbol, symbol_row.exchange_segment, run.timeframe, day_start, day_end,
    )
    indicators = {
        row.ts: row
        for row in g.db_session.query(CandleIndicators).filter(
            CandleIndicators.instrument_id == run.instrument_id,
            CandleIndicators.timeframe == run.timeframe,
            CandleIndicators.ts >= day_start, CandleIndicators.ts < day_end,
        ).all()
    }
    return jsonify({
        "candles": [
            {
                "ts": c.ts.isoformat(), "open": _num(c.open_price), "high": _num(c.high_price),
                "low": _num(c.low_price), "close": _num(c.close_price), "volume": c.volume,
                "vwap": _num(indicators[c.ts].vwap) if c.ts in indicators else None,
                "bb_upper": _num(indicators[c.ts].bb_upper) if c.ts in indicators else None,
                "bb_middle": _num(indicators[c.ts].bb_middle) if c.ts in indicators else None,
                "bb_lower": _num(indicators[c.ts].bb_lower) if c.ts in indicators else None,
            }
            for c in candles
        ],
    })


@backtests_bp.get("/api/backtests/runs/<int:run_master_id>/runs/<int:run_id>/day-results")
def get_run_day_results(run_master_id, run_id):
    run = ops_runs.get_run(g.db_session, run_id)
    if run is None or run.run_master_id != run_master_id:
        return "", 404
    rows = ops_runs.get_day_results_for_run(g.db_session, run_id)
    return jsonify([_serialize_day_result(r) for r in rows])


@backtests_bp.post("/api/backtests/runs")
def create_run():
    """Launches one or more BacktestRun rows. body: {strategy_id,
    timeframe, start, end, session_name?, instrument_id? | watchlist_id?,
    run_master_id?}.

    Exactly one of instrument_id/watchlist_id is required to start a NEW
    session ("one run per stock in watchlist", explicit instruction — a
    watchlist expands into one run per active member). When run_master_id
    is given instead, the EXISTING session's own scope is reused (its
    instrument_id/watchlist_id, resolved the same way) and
    instrument_id/watchlist_id/session_name in the body are ignored —
    that's how a "1min vs 3min" comparison adds its second run next to
    the first.
    """
    body = request.get_json(silent=True) or {}
    strategy_id = body.get("strategy_id")
    timeframe = body.get("timeframe")
    start_s, end_s = body.get("start"), body.get("end")
    if not strategy_id or not timeframe or not start_s or not end_s:
        return jsonify({"error": "strategy_id, timeframe, start, end are required"}), 400

    strategy = ops_strategies.get_by_id(g.db_session, strategy_id)
    if strategy is None:
        return jsonify({"error": f"Unknown strategy_id: {strategy_id}"}), 404

    start = _parse_date(start_s)
    end = _parse_date(end_s) + timedelta(days=1)
    session_factory = current_app.extensions["db_session_factory"]

    # Resolution reads below deliberately use a short-lived session from
    # session_factory (opened and closed immediately), NOT g.db_session —
    # g.db_session stays open for the whole request, and under this
    # project's test suite's shared-cache SQLite setup, a lock it takes on
    # backtest_run_masters/backtest_runs (tables run_backtest() itself also
    # WRITES to, from its own separate sessions) would block those writes
    # and fail the request with "database table is locked" (found the hard
    # way — reproducible every time run_master_id was checked via
    # g.db_session first). Real SQL Server's row-level MVCC locking
    # wouldn't hit this, but there's no reason to depend on that difference
    # when the fix is this cheap.
    run_master_id = body.get("run_master_id")
    session_name = None
    if run_master_id is not None:
        with session_factory() as session:
            master = ops_runs.get_run_master(session, run_master_id)
            if master is None:
                return jsonify({"error": f"Unknown run_master_id: {run_master_id}"}), 404
            instrument_ids = ops_runs.resolve_run_instrument_ids(session, master)
    else:
        instrument_id = body.get("instrument_id")
        watchlist_id = body.get("watchlist_id")
        if not instrument_id and not watchlist_id:
            return jsonify({"error": "instrument_id or watchlist_id is required for a new session"}), 400
        if instrument_id and watchlist_id:
            return jsonify({"error": "instrument_id and watchlist_id are mutually exclusive"}), 400
        if watchlist_id:
            with session_factory() as session:
                members = ops_watchlists.get_members(session, watchlist_id)
                instrument_ids = [s.id for _, s in members]
            if not instrument_ids:
                return jsonify({"error": "watchlist has no active members"}), 400
        else:
            instrument_ids = [instrument_id]
        session_name = body.get("session_name")

    created_runs = []
    for instrument_id in instrument_ids:
        symbol_row = ops_symbols.get_by_id(g.db_session, instrument_id)
        if symbol_row is None:
            return jsonify({"error": f"Unknown instrument_id: {instrument_id}"}), 404
        try:
            result = run_backtest(
                session_factory, strategy_id, symbol_row.symbol, timeframe, start, end,
                session_name=session_name, run_master_id=run_master_id,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        run_master_id = result["run_master_id"]  # every subsequent instrument joins this SAME session
        created_runs.append({
            "instrument_id": instrument_id, "symbol": symbol_row.symbol,
            "run_id": result["run_id"], "run_time_seconds": result["run_time_seconds"],
            "summary": result["summary"],
        })

    return jsonify({"run_master_id": run_master_id, "runs": created_runs}), 201


@backtests_bp.post("/api/backtests/occurrence-runs")
def create_occurrence_run():
    """Records an occurrence backtest (mode="occurrence_count") — closes a
    real gap found live 2026-09-15: the Occurrence Backtest page's own
    read API (/api/activities/occurrences) never persisted anything, so
    every occurrence-backtest view was thrown away with no history. body:
    {instrument_id, timeframe, pattern, start, end, source?, checkpoint?,
    run_master_id?, session_name?}. "all backtests should be recorded...
    you can set the source of the backtest as RS1/Strategy-abc/RS2 etc." —
    source defaults to "occurrence_backtest" when not given (see
    occurrence_backtest.py's own docstring). run_master_id chains this run
    into an EXISTING session (e.g. the page's own "Run" covering several
    patterns at once) instead of creating a new one each call."""
    body = request.get_json(silent=True) or {}
    instrument_id = body.get("instrument_id")
    timeframe = body.get("timeframe")
    pattern = body.get("pattern")
    start_s, end_s = body.get("start"), body.get("end")
    if not instrument_id or not timeframe or not pattern or not start_s or not end_s:
        return jsonify({"error": "instrument_id, timeframe, pattern, start, end are required"}), 400

    symbol_row = ops_symbols.get_by_id(g.db_session, instrument_id)
    if symbol_row is None:
        return jsonify({"error": f"Unknown instrument_id: {instrument_id}"}), 404

    existing_run_master_id = body.get("run_master_id")
    if existing_run_master_id is not None and ops_runs.get_run_master(g.db_session, existing_run_master_id) is None:
        return jsonify({"error": f"Unknown run_master_id: {existing_run_master_id}"}), 404

    start = _parse_date(start_s)
    end = _parse_date(end_s) + timedelta(days=1)
    session_factory = current_app.extensions["db_session_factory"]

    result = record_occurrence_backtest(
        session_factory, instrument_id, timeframe, pattern, start, end,
        source=body.get("source"), checkpoint=body.get("checkpoint", 20),
        run_master_id=existing_run_master_id, session_name=body.get("session_name"),
    )
    return jsonify({
        "run_master_id": result["run_master_id"], "run_id": result["run_id"],
        "summary": result["summary"],
    }), 201
