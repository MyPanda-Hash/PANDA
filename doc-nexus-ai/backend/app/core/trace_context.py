"""Request-scoped trace context for correlation ID propagation.

Uses ContextVar so trace_id is automatically scoped to each async task
and propagated through synchronous call chains within the same context.

Usage in middleware / entry points:
    from app.core.trace_context import set_trace_context, get_trace_id
    set_trace_context(trace_id="req_abc123", user_id="usr_xyz")

Usage anywhere in the call stack:
    from app.core.trace_context import get_trace_id, get_user_id
    trace_id = get_trace_id()  # returns "req_abc123" or None
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Optional

_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_id", default=None
)
_user_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "user_id", default=None
)
_span_name_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "span_name", default=None
)


def new_trace_id() -> str:
    """Generate a compact trace ID: 'req_' + 12-char hex."""
    return f"req_{uuid.uuid4().hex[:12]}"


def get_trace_id() -> str | None:
    """Return current trace ID or None if outside request context."""
    return _trace_id_var.get()


def get_user_id() -> str | None:
    """Return current user ID or None."""
    return _user_id_var.get()


def get_span_name() -> str | None:
    """Return current span name (e.g. 'qa.generate', 'llm.call')."""
    return _span_name_var.get()


def set_trace_context(
    *,
    trace_id: str | None = None,
    user_id: str | None = None,
    span_name: str | None = None,
) -> None:
    """Set trace context variables for the current async task / thread.

    Called by middleware at request entry; safe to call again mid-request
    to update span_name or user_id.
    """
    if trace_id is not None:
        _trace_id_var.set(trace_id)
    if user_id is not None:
        _user_id_var.set(user_id)
    if span_name is not None:
        _span_name_var.set(span_name)


def clear_trace_context() -> None:
    """Reset all context variables (called at request exit)."""
    _trace_id_var.set(None)
    _user_id_var.set(None)
    _span_name_var.set(None)


def trace_context_snapshot() -> dict[str, str | None]:
    """Return a dict of current context for embedding into log records."""
    return {
        "trace_id": get_trace_id(),
        "user_id": get_user_id(),
        "span": get_span_name(),
    }
