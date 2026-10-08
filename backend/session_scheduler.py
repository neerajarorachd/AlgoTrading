"""Market-hours lifecycle for the live WS feed — connect at 08:50 IST, start
capturing (hydrate + subscribe every active instrument) at 09:08 IST, stop
(disconnect) at 15:30 IST, repeat daily. Same in-process self-rescheduling
`threading.Timer` convention as every other scheduler in this codebase
(scenario_scheduler.py, feed/gap_fill.py's start_gap_scanner,
feed/bootstrap.py's _schedule_daily_flush) — not cron/Task Scheduler/
systemd; see scenario_scheduler.py's own docstring for why that's this
project's deliberate choice, not an oversight.

Before this existed, feed.bootstrap.start_feed() connected and started
capturing the instant the process started, with no scheduled stop at all —
fine for an always-on dev process, wrong for a market-hours-scoped session.

Designed to be started at ANY wall-clock time — a dev restarting the
backend mid-afternoon, or right at 15:35 after close — by figuring out
which of four phases "now" falls into and acting accordingly instead of
assuming it's always started before 08:50.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from feed.bootstrap import connect_feed, start_capture

logger = logging.getLogger(__name__)

# IST has no DST, always UTC+5:30 — same UTC-throughout convention as
# feed/bootstrap.py's flush timer and feed/gap_fill.py's market-hours gate.
# 08:50 IST = 03:20 UTC / 09:08 IST = 03:38 UTC / 15:30 IST = 10:00 UTC
CONNECT_HOUR_UTC = 3
CONNECT_MINUTE_UTC = 20
CAPTURE_HOUR_UTC = 3
CAPTURE_MINUTE_UTC = 38
STOP_HOUR_UTC = 10
STOP_MINUTE_UTC = 0

ScheduleFn = Callable[[float, Callable[[], None]], None]


def _default_schedule_fn(delay_seconds: float, callback: Callable[[], None]) -> None:
    timer = threading.Timer(max(0.0, delay_seconds), callback)
    timer.daemon = True
    timer.start()


def _at(base: datetime, hour: int, minute: int) -> datetime:
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _delay_from_now(target: datetime) -> float:
    return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())


class _Session:
    """Bundles everything every phase transition needs, so each
    _schedule_*/_run_* helper takes one argument instead of eight — avoids
    the class of bug where one leg of a long parameter chain accidentally
    drops/loses a value (e.g. session_factory) on a later reschedule."""

    def __init__(self, broker, market_feed, session_factory, aggregator,
                 rest_broker, activity_engine, schedule_fn: ScheduleFn):
        self.broker = broker
        self.market_feed = market_feed
        self.session_factory = session_factory
        self.aggregator = aggregator
        self.rest_broker = rest_broker
        self.activity_engine = activity_engine
        self.schedule_fn = schedule_fn


def start_session_scheduler(
    broker, market_feed, session_factory, aggregator,
    rest_broker=None, activity_engine=None,
    now: Optional[datetime] = None, schedule_fn: Optional[ScheduleFn] = None,
) -> None:
    """Call once at app startup — replaces the old unconditional
    feed.bootstrap.start_feed() call. Figures out which of four phases
    "now" falls into and acts immediately for that phase, then schedules
    every future transition via a self-rescheduling timer:

      before 08:50   -> do nothing now, schedule the 08:50 connect
      08:50-09:08     -> connect now, schedule the 09:08 capture
      09:08-15:30     -> connect AND capture immediately (today's old
                         start_feed() behavior, e.g. starting mid-session
                         during dev/testing), schedule only the 15:30 stop
      after 15:30     -> already closed for today, skip to tomorrow 08:50

    `now`/`schedule_fn` are injectable purely for tests (a fixed instant,
    and a fake that doesn't actually wait on a real timer) — real callers
    never pass them.
    """
    sess = _Session(
        broker, market_feed, session_factory, aggregator,
        rest_broker if rest_broker is not None else broker, activity_engine,
        schedule_fn or _default_schedule_fn,
    )
    now = now or datetime.now(timezone.utc)

    connect_today = _at(now, CONNECT_HOUR_UTC, CONNECT_MINUTE_UTC)
    capture_today = _at(now, CAPTURE_HOUR_UTC, CAPTURE_MINUTE_UTC)
    stop_today = _at(now, STOP_HOUR_UTC, STOP_MINUTE_UTC)

    if now < connect_today:
        _schedule_connect(sess, connect_today)
    elif now < capture_today:
        _run_connect_phase(sess, now)
    elif now < stop_today:
        logger.info("session_scheduler: starting mid-session (now=%s UTC) — connecting and capturing immediately", now)
        _run_mid_session_start(sess, now)
    else:
        _schedule_connect(sess, connect_today + timedelta(days=1))


def _run_connect_phase(sess: "_Session", now: datetime) -> None:
    try:
        connect_feed(sess.broker, sess.market_feed)
    except Exception:
        logger.exception("session_scheduler: connect_feed failed at 08:50 IST")
    _schedule_capture(sess, _at(now, CAPTURE_HOUR_UTC, CAPTURE_MINUTE_UTC))


def _run_mid_session_start(sess: "_Session", now: datetime) -> None:
    try:
        connect_feed(sess.broker, sess.market_feed)
        start_capture(
            sess.rest_broker, sess.market_feed, sess.session_factory, sess.aggregator,
            activity_engine=sess.activity_engine,
        )
    except Exception:
        logger.exception("session_scheduler: mid-session start failed")
    _schedule_stop(sess, _at(now, STOP_HOUR_UTC, STOP_MINUTE_UTC))


def _schedule_connect(sess: "_Session", target: datetime) -> None:
    def _fire() -> None:
        _run_connect_phase(sess, datetime.now(timezone.utc))
    sess.schedule_fn(_delay_from_now(target), _fire)


def _schedule_capture(sess: "_Session", target: datetime) -> None:
    def _fire() -> None:
        try:
            start_capture(
                sess.rest_broker, sess.market_feed, sess.session_factory, sess.aggregator,
                activity_engine=sess.activity_engine,
            )
        except Exception:
            logger.exception("session_scheduler: start_capture failed at 09:08 IST")
        _schedule_stop(sess, _at(datetime.now(timezone.utc), STOP_HOUR_UTC, STOP_MINUTE_UTC))
    sess.schedule_fn(_delay_from_now(target), _fire)


def _schedule_stop(sess: "_Session", target: datetime) -> None:
    def _fire() -> None:
        try:
            sess.market_feed.stop()
        except Exception:
            logger.exception("session_scheduler: market_feed.stop() failed at 15:30 IST")
        tomorrow_connect = _at(datetime.now(timezone.utc), CONNECT_HOUR_UTC, CONNECT_MINUTE_UTC) + timedelta(days=1)
        _schedule_connect(sess, tomorrow_connect)
    sess.schedule_fn(_delay_from_now(target), _fire)
