from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import defaultdict
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.config import settings

logger = logging.getLogger("uvicorn.error")
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


class MetricsRegistry:
    def __init__(self) -> None:
        self.started_at = time.time()
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._latency_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._latency_count: dict[tuple[str, str], int] = defaultdict(int)
        self._lock = threading.Lock()

    def observe(self, method: str, route: str, status: int, duration: float) -> None:
        with self._lock:
            self._requests[(method, route, status)] += 1
            self._latency_sum[(method, route)] += duration
            self._latency_count[(method, route)] += 1

    def render(self, jobs: dict[str, int]) -> str:
        with self._lock:
            lines = [
                "# HELP shoppilot_uptime_seconds Process uptime in seconds.",
                "# TYPE shoppilot_uptime_seconds gauge",
                f"shoppilot_uptime_seconds {time.time() - self.started_at:.3f}",
                "# HELP shoppilot_http_requests_total HTTP requests by route and status.",
                "# TYPE shoppilot_http_requests_total counter",
            ]
            for (method, route, status), total in sorted(self._requests.items()):
                labels = _labels(method=method, route=route, status=str(status))
                lines.append(f"shoppilot_http_requests_total{{{labels}}} {total}")
            lines.extend([
                "# HELP shoppilot_http_request_duration_seconds_sum Total request duration.",
                "# TYPE shoppilot_http_request_duration_seconds_sum counter",
            ])
            for (method, route), duration in sorted(self._latency_sum.items()):
                labels = _labels(method=method, route=route)
                lines.append(
                    f"shoppilot_http_request_duration_seconds_sum{{{labels}}} {duration:.6f}"
                )
                lines.append(
                    f"shoppilot_http_request_duration_seconds_count{{{labels}}} "
                    f"{self._latency_count[(method, route)]}"
                )
            lines.extend([
                "# HELP shoppilot_jobs Current durable job queue depth by status.",
                "# TYPE shoppilot_jobs gauge",
            ])
            for status, total in sorted(jobs.items()):
                lines.append(f'shoppilot_jobs{{status="{_escape(status)}"}} {total}')
        return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(**values: str) -> str:
    return ",".join(f'{key}="{_escape(value)}"' for key, value in values.items())


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", "")
        if not SAFE_REQUEST_ID.fullmatch(request_id):
            request_id = str(uuid4())
        started = time.perf_counter()
        status = 500
        try:
            protected_write = request.method not in {"GET", "HEAD", "OPTIONS"} and (
                request.url.path.startswith("/api/auth/")
                or request.url.path.startswith("/api/admin/")
                or request.url.path.startswith("/api/invitations/")
                or request.url.path.startswith("/api/shops/")
            )
            origin = request.headers.get("origin")
            allowed = settings.public_base_url.rstrip("/")
            if settings.app_env != "production":
                allowed = str(request.base_url).rstrip("/")
            cross_origin = origin is not None and (
                urlparse(origin).scheme, urlparse(origin).netloc
            ) != (urlparse(allowed).scheme, urlparse(allowed).netloc)
            if protected_write and (
                cross_origin or request.headers.get("sec-fetch-site") == "cross-site"
            ):
                response = JSONResponse({"detail": "Nguồn yêu cầu không được phép."}, status_code=403)
            else:
                response = await call_next(request)
            status = response.status_code
            if request.url.path.startswith("/api/") or request.url.path in {"/login", "/metrics"}:
                response.headers["Cache-Control"] = "no-store"
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            if request.url.path == "/static/widget.html":
                # The customer chat is intentionally embedded on third-party shop sites.
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; base-uri 'self'; frame-ancestors *; "
                    "form-action 'self'; img-src 'self' data:; "
                    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
                )
            else:
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
                    "form-action 'self'; img-src 'self' data:; "
                    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
                )
            if settings.app_env == "production":
                response.headers["Strict-Transport-Security"] = (
                    "max-age=31536000; includeSubDomains"
                )
            return response
        finally:
            duration = time.perf_counter() - started
            route_object = request.scope.get("route")
            # Never retain arbitrary paths or tokens as metrics labels or access logs.
            route = getattr(route_object, "path", "/unmatched")
            metrics.observe(request.method, route, status, duration)
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": request.method,
                        "route": route,
                        "status": status,
                        "duration_ms": round(duration * 1000, 2),
                    },
                    ensure_ascii=False,
                )
            )


metrics = MetricsRegistry()
