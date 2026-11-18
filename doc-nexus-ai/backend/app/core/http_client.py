"""Instrumented HTTP client with trace ID propagation and request/response logging.

Wraps httpx to automatically:
- Propagate X-Request-ID to outgoing requests
- Log request start/end with timing, status, and error details
- Capture third-party error response bodies for debugging
- Filter sensitive headers from logs

Usage:
    from app.core.http_client import get_http_client, log_http_call

    async with get_http_client() as client:
        response = await client.post(url, json=payload)
        # or
        response = await log_http_call(
            "POST", url, client=client, json=payload
        )
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.core.trace_context import get_trace_id

logger = logging.getLogger(__name__)

# Headers to redact in logs
_SENSITIVE_HEADERS: frozenset[str] = frozenset({
    "authorization",
    "x-api-key",
    "api-key",
    "cookie",
    "set-cookie",
})

# Max bytes of error response body to capture
_MAX_ERROR_BODY_BYTES = 2048


def _redact_headers(headers: httpx.Headers) -> dict[str, str]:
    """Redact sensitive headers for logging."""
    result: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _SENSITIVE_HEADERS:
            result[key] = "***"
        else:
            result[key] = value
    return result


def _safe_error_body(response: httpx.Response) -> str | None:
    """Try to extract error response body (truncated)."""
    try:
        body = response.text[:_MAX_ERROR_BODY_BYTES]
        return body if body else None
    except Exception:
        return None


async def log_http_call(
    method: str,
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    _logger: logging.Logger | None = None,
    _span: str | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Make an HTTP call with full logging.

    Logs request start, response status/timing, and error details.
    Automatically injects X-Request-ID header.

    Parameters
    ----------
    method : str
        HTTP method (GET, POST, etc.)
    url : str
        Target URL
    client : httpx.AsyncClient | None
        Client instance; creates temporary one if None
    _logger : Logger | None
        Logger to use; defaults to module logger
    _span : str | None
        Span name for trace context (e.g. "linkmind.api_key.create")
    **kwargs
        Passed to client.request()
    """
    log = _logger or logger
    trace_id = get_trace_id()

    # Inject trace ID into outgoing headers
    headers = kwargs.pop("headers", None) or {}
    if trace_id and "x-request-id" not in {k.lower() for k in headers}:
        headers["x-request-id"] = trace_id
    kwargs["headers"] = headers

    # Build safe log URL (strip query params for non-debug)
    safe_url = url.split("?")[0] if "?" in url else url

    log.info(
        "http.request",
        extra={
            "http_method": method,
            "url": safe_url,
            "span": _span or f"http.{method.lower()}",
            **({"body_size": len(kwargs.get("json", {}))} if "json" in kwargs else {}),
        },
    )

    t0 = time.monotonic()
    response: httpx.Response | None = None
    error_detail: str | None = None

    try:
        if client:
            response = await client.request(method, url, **kwargs)
        else:
            async with httpx.AsyncClient(timeout=kwargs.pop("timeout", 60.0)) as temp:
                response = await temp.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        error_detail = f"timeout: {exc}"
        raise
    except httpx.HTTPError as exc:
        error_detail = f"http_error: {exc}"
        raise
    finally:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)

        if response is not None:
            status = response.status_code
            level = logging.WARNING if status >= 400 else logging.INFO
            log_data: dict[str, Any] = {
                "http_method": method,
                "url": safe_url,
                "status": status,
                "duration_ms": duration_ms,
                "span": _span or f"http.{method.lower()}",
            }
            if status >= 400:
                error_body = _safe_error_body(response)
                if error_body:
                    log_data["error_body"] = error_body
            log.log(level, "http.response", extra=log_data)
        else:
            log.error(
                "http.response",
                extra={
                    "http_method": method,
                    "url": safe_url,
                    "duration_ms": duration_ms,
                    "error": error_detail,
                    "span": _span or f"http.{method.lower()}",
                },
            )

    return response


def sync_log_http_call(
    method: str,
    url: str,
    *,
    client: httpx.Client | None = None,
    _logger: logging.Logger | None = None,
    _span: str | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Synchronous version of log_http_call for non-async contexts."""
    log = _logger or logger
    trace_id = get_trace_id()

    headers = kwargs.pop("headers", None) or {}
    if trace_id and "x-request-id" not in {k.lower() for k in headers}:
        headers["x-request-id"] = trace_id
    kwargs["headers"] = headers

    safe_url = url.split("?")[0] if "?" in url else url

    log.info(
        "http.request",
        extra={
            "http_method": method,
            "url": safe_url,
            "span": _span or f"http.{method.lower()}",
        },
    )

    t0 = time.monotonic()
    response: httpx.Response | None = None
    error_detail: str | None = None

    try:
        if client:
            response = client.request(method, url, **kwargs)
        else:
            with httpx.Client(timeout=kwargs.pop("timeout", 60.0)) as temp:
                response = temp.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        error_detail = f"timeout: {exc}"
        raise
    except httpx.HTTPError as exc:
        error_detail = f"http_error: {exc}"
        raise
    finally:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)

        if response is not None:
            status = response.status_code
            level = logging.WARNING if status >= 400 else logging.INFO
            log_data_sync: dict[str, Any] = {
                "http_method": method,
                "url": safe_url,
                "status": status,
                "duration_ms": duration_ms,
                "span": _span or f"http.{method.lower()}",
            }
            if status >= 400:
                error_body = _safe_error_body(response)
                if error_body:
                    log_data_sync["error_body"] = error_body
            log.log(level, "http.response", extra=log_data_sync)
        else:
            log.error(
                "http.response",
                extra={
                    "http_method": method,
                    "url": safe_url,
                    "duration_ms": duration_ms,
                    "error": error_detail,
                    "span": _span or f"http.{method.lower()}",
                },
            )

    return response


@asynccontextmanager
async def get_http_client(**kwargs: Any):
    """Context manager for an instrumented httpx.AsyncClient.

    Automatically propagates trace ID headers.

    Usage:
        async with get_http_client(timeout=30) as client:
            resp = await client.get(url)
    """
    trace_id = get_trace_id()
    headers = kwargs.pop("headers", None) or {}
    if trace_id:
        headers.setdefault("x-request-id", trace_id)
    async with httpx.AsyncClient(headers=headers, **kwargs) as client:
        yield client
