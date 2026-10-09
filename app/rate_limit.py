from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request


class InMemoryRateLimiter:
    """Small, thread-safe sliding-window limiter for a single app instance."""

    def __init__(self, max_identities: int = 10_000) -> None:
        self._events: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
        self._max_identities = max_identities
        self._lock = threading.Lock()

    def check(
        self,
        scope: str,
        identity: str,
        limit: int,
        window_seconds: int,
        *,
        now: float | None = None,
    ) -> int | None:
        """Record one request and return retry seconds when the window is full."""
        if limit <= 0 or window_seconds <= 0:
            return None

        timestamp = time.monotonic() if now is None else now
        cutoff = timestamp - window_seconds
        key = (scope, identity)

        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self._max_identities:
                    self._events.popitem(last=False)
                events = deque()
                self._events[key] = events
            else:
                self._events.move_to_end(key)
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return max(1, math.ceil(events[0] + window_seconds - timestamp))
            events.append(timestamp)
            return None

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = InMemoryRateLimiter()


def client_identity(request: Request) -> str:
    """Use the client address already normalized by the ASGI server/proxy middleware."""
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(
    request: Request,
    scope: str,
    limit: int,
    window_seconds: int,
    *,
    enabled: bool,
) -> None:
    if not enabled:
        return
    retry_after = rate_limiter.check(
        scope,
        client_identity(request),
        limit,
        window_seconds,
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Bạn gửi yêu cầu quá nhanh. Vui lòng thử lại sau.",
            headers={"Retry-After": str(retry_after)},
        )
