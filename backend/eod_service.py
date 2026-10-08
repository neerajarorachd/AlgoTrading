"""End-of-day catch-up + weekly-rotated backtest pass — runs once daily,
shortly after session_scheduler.py's 15:30 IST stop. Two responsibilities:

  1. Fetch any still-missing historical candle data for every actively-
     watched instrument (reuses feed/gap_fill.py's own idempotent
     backfill_missing_candles directly, NOT the existing gap scanner,
     which is gated to skip outside market hours and would do nothing
     right at/after close).
  2. Run backtests, in-process (same synchronous convention
     api/routes_backtests.py's POST /api/backtests/runs already uses — no
     background job), for whichever Strategy rows land on today's weekday
     in a 7-day rotation, against every active instrument, on each of
     1min/3min/5min, writing results into the existing BacktestRunMaster/
     BacktestRun/BacktestRunResult tables via scripts/run_backtest.py.

Assumptions, carried over from planning rather than silently re-decided
here (see memory: the market-session-scheduler/EOD-service design):
  - No Strategy-to-instrument linkage exists in the schema yet (Watchlist's
    own docstring says this is deliberately deferred) — every rotated
    strategy is backtested against EVERY active SubscribedSymbol.
  - Rotation key is `strategy.id % 7` (Monday=0..Sunday=6, matching
    datetime.weekday()) — a DB-id artifact, not a deliberate per-strategy
    day assignment; revisit if/when a real schedule field is added to
    Strategy.
  - `strategy_type == "recommendation_parent"` rows are containers (see
    db/ops/LibRecommendationSystems.ensure_parent_strategies), never real
    backtestable strategies — excluded from the rotation pool.
  - Backtest window is each symbol's full available history, floored at
    ~2 years back — not a short trailing window. Combined with the 7-day
    rotation, each strategy's full-history result is refreshed about once
    a week, not daily.
  - Daily-timeframe backtesting is explicitly out of scope ("we will plan
    daily backtesting separately") — no 1-day candle fetch or
    daily-timeframe run is part of this pass.
"""
from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from db.ops.LibStrategies import get_all as get_all_strategies
from db.ops.LibSymbols import get_active as get_active_symbols
from db.session import session_scope
from feed.gap_fill import backfill_missing_candles

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from run_backtest import run_backtest  # noqa: E402

logger = logging.getLogger(__name__)

TIMEFRAMES: Tuple[str, ...] = ("1min", "3min", "5min")
SOURCE = "EOD-rotation"
BACKTEST_LOOKBACK_DAYS = 730  # ~2 years — the practical ceiling of real candle history

# 15:30 IST = 10:00 UTC, same as session_scheduler.STOP_HOUR_UTC/MINUTE_UTC
# and feed/bootstrap.py's default flush time — kept as its own constant
# here (not imported) since eod_service.py deliberately has zero
# cross-import with session_scheduler.py (see module docstring / plan: the
# two run on independent timers, not a shared callback).
_DEFAULT_HOUR_UTC = 10
_DEFAULT_MINUTE_UTC = 0
_DEFAULT_DELAY_SECONDS = 30  # defensive offset after session_scheduler's own stop, not a correctness requirement

ScheduleFn = Callable[[float, Callable[[], None]], None]


def _default_schedule_fn(delay_seconds: float, callback: Callable[[], None]) -> None:
    timer = threading.Timer(max(0.0, delay_seconds), callback)
    timer.daemon = True
    timer.start()


def strategies_for_today(session, today: Optional[datetime] = None) -> List:
    """Active, non-container strategies whose id lands on today's weekday
    in the 7-day rotation — see module docstring for the rotation-key
    assumption."""
    today = today or datetime.now(timezone.utc)
    weekday = today.weekday()
    return [
        s for s in get_all_strategies(session)
        if s.strategy_type != "recommendation_parent" and s.id % 7 == weekday
    ]


def run_eod_cycle(session_factory, rest_broker, aggregator, now: Optional[datetime] = None) -> dict:
    """One full EOD pass: backfill every active symbol, then backtest
    today's rotated strategies against every active symbol on every
    timeframe. One bad symbol/strategy/timeframe is logged and skipped,
    never aborts the rest — same discipline as feed/bootstrap.py's
    _hydrate and scenario_scheduler.py's regeneration pass."""
    now = now or datetime.now(timezone.utc)

    with session_scope(session_factory) as session:
        symbol_targets = [
            (row.symbol, row.exchange_segment, row.security_id)
            for row in get_active_symbols(session)
        ]
        strategies = [(s.id, s.name) for s in strategies_for_today(session, now)]

    logger.info("EOD: backfilling %d active symbol(s)", len(symbol_targets))
    backfill_errors: List[str] = []
    for symbol, exchange_segment, security_id in symbol_targets:
        try:
            backfill_missing_candles(symbol, exchange_segment, security_id, rest_broker, session_factory, aggregator)
        except Exception:
            logger.exception("EOD backfill failed for %s (%s)", symbol, exchange_segment)
            backfill_errors.append(symbol)

    logger.info(
        "EOD: %d strategy(ies) rotated today, backtesting against %d active symbol(s) x %d timeframe(s)",
        len(strategies), len(symbol_targets), len(TIMEFRAMES),
    )
    start = now - timedelta(days=BACKTEST_LOOKBACK_DAYS)
    backtests: List[dict] = []
    backtest_errors: List[Tuple[int, str, str]] = []
    for strategy_id, strategy_name in strategies:
        for timeframe in TIMEFRAMES:
            run_master_id = None  # reused across symbols so one session groups them under one master, same pattern as routes_backtests.py's watchlist expansion
            for symbol, exchange_segment, security_id in symbol_targets:
                try:
                    result = run_backtest(
                        session_factory, strategy_id, symbol, timeframe, start, now,
                        session_name=f"EOD {now.date()} / {strategy_name} / {timeframe}",
                        run_master_id=run_master_id, source=SOURCE,
                    )
                    run_master_id = result["run_master_id"]
                    backtests.append({"strategy_id": strategy_id, "symbol": symbol, "timeframe": timeframe, **result})
                except Exception:
                    logger.exception(
                        "EOD backtest failed: strategy=%s (%d) symbol=%s timeframe=%s",
                        strategy_name, strategy_id, symbol, timeframe,
                    )
                    backtest_errors.append((strategy_id, symbol, timeframe))

    return {
        "symbols_backfilled": len(symbol_targets), "backfill_errors": backfill_errors,
        "strategies_run_today": [name for _, name in strategies],
        "backtests_completed": len(backtests), "backtest_errors": backtest_errors,
    }


def start_eod_scheduler(
    session_factory, rest_broker, aggregator,
    hour_utc: int = _DEFAULT_HOUR_UTC, minute_utc: int = _DEFAULT_MINUTE_UTC,
    delay_seconds: int = _DEFAULT_DELAY_SECONDS, schedule_fn: Optional[ScheduleFn] = None,
) -> None:
    """Self-rescheduling daily timer, same shape as feed/bootstrap.py's
    _schedule_daily_flush. Call once at app startup."""
    schedule_fn = schedule_fn or _default_schedule_fn

    def _fire_and_reschedule() -> None:
        try:
            run_eod_cycle(session_factory, rest_broker, aggregator)
        except Exception:
            logger.exception("EOD scheduler: unexpected error during a cycle")
        finally:
            start_eod_scheduler(session_factory, rest_broker, aggregator, hour_utc, minute_utc, delay_seconds, schedule_fn)

    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=minute_utc, second=0, microsecond=0) + timedelta(seconds=delay_seconds)
    if target <= now:
        target += timedelta(days=1)
    schedule_fn(max(0.0, (target - now).total_seconds()), _fire_and_reschedule)
