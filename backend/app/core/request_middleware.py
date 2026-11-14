"""FastAPI middleware for request ID propagation and structured request logging.

Extracts or generates a trace ID for every incoming request, binds it
to the async context via ContextVar, and logs request/response lifecycle.

Headers:
    X-Request-ID (in)  — client-provided correlation ID (optional)
    X-Request-ID (out) — echoed back in every response
"""

from __future__ import annotations

import logging
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.trace_context import (
    clear_trace_context,
    get_trace_id,
    new_trace_id,
    set_trace_context,
)

logger = logging.getLogger(__name__)

# Paths that don't need trace context (health checks, metrics, static)
_SKIP_PATHS: frozenset[str] = frozenset({"/health", "/metrics", "/favicon.ico"})
_MODEL_PROXY_PATH_PREFIX = "/admin/harness/model-proxy/"


def _path_for_log(path: str) -> str:
    if not path.startswith(_MODEL_PROXY_PATH_PREFIX):
        return path
    remainder = path[len(_MODEL_PROXY_PATH_PREFIX) :]
    suffix_start = remainder.find("/")
    suffix = remainder[suffix_start:] if suffix_start >= 0 else ""
    return f"{_MODEL_PROXY_PATH_PREFIX}<redacted>{suffix}"


class RequestTraceMiddleware(BaseHTTPMiddleware):
    """Bind trace ID to every request; log start/completion with timing.

    Lifecycle:
        1. Extract X-Request-ID or generate new trace ID
        2. Extract user_id from auth if present (best-effort)
        3. Set trace context (ContextVar) for the entire request
        4. Log request start
        5. Process request
        6. Log request completion with status and duration
        7. Clear trace context
        8. Set X-Request-ID response header
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        log_path = _path_for_log(path)

        # Skip trace setup for trivial endpoints
        if path in _SKIP_PATHS:
            return await call_next(request)

        # 1. Extract or generate trace ID
        trace_id = request.headers.get("x-request-id") or new_trace_id()

        # 2. Best-effort user ID extraction (before auth middleware runs)
        user_id: str | None = None
        # We'll set user_id later if auth middleware populates request.state

        # 3. Bind trace context
        set_trace_context(trace_id=trace_id, user_id=user_id)

        # 4. Log request start
        method = request.method
        client = request.client
        client_ip = client.host if client else "-"
        user_agent = request.headers.get("user-agent", "-")
        logger.info(
            "request.start",
            extra={
                "method": method,
                "path": log_path,
                "client_ip": client_ip,
                "user_agent": user_agent,
                "query": str(request.url.query) if request.url.query else None,
            },
        )

        # 5. Process request
        t0 = time.monotonic()
        status_code = 500
        error_detail: str | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as exc:
            error_detail = str(exc)
            raise
        finally:
            # 6. Log completion
            duration_ms = round((time.monotonic() - t0) * 1000, 1)
            log_level = logging.WARNING if status_code >= 400 else logging.INFO

            log_data: dict[str, Any] = {
                "method": method,
                "path": log_path,
                "status": status_code,
                "duration_ms": duration_ms,
            }
            if error_detail:
                log_data["error"] = error_detail

            logger.log(
                log_level,
                "request.end",
                extra=log_data,
            )

            # 7. Clear context
            clear_trace_context()

        # 8. Set response header
        response.headers["X-Request-ID"] = get_trace_id() or trace_id
        return response
