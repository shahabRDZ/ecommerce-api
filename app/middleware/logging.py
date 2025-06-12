"""
Structured request/response logging middleware.

Logs method, path, status code, duration, and a correlation ID (X-Request-ID)
for every HTTP request.  Correlation IDs propagate to downstream service calls
via context-var so they appear in every log line emitted during a request.
"""
from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger("api.access")

# Context var — accessible anywhere during a request without passing explicitly
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that:
    1. Assigns a unique X-Request-ID to every incoming request.
    2. Emits a structured access log line with timing on response.
    3. Stores the request-id in a ContextVar for downstream use.
    """

    def __init__(self, app: ASGIApp, *, exclude_paths: tuple[str, ...] = ()) -> None:
        super().__init__(app)
        self.exclude_paths = exclude_paths

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # --- Short-circuit for excluded paths (health checks, metrics) --------
        if any(request.url.path.startswith(p) for p in self.exclude_paths):
            return await call_next(request)

        # --- Assign / inherit request ID --------------------------------------
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_ctx.set(req_id)

        # --- Time the request --------------------------------------------------
        start = time.perf_counter()
        status_code = 500
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
        except Exception as exc:
            logger.exception(
                "Unhandled exception during request",
                extra={
                    "request_id": req_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            request_id_ctx.reset(token)

            _level = logging.WARNING if status_code >= 400 else logging.INFO
            logger.log(
                _level,
                "%s %s %s %.2fms",
                request.method,
                request.url.path,
                status_code,
                duration_ms,
                extra={
                    "request_id": req_id,
                    "method": request.method,
                    "path": request.url.path,
                    "query": str(request.url.query),
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "client_ip": _get_client_ip(request),
                    "user_agent": request.headers.get("User-Agent", ""),
                },
            )

        response.headers["X-Request-ID"] = req_id
        response.headers["X-Response-Time"] = f"{duration_ms}ms"
        return response


def _get_client_ip(request: Request) -> str:
    """Extract real client IP, respecting common proxy headers."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
