"""In-process sliding-window rate limiter (single-instance deployments).

Used to keep the unauthenticated browser error-report endpoint from being
used to flood the database. A multi-instance deployment would move this to
the edge (nginx `limit_req`) or Redis.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory under key churn
                self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v})
            return True
