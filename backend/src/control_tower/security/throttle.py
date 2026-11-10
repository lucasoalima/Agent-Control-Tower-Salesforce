"""Login throttling.

In-memory and per-process: enough to slow password guessing against a local or
single-instance deployment. A multi-instance deployment would need a shared
store; see docs/security-model.md.
"""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class LoginThrottle:
    def __init__(
        self,
        max_failures: int = 5,
        window_seconds: float = 300.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._now = now
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str) -> deque[float]:
        entries = self._failures[key]
        cutoff = self._now() - self._window
        while entries and entries[0] <= cutoff:
            entries.popleft()
        return entries

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key)) >= self._max

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._prune(key).append(self._now())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
