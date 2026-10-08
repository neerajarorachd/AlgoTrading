from datetime import datetime, timedelta, timezone

import session_scheduler
from session_scheduler import (
    CAPTURE_HOUR_UTC, CAPTURE_MINUTE_UTC, CONNECT_HOUR_UTC, CONNECT_MINUTE_UTC,
    STOP_HOUR_UTC, STOP_MINUTE_UTC, start_session_scheduler,
)


class FakeBroker:
    pass  # connect_feed/start_capture are monkeypatched away below -- these tests are about the state machine, not the real broker plumbing


class FakeMarketFeed:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class RecordingScheduler:
    """Fake schedule_fn that records (delay, callback) instead of touching a
    real threading.Timer, and `fire_all()` to invoke everything queued so
    far -- lets a test drive the connect->capture->stop->tomorrow chain
    deterministically without waiting on real time."""

    def __init__(self):
        self.calls = []

    def __call__(self, delay_seconds, callback):
        self.calls.append((delay_seconds, callback))

    def fire_next(self):
        _, callback = self.calls.pop(0)
        callback()


def _patch_phases(monkeypatch, connect_calls, capture_calls):
    monkeypatch.setattr(session_scheduler, "connect_feed", lambda broker, market_feed: connect_calls.append((broker, market_feed)))
    monkeypatch.setattr(
        session_scheduler, "start_capture",
        lambda rest_broker, market_feed, session_factory, aggregator, activity_engine=None:
            capture_calls.append((rest_broker, market_feed, session_factory, aggregator)),
    )


def _at(base: datetime, hour: int, minute: int) -> datetime:
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def test_before_connect_time_only_schedules_the_connect(monkeypatch):
    connect_calls, capture_calls = [], []
    _patch_phases(monkeypatch, connect_calls, capture_calls)
    scheduler = RecordingScheduler()
    now = _at(datetime.now(timezone.utc), CONNECT_HOUR_UTC, CONNECT_MINUTE_UTC) - timedelta(minutes=30)

    start_session_scheduler(FakeBroker(), FakeMarketFeed(), None, None, now=now, schedule_fn=scheduler)

    assert connect_calls == []
    assert capture_calls == []
    assert len(scheduler.calls) == 1  # just the 08:50 connect scheduled


def test_between_connect_and_capture_connects_now_and_schedules_capture(monkeypatch):
    connect_calls, capture_calls = [], []
    _patch_phases(monkeypatch, connect_calls, capture_calls)
    scheduler = RecordingScheduler()
    now = _at(datetime.now(timezone.utc), CONNECT_HOUR_UTC, CONNECT_MINUTE_UTC) + timedelta(minutes=5)

    start_session_scheduler(FakeBroker(), FakeMarketFeed(), None, None, now=now, schedule_fn=scheduler)

    assert len(connect_calls) == 1  # connected immediately
    assert capture_calls == []  # not yet -- that's scheduled for 09:08
    assert len(scheduler.calls) == 1


def test_mid_session_connects_and_captures_immediately_and_schedules_stop(monkeypatch):
    connect_calls, capture_calls = [], []
    _patch_phases(monkeypatch, connect_calls, capture_calls)
    scheduler = RecordingScheduler()
    now = _at(datetime.now(timezone.utc), CAPTURE_HOUR_UTC, CAPTURE_MINUTE_UTC) + timedelta(hours=3)  # mid-afternoon

    start_session_scheduler(FakeBroker(), FakeMarketFeed(), None, None, now=now, schedule_fn=scheduler)

    assert len(connect_calls) == 1
    assert len(capture_calls) == 1  # both happened immediately, matching today's pre-scheduler start_feed() behavior
    assert len(scheduler.calls) == 1  # only the 15:30 stop is scheduled


def test_after_stop_time_skips_to_tomorrows_connect(monkeypatch):
    connect_calls, capture_calls = [], []
    _patch_phases(monkeypatch, connect_calls, capture_calls)
    scheduler = RecordingScheduler()
    now = _at(datetime.now(timezone.utc), STOP_HOUR_UTC, STOP_MINUTE_UTC) + timedelta(minutes=10)

    start_session_scheduler(FakeBroker(), FakeMarketFeed(), None, None, now=now, schedule_fn=scheduler)

    assert connect_calls == []
    assert capture_calls == []
    assert len(scheduler.calls) == 1
    delay, _ = scheduler.calls[0]
    # scheduled for tomorrow's connect, not today's (already passed) -- at
    # least several hours out regardless of exactly when "now" falls
    # relative to the fixed UTC trigger constants
    assert delay > timedelta(hours=10).total_seconds()


def test_full_daily_chain_connect_capture_stop_then_tomorrow(monkeypatch):
    """Drives connect -> capture -> stop -> next day's connect by manually
    firing each recorded callback in sequence, proving the chain actually
    re-schedules itself rather than running once and stopping."""
    connect_calls, capture_calls = [], []
    _patch_phases(monkeypatch, connect_calls, capture_calls)
    scheduler = RecordingScheduler()
    market_feed = FakeMarketFeed()
    now = _at(datetime.now(timezone.utc), CONNECT_HOUR_UTC, CONNECT_MINUTE_UTC) - timedelta(minutes=30)

    start_session_scheduler(FakeBroker(), market_feed, None, None, now=now, schedule_fn=scheduler)
    assert len(scheduler.calls) == 1  # connect scheduled

    scheduler.fire_next()  # fires the 08:50 connect
    assert len(connect_calls) == 1
    assert len(scheduler.calls) == 1  # capture scheduled

    scheduler.fire_next()  # fires the 09:08 capture
    assert len(capture_calls) == 1
    assert len(scheduler.calls) == 1  # stop scheduled

    scheduler.fire_next()  # fires the 15:30 stop
    assert market_feed.stopped is True
    assert len(scheduler.calls) == 1  # tomorrow's connect scheduled
    delay, _ = scheduler.calls[0]
    assert delay > 0  # a real future delay, not immediate
