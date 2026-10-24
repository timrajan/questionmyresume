"""In-memory, per-IP sliding-window rate limiting.

State lives in this process, so limits reset on restart/redeploy and aren't shared
across replicas. That's fine for a single Railway instance.
"""

import logging
import math
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

MAX_TRACKED_IPS = 10_000

logger = logging.getLogger("uvicorn.error")


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int, action: str):
        self.limit = limit  # 0 disables the limiter
        self.window = window_seconds
        self.action = action  # used in the error message, e.g. "resume analyses"
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        """Record a hit for `key`, or raise 429 if it's over the limit."""
        if self.limit <= 0:
            return
        now = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()

        if len(hits) >= self.limit:
            retry_after = math.ceil(hits[0] + self.window - now)
            logger.warning("Rate limit hit: %s (%s)", key, self.action)
            minutes = max(1, math.ceil(retry_after / 60))
            raise HTTPException(
                429,
                {
                    "message": f"You've reached the limit of {self.limit} {self.action} per hour. "
                    f"Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
                    "code": "rate_limited",
                },
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)
        if len(self._hits) > MAX_TRACKED_IPS:
            self._prune(now)

    def _prune(self, now: float) -> None:
        for key in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.window]:
            del self._hits[key]


def client_ip(request: Request) -> str:
    """The caller's IP, as seen by our hosting proxy.

    Railway's edge sets X-Real-IP to the visitor's address and overwrites any value the
    visitor sends, so it can't be forged. (X-Forwarded-For isn't usable: its last entry is
    a rotating Railway edge IP.) Without a proxy, e.g. locally, use the socket address.
    """
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"
