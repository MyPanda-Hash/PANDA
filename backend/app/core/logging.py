"""Centralized logging configuration for Doc Nexus AI.

Provides structured logging with:
- Timestamp (ISO 8601 with ms)
- Log level with label prefix
- Trace ID correlation (auto-injected from ContextVar)
- User ID correlation
- Span name for operation tracking
- Module/function source location
- Exception tracebacks with full chain

Production: JSON output for log aggregation (ELK, Loki, etc.)
Development: Human-readable colored output for terminal

Zero new dependencies — stdlib logging + ContextVar only.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

from app.core.trace_context import get_trace_id, get_user_id, get_span_name

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Level → short label prefix (aligns with common dashboard conventions)
_LEVEL_LABELS: dict[int, str] = {
    logging.DEBUG: "DBG",
    logging.INFO: "INFO",
    logging.WARNING: "WARN",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRIT",
}

# ANSI colors for terminal output
_COLORS: dict[int, str] = {
    logging.DEBUG: "\033[36m",      # cyan
    logging.INFO: "\033[32m",       # green
    logging.WARNING: "\033[33m",    # yellow
    logging.ERROR: "\033[31m",      # red
    logging.CRITICAL: "\033[1;31m", # bold red
}
_RESET = "\033[0m"
_DIM = "\033[2m"

# Modules to suppress noisy third-party logs in production
_QUIET_MODULES: frozenset[str] = frozenset({
    "httpx",
    "httpcore",
    "urllib3",
    "sqlalchemy.engine",
    "uvicorn.access",
    "multipart",
})


# ---------------------------------------------------------------------------
# Custom LogRecord extras
# ---------------------------------------------------------------------------

class TraceContextFilter(logging.Filter):
    """Inject trace_id, user_id, span from ContextVar into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id() or "-"  # type: ignore[attr-defined]
        record.user_id = get_user_id() or "-"  # type: ignore[attr-defined]
        record.span = get_span_name() or "-"  # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """Structured JSON log line — one JSON object per line.

    Fields:
        ts       — ISO 8601 timestamp with milliseconds
        level    — log level (INFO, ERROR, etc.)
        label    — short label (INFO, ERR, WARN, DBG, CRIT)
        logger   — logger name (module path)
        msg      — log message
        trace_id — request correlation ID
        user_id  — authenticated user ID
        span     — current operation span name
        file     — source filename
        line     — source line number
        func     — function name
        exc      — exception info (if present)
        stack    — stack info (if present)
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).astimezone().isoformat(
            timespec="milliseconds"
        )
        entry: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "label": _LEVEL_LABELS.get(record.levelno, "?"),
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "span": getattr(record, "span", "-"),
        }

        # Source location (skip if already in logger name)
        if record.pathname:
            entry["file"] = record.pathname
            entry["line"] = record.lineno
            entry["func"] = record.funcName

        # Structured extra fields (anything set via logger.info("...", extra={...}))
        _EXTRA_KEYS = frozenset({
            "ts", "level", "label", "logger", "msg", "trace_id", "user_id",
            "span", "file", "line", "func", "exc_info", "stack_info",
            "name", "message", "args", "levelname", "levelno", "pathname",
            "filename", "module", "funcName", "lineno", "created", "msecs",
            "relativeCreated", "thread", "threadName", "processName", "process",
            "exc_text", "stack_trace",
        })
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _EXTRA_KEYS and not k.startswith("_")
        }
        if extras:
            entry["extra"] = extras

        # Exception
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack"] = record.stack_info

        return json.dumps(entry, ensure_ascii=False, default=str)


class HumanReadableFormatter(logging.Formatter):
    """Colored terminal output for development.

    Format:
        2026-08-28T10:30:15.123 [INFO ] [req_abc123] [app.services.qa] QA generation started user=usr_xyz

    Color-coded level, dimmed trace/user IDs, highlighted errors.
    """

    def __init__(self) -> None:
        super().__init__(fmt="%(message)s", datefmt=None)

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).astimezone().strftime(
            "%Y-%m-%dT%H:%M:%S."
        ) + f"{int(record.msecs):03d}"
        level = record.levelname
        label = _LEVEL_LABELS.get(record.levelno, "?")
        color = _COLORS.get(record.levelno, "")
        reset = _RESET if color else ""
        dim = _DIM if color else ""

        trace_id = getattr(record, "trace_id", "-")
        user_id = getattr(record, "user_id", "-")
        span = getattr(record, "span", "-")
        logger_name = record.name

        # Build header
        parts = [
            f"{color}{ts} [{label:>5}]{reset}",
        ]
        if trace_id and trace_id != "-":
            parts.append(f"{dim}[{trace_id}]{reset}")
        if user_id and user_id != "-":
            parts.append(f"{dim}[user={user_id}]{reset}")
        if span and span != "-":
            parts.append(f"{color}[{span}]{reset}")
        parts.append(f"{dim}[{logger_name}]{reset}")

        header = " ".join(parts)
        msg = record.getMessage()

        # Structured extras as key=value
        _EXTRA_KEYS = frozenset({
            "ts", "level", "label", "logger", "msg", "trace_id", "user_id",
            "span", "file", "line", "func", "exc_info", "stack_info",
            "name", "message", "args", "levelname", "levelno", "pathname",
            "filename", "module", "funcName", "lineno", "created", "msecs",
            "relativeCreated", "thread", "threadName", "processName", "process",
            "exc_text", "stack_trace",
        })
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _EXTRA_KEYS and not k.startswith("_")
        }
        extra_str = ""
        if extras:
            extra_str = " " + " ".join(f"{k}={v!r}" for k, v in extras.items())

        result = f"{header} {msg}{extra_str}"

        # Exception
        if record.exc_info and record.exc_info[0] is not None:
            exc_text = self.formatException(record.exc_info)
            result += f"\n{color}{exc_text}{reset}"

        return result


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _is_prod() -> bool:
    env = os.environ.get("DOC_NEXUS_ENVIRONMENT", "").strip().lower()
    return env in {"production", "prod"}


def _effective_level() -> int:
    level_str = os.environ.get("DOC_NEXUS_LOG_LEVEL", "").strip().upper()
    if level_str and hasattr(logging, level_str):
        return getattr(logging, level_str)
    return logging.DEBUG if not _is_prod() else logging.INFO


def setup_logging() -> None:
    """Initialize application logging.

    Call once during app startup (before any logger is used).
    Safe to call multiple times (idempotent).
    """
    root = logging.getLogger()
    level = _effective_level()
    root.setLevel(level)

    # Remove existing handlers (uvicorn adds its own)
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    # Add trace context filter to root
    trace_filter = TraceContextFilter()

    # Create handler
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(trace_filter)

    if _is_prod():
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(HumanReadableFormatter())

    root.addHandler(handler)

    # Quiet noisy third-party loggers
    for mod_name in _QUIET_MODULES:
        logging.getLogger(mod_name).setLevel(logging.WARNING)

    # Ensure our app loggers are at DEBUG (root level controls filtering)
    logging.getLogger("app").setLevel(level)

    log = logging.getLogger(__name__)
    log.info(
        "logging initialized: level=%s format=%s",
        logging.getLevelName(level),
        "json" if _is_prod() else "human",
    )


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name.

    Convenience wrapper — same as logging.getLogger(name) but
    guarantees trace context filter is applied.
    """
    return logging.getLogger(name)
