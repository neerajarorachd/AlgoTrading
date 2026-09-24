"""Records an "occurrence backtest" (BacktestRunMaster.mode="occurrence_count")
— counting how a pattern's historical occurrences actually played out,
never simulating a trade — as a real, persistent BacktestRun, closing a gap
found live 2026-09-15: the Occurrence Backtest page's own API
(/api/activities/occurrences) was a pure read query straight against
LibPatternOutcomes, with no BacktestRunMaster/BacktestRun ever created for
it, even though BacktestRunMaster.mode has documented "occurrence_count"
support since the table was first built. Every occurrence-backtest view
was computed fresh and thrown away — no history, nothing to revisit later,
nothing showing up next to strategy_trade runs.

Deliberately a thin orchestration layer over TWO existing db/ops modules
(LibBacktestRuns for the run-tracking tables, LibPatternOutcomes for the
actual analysis) — same shape as recommendation_engine.py/
pattern_outcome_analysis.py, which is why this lives here and not inside
either db/ops module itself (db/ops stays single-table/single-concern).

BacktestRunResult's own columns are trade-shaped (total_trades/wins/
losses/win_ratio/pnl) — reused here with occurrence-count semantics
instead of adding a parallel results table: total_trades = total analyzed
occurrences, wins/losses = matched/unmatched at the qualifying checkpoint,
win_ratio = that checkpoint's win%, every pnl field left at 0 (occurrence
counting has no capital/P&L concept — matches BacktestRunMaster.mode's own
docstring, "no trade simulation"). This is a snapshot for the run-history
list, not the sole source of truth: the FULL multi-checkpoint/intensity/
indicator-state breakdown is always available live by re-querying
LibPatternOutcomes with this run's own recorded instrument/timeframe/
pattern/date range — PatternOutcome data persists independently of this
run record.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from db.ops import LibBacktestRuns, LibPatternOutcomes


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def record_occurrence_backtest(
    session_factory, instrument_id: int, timeframe: str, pattern: str,
    date_from: datetime, date_to: datetime,
    source: Optional[str] = None, checkpoint: int = 20,
    run_master_id: Optional[int] = None, session_name: Optional[str] = None,
) -> dict:
    """Runs LibPatternOutcomes' own analysis (pattern_level_analysis, the
    same un-banded overall stat recommendation_engine's PRIMARY stage
    falls back to) over [date_from, date_to] and persists the result as a
    BacktestRun + BacktestRunResult, exactly parallel to how
    run_backtest.py persists a strategy_trade run. source defaults to
    "occurrence_backtest" when not given (2026-09-15: "you can set the
    source of the backtest as RS1/Strategy-abc/RS2 etc."). Returns
    {"run_master_id", "run_id", "summary"} — summary is the same {"count",
    "checkpoints", ...} shape LibPatternOutcomes._band_stats produces, not
    a re-derived subset, so a caller gets the full multi-checkpoint detail
    immediately without a second query.

    run_master_id: attach this run to an EXISTING session instead of
    creating a new one — same convention as run_backtest.py's own
    run_master_id, used when one "Run" click on the Occurrence Backtest
    page covers several patterns (or timeframes/instruments): the first
    pattern's call creates the session, every subsequent pattern's call
    passes that id back in so they all land under ONE BacktestRunMaster,
    not one each."""
    started_at = _utcnow()
    created_new_master = run_master_id is None
    with session_factory() as session:
        stats = LibPatternOutcomes.pattern_level_analysis(
            session, instrument_id, [timeframe], date_from, date_to, pattern,
        )
        total = stats["count"] if stats is not None else 0
        checkpoint_stats = stats["checkpoints"].get(checkpoint) if stats is not None else None
        wins = checkpoint_stats["matched"] if checkpoint_stats else 0
        win_pct = checkpoint_stats["pct"] if checkpoint_stats else None

        if created_new_master:
            run_master_id = LibBacktestRuns.create_run_master(session, {
                "name": session_name or f"{pattern} occurrences / instrument {instrument_id} / {timeframe}",
                "mode": "occurrence_count", "source": source or "occurrence_backtest",
                "instrument_id": instrument_id,
                "date_from": date_from, "date_to": date_to,
                "status": "running", "started_at": started_at,
            })
        run_id = LibBacktestRuns.create_run(session, {
            "run_master_id": run_master_id, "instrument_id": instrument_id,
            "pattern": pattern, "timeframe": timeframe,
            "status": "running", "started_at": started_at,
        })
        session.flush()

        LibBacktestRuns.upsert_run_result(session, run_id, {
            "total_trades": total, "wins": wins, "losses": total - wins,
            "win_ratio": win_pct,
            "total_gross_pnl": 0, "total_expenses": 0, "total_net_pnl": 0,
            "winning_days": 0, "losing_days": 0, "flat_days": 0, "days_with_trades": 0,
        })
        ended_at = _utcnow()
        elapsed = round((ended_at - started_at).total_seconds(), 2)
        LibBacktestRuns.update_run_fields(session, run_id, {
            "status": "completed", "ended_at": ended_at, "time_elapsed_seconds": elapsed,
        })
        # Re-scan every run under this session (not just this one) for
        # run_count, same reasoning as run_backtest.py's own sibling-rescan.
        sibling_count = len(LibBacktestRuns.get_runs_for_master(session, run_master_id))
        LibBacktestRuns.update_run_master_fields(session, run_master_id, {
            "status": "completed", "ended_at": ended_at, "time_elapsed_seconds": elapsed,
            "run_count": sibling_count,
        })
        session.commit()

    return {"run_master_id": run_master_id, "run_id": run_id, "summary": stats}
