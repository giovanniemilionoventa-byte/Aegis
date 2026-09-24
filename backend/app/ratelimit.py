"""Phase 20 -- rate limiting for the endpoints anyone can reach.

Login and registration need no credential, so they are where guessing and
signup abuse happen. This is a sliding window per client address, kept in
process memory and enforced before any work is done.

WHAT IT IS NOT. Each worker counts on its own, so N workers allow N times the
limit, and a restart forgets everything. That is enough to stop a script from
hammering the password check; the real ceiling belongs to the edge (Cloudflare
or the reverse proxy), which sees every worker.
ponytail: shared store (Redis or a table) only if a single process stops being enough.
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Optional

from fastapi import HTTPException, Request

from . import config

_PRUNE_ABOVE = 10_000


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = {}
        self._lock = Lock()

    def allow(
        self, key: str, limit: int, window: float = 60.0, now: Optional[float] = None
    ) -> bool:
        """Record a hit and return True, or return False when over the limit."""
        stamp = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= stamp - window:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(stamp)
            if len(self._hits) > _PRUNE_ABOVE:
                self._prune(stamp, window)
            return True

    def _prune(self, stamp: float, window: float) -> None:
        stale = [
            key
            for key, hits in self._hits.items()
            if not hits or hits[-1] <= stamp - window
        ]
        for key in stale:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


RATE = SlidingWindow()


def enforce(request: Request, bucket: str) -> None:
    """Raise 429 when this client address is over its limit for `bucket`."""
    limit = config.AUTH_RATE_PER_MIN
    if limit <= 0:
        return
    host = request.client.host if request.client else "unknown"
    if not RATE.allow(f"{bucket}:{host}", limit):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Try again in a minute.",
            headers={"Retry-After": "60"},
        )
