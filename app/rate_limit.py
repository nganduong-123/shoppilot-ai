from __future__ import annotations

import hashlib
import math
import time

from fastapi import HTTPException, Request

from app.database import db_session


class PersistentRateLimiter:
    """Database-backed fixed-window limiter shared by every app instance."""

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

        timestamp = time.time() if now is None else now
        window_start = int(timestamp // window_seconds) * window_seconds
        expires_at = window_start + (window_seconds * 2)
        identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()

        with db_session() as connection:
            connection.execute(
                "DELETE FROM rate_limit_buckets WHERE expires_at <= ?",
                (int(timestamp),),
            )
            connection.execute(
                """
                INSERT INTO rate_limit_buckets
                    (scope, identity_hash, window_start, request_count, expires_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(scope, identity_hash, window_start)
                DO UPDATE SET request_count = rate_limit_buckets.request_count + 1
                """,
                (scope, identity_hash, window_start, expires_at),
            )
            row = connection.execute(
                """
                SELECT request_count FROM rate_limit_buckets
                WHERE scope = ? AND identity_hash = ? AND window_start = ?
                """,
                (scope, identity_hash, window_start),
            ).fetchone()

        if row and row["request_count"] > limit:
            return max(1, math.ceil(window_start + window_seconds - timestamp))
        return None

    def reset(self) -> None:
        with db_session() as connection:
            connection.execute("DELETE FROM rate_limit_buckets")


rate_limiter = PersistentRateLimiter()


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
