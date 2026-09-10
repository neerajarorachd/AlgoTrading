import threading
import time

import pytest

from brokers.token_pool import TokenPool


class FakeClock:
    """A controllable monotonic clock: sleep() advances it, same pattern used
    for DhanBroker's own throttle tests — keeps these deterministic instead of
    racing the real wall clock."""

    def __init__(self, start: float = 100.0):
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_single_token_pool_behaves_like_a_simple_throttle():
    clock = FakeClock()
    pool = TokenPool(["only-token"], min_interval_sec=2.0, sleep=clock.sleep, clock=clock.clock)

    assert pool.acquire() == "only-token"  # first use: no prior call, no wait
    assert pool.acquire() == "only-token"  # immediately again: must wait out the full interval

    assert clock.sleeps == [2.0]


def test_two_immediate_acquires_return_different_tokens_without_waiting():
    clock = FakeClock()
    pool = TokenPool(["A", "B"], min_interval_sec=2.0, sleep=clock.sleep, clock=clock.clock)

    first = pool.acquire()
    second = pool.acquire()

    assert {first, second} == {"A", "B"}
    assert clock.sleeps == []  # both tokens were fresh, neither call had to wait


def test_third_acquire_waits_for_whichever_token_frees_up_first():
    clock = FakeClock()
    pool = TokenPool(["A", "B"], min_interval_sec=2.0, sleep=clock.sleep, clock=clock.clock)

    pool.acquire()
    pool.acquire()
    third = pool.acquire()  # both tokens just used "simultaneously" -> must wait the full interval once

    assert third in ("A", "B")
    assert clock.sleeps == [2.0]


def test_no_wait_once_enough_time_has_already_passed():
    clock = FakeClock()
    pool = TokenPool(["only-token"], min_interval_sec=2.0, sleep=clock.sleep, clock=clock.clock)

    pool.acquire()
    clock.now += 2.5  # simulate other work happening between calls
    pool.acquire()

    assert clock.sleeps == []


def test_pool_requires_at_least_one_token():
    with pytest.raises(ValueError):
        TokenPool([])


def test_concurrent_callers_get_different_tokens_without_serializing():
    """Proves the lock is released during the wait, not just that a pool of 2
    can serve 2 immediate callers (which would pass even if waits were
    serialized). Pre-claim both tokens so both threads are forced to wait out
    the same cooldown — if the lock were (incorrectly) held across the sleep,
    thread 2 would queue behind thread 1's entire wait too, taking ~2x the
    interval; releasing it properly lets both wait out their own cooldown
    concurrently, finishing in ~1x."""
    pool = TokenPool(["A", "B"], min_interval_sec=0.2)
    pool.acquire()
    pool.acquire()  # both tokens are now freshly "used" — any further acquire() must wait

    acquired = []
    lock = threading.Lock()

    def worker():
        token = pool.acquire()
        with lock:
            acquired.append(token)

    start = time.monotonic()
    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)
    elapsed = time.monotonic() - start

    assert set(acquired) == {"A", "B"}
    assert elapsed < 0.35  # ~1x the 0.2s interval if parallel; would be ~0.4s+ if serialized
