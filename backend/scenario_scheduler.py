"""Weekly regeneration of guiding scenarios — see guiding_scenarios.py's own
docstring for why no scheduler lives there itself. This is the piece that
actually runs it periodically, following this project's own established
in-process-timer convention (feed/gap_fill.py's start_gap_scanner,
feed/bootstrap.py's _schedule_daily_flush) rather than any external cron/
Task Scheduler/systemd unit — lightweight polling over standing
infrastructure is this project's real convention (in-process timers,
auto-started when the app runs), not just its stated aspiration.

Without this, guiding_scenarios.match_guiding_scenario keeps comparing live
signals against whatever scenarios were generated once, manually, and never
refreshed — a real, silent staleness risk this closes.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from db.ops.LibSymbols import get_active
from db.session import session_scope
from guiding_scenarios import WINDOW_LOOKBACK_DAYS, generate_guiding_scenarios

logger = logging.getLogger(__name__)

TIMEFRAMES: Tuple[str, ...] = ("1min", "3min", "5min")

# Sunday 02:00 UTC (~7:30 IST) — well before market open (09:15 IST), a
# quiet window with no live trading activity to contend with. Monday=0 ...
# Sunday=6, matching datetime.weekday()'s own convention.
DEFAULT_DAY_OF_WEEK = 6
DEFAULT_HOUR_UTC = 2
DEFAULT_MINUTE_UTC = 0


def _next_weekly_target(now: datetime, day_of_week: int, hour_utc: int, minute_utc: int) -> datetime:
    """The next datetime >= now matching day_of_week/hour_utc/minute_utc —
    same "compute a concrete wall-clock target, not just a fixed interval"
    approach as feed/bootstrap.py's _schedule_daily_flush, extended from
    daily to weekly."""
    candidate = now.replace(hour=hour_utc, minute=minute_utc, second=0, microsecond=0)
    days_ahead = (day_of_week - candidate.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def run_guiding_scenario_regeneration(session_factory) -> Dict[str, object]:
    """One full regeneration pass — every active SubscribedSymbol x
    TIMEFRAMES x WINDOW_LOOKBACK_DAYS, reusing the project's own "always
    fetch the full timeframe set" convention. Callable directly (a manual
    run, a test, or the scheduler below) — this function itself has no
    timing logic. One symbol/timeframe/window failing (e.g. no PatternOutcome
    data yet for a newly-registered symbol) is logged and skipped, never
    aborts the rest of the pass.

    Returns {"scenarios_written", "indicator_stats_written", "symbols_scanned",
    "errors": [(symbol, timeframe, window_kind), ...]}."""
    with session_scope(session_factory) as session:
        targets: List[Tuple[int, str]] = [(row.id, row.symbol) for row in get_active(session)]

    total_scenarios = total_indicator_stats = 0
    errors: List[Tuple[str, str, str]] = []
    for instrument_id, symbol in targets:
        for timeframe in TIMEFRAMES:
            for window_kind in WINDOW_LOOKBACK_DAYS:
                try:
                    result = generate_guiding_scenarios(session_factory, instrument_id, timeframe, window_kind)
                except Exception:
                    logger.exception(
                        "guiding-scenario regeneration failed for %s %s [%s]", symbol, timeframe, window_kind,
                    )
                    errors.append((symbol, timeframe, window_kind))
                    continue
                total_scenarios += result["scenarios_written"]
                total_indicator_stats += result["indicator_stats_written"]

    logger.info(
        "Weekly guiding-scenario regeneration: %d scenario(s), %d indicator-stat row(s), "
        "%d error(s) across %d symbol(s)",
        total_scenarios, total_indicator_stats, len(errors), len(targets),
    )
    return {
        "scenarios_written": total_scenarios, "indicator_stats_written": total_indicator_stats,
        "symbols_scanned": len(targets), "errors": errors,
    }


def start_guiding_scenario_scheduler(
    session_factory, day_of_week: int = DEFAULT_DAY_OF_WEEK,
    hour_utc: int = DEFAULT_HOUR_UTC, minute_utc: int = DEFAULT_MINUTE_UTC,
) -> None:
    """Self-rescheduling weekly timer — same try/except/finally-reschedule
    discipline as feed/gap_fill.py's start_gap_scanner (one bad cycle must
    never silently end the recurring job), targeting a concrete next
    wall-clock datetime like feed/bootstrap.py's _schedule_daily_flush,
    extended from daily to a specific weekday. Call once at app startup;
    the first fire is scheduled for the next real occurrence of
    day_of_week/hour_utc/minute_utc, not immediately."""
    def _schedule_next() -> None:
        now = datetime.now(timezone.utc)
        target = _next_weekly_target(now, day_of_week, hour_utc, minute_utc)
        timer = threading.Timer((target - now).total_seconds(), _tick)
        timer.daemon = True
        timer.start()

    def _tick() -> None:
        try:
            run_guiding_scenario_regeneration(session_factory)
        except Exception:
            logger.exception("Guiding-scenario scheduler: unexpected error during a regeneration cycle")
        finally:
            _schedule_next()

    _schedule_next()
