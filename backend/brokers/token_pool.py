from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional


class TokenPool:
    """Picks whichever broker token/account is available for an account-agnostic
    REST call (e.g. a market-data quote lookup that could be served by any
    account), instead of always funneling through a single one.

    Each token gets its own independent minimum interval between uses. If every
    token is still within its cooldown, this waits out whichever one becomes
    available soonest — not a fixed queue behind one particular token.

    `tokens` can be anything hashable: raw access-token strings, account ids,
    or broker adapter instances themselves — the pool doesn't care what a
    token *is*, only how to track when each one was last used. The caller
    uses whatever `acquire()` returns to know which account/session to
    actually make the call with.
    """

    def __init__(
        self,
        tokens: List,
        min_interval_sec: float = 2.0,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        if not tokens:
            raise ValueError("TokenPool needs at least one token")
        self._min_interval = min_interval_sec
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._last_used_at: Dict = {token: 0.0 for token in tokens}

    def acquire(self):
        """Blocks until some token is available, marks it used, and returns it.

        The wait happens outside the lock, so other callers can concurrently
        check and claim a *different* available token in the meantime — the
        lock only ever guards the quick "check availability, claim it" step.
        """
        while True:
            with self._lock:
                token, remaining = self._soonest_available()
                if remaining <= 0:
                    self._last_used_at[token] = self._clock()
                    return token
            self._sleep(remaining)

    def _soonest_available(self):
        now = self._clock()
        token = min(self._last_used_at, key=lambda t: self._last_used_at[t])
        remaining = self._min_interval - (now - self._last_used_at[token])
        return token, remaining
