from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.chat_control import ChatControlOperation


_USAGE_TEXT_FIELDS = frozenset(
    {
        "provider",
        "model",
        "answer_provider",
        "answer_model",
        "answer_endpoint_id",
        "vision_provider",
        "vision_model",
        "vision_endpoint_id",
        "status",
        "error_code",
        "price_version_id",
        "currency",
        "route_mode",
    }
)
_USAGE_INT_FIELDS = frozenset(
    {
        "input_tokens",
        "input_uncached_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
    }
)
_USAGE_CALL_FIELDS = _USAGE_TEXT_FIELDS | _USAGE_INT_FIELDS | {
    "purpose",
    "endpoint_id",
    "cost",
}
_USAGE_DEFAULT_INT_FIELDS = (
    "input_tokens",
    "input_uncached_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens",
    "total_tokens",
    "latency_ms",
)


def _safe_text(value: object, *, limit: int = 256) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_call(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    call: dict[str, Any] = {}
    for key in _USAGE_CALL_FIELDS:
        if key not in raw:
            continue
        if key in _USAGE_INT_FIELDS:
            call[key] = _safe_int(raw.get(key))
        elif key == "cost":
            value = _safe_text(raw.get(key), limit=64)
            call[key] = value or "0"
        else:
            call[key] = _safe_text(raw.get(key))
    for key in _USAGE_DEFAULT_INT_FIELDS:
        call.setdefault(key, 0)
    call.setdefault("cost", "0")
    call.setdefault("latency_ms", 0)
    call.setdefault("status", "completed")
    call.setdefault("error_code", None)
    call.setdefault("price_version_id", None)
    call.setdefault("currency", None)
    return call


def _safe_usage(
    usage: dict[str, Any] | None,
    *,
    status: str,
    error_code: str | None,
) -> dict[str, Any]:
    """Keep only route/usage metadata; never persist control or visual bodies."""
    raw = usage if isinstance(usage, dict) else {}
    safe: dict[str, Any] = {}
    for key in _USAGE_TEXT_FIELDS:
        if key in raw:
            safe[key] = _safe_text(raw.get(key))
    for key in _USAGE_INT_FIELDS:
        if key in raw:
            safe[key] = _safe_int(raw.get(key))
    if "cost" in raw:
        safe["cost"] = _safe_text(raw.get("cost"), limit=64) or "0"
    if "vision_fallback_used" in raw:
        safe["vision_fallback_used"] = bool(raw.get("vision_fallback_used"))
    calls = raw.get("calls")
    if isinstance(calls, list):
        safe_calls = [_safe_call(item) for item in calls[:8]]
        safe["calls"] = [item for item in safe_calls if item is not None]
    safe["status"] = status
    safe["error_code"] = _safe_text(error_code or raw.get("error_code"))
    safe.setdefault("provider", "pi")
    safe.setdefault("model", None)
    safe.setdefault("answer_provider", safe.get("provider"))
    safe.setdefault("answer_model", safe.get("model"))
    safe.setdefault("answer_endpoint_id", None)
    safe.setdefault("vision_provider", None)
    safe.setdefault("vision_model", None)
    safe.setdefault("vision_endpoint_id", None)
    safe.setdefault("price_version_id", None)
    safe.setdefault("currency", None)
    safe.setdefault("cost", "0")
    safe.setdefault("latency_ms", 0)
    for key in _USAGE_DEFAULT_INT_FIELDS:
        safe.setdefault(key, 0)
    safe.setdefault("route_mode", None)
    safe.setdefault("vision_fallback_used", False)
    safe.setdefault("calls", [])
    return safe


def record_control_operation(
    db: Session,
    *,
    owner_user_id: str,
    session_id: str,
    action: str,
    status: str,
    usage: dict[str, Any],
    error_code: str | None = None,
) -> ChatControlOperation:
    """Persist one control command's privacy-safe route and usage trace."""
    if action not in {"steer", "follow_up"}:
        raise ValueError("unsupported chat control action")
    if status not in {"completed", "failed"}:
        raise ValueError("unsupported chat control status")
    safe_usage = _safe_usage(usage, status=status, error_code=error_code)
    operation = ChatControlOperation(
        id=f"cco_{new_token(10)}",
        owner_user_id=owner_user_id,
        session_id=session_id,
        action=action,
        status=status,
        error_code=_safe_text(error_code),
        usage=json.dumps(
            safe_usage,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        created_at=now(),
        completed_at=now(),
    )
    db.add(operation)
    db.flush()
    return operation
