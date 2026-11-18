"""Pi RPC event redaction and compatibility mapping."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.user_messages import (
    canonical_chat_error_metadata,
    is_sensitive_key,
    redact_error_text,
    redact_inline_secrets as _redact_inline_secrets,
    stable_chat_error_code,
    trusted_chat_error_message,
)

_TOOL_EVENT_TYPES = frozenset(
    {"tool_execution_start", "tool_execution_update", "tool_execution_end"}
)
_STREAM_PEM_BEGIN_RE = re.compile(
    r"-----BEGIN [^\r\n-]*PRIVATE KEY-----", re.IGNORECASE
)
_STREAM_PEM_END_RE = re.compile(
    r"-----END [^\r\n-]*PRIVATE KEY-----", re.IGNORECASE
)
_STREAM_DANGLING_ASSIGNMENT_RE = re.compile(
    r"(?ix)(?<![a-z0-9_.])"
    r"(?P<key>[a-z_][a-z0-9_.-]*)[ \t]*[:=][ \t]*$"
)


def _is_text_leaf(value: Any) -> bool:
    return isinstance(value, str) or (
        isinstance(value, dict) and isinstance(value.get("text"), str)
    )


def _text_leaf_value(value: Any) -> str:
    return value if isinstance(value, str) else str(value.get("text") or "")


def _redact_text_leaf(value: Any, text: str) -> Any:
    if isinstance(value, str):
        return text
    return {
        str(key): (
            "[REDACTED]"
            if is_sensitive_key(key)
            else (text if key == "text" else redact_pi_payload(item))
        )
        for key, item in value.items()
    }


def _redact_text_sequence(values: list[Any]) -> list[Any]:
    redacted: list[Any] = []
    index = 0
    while index < len(values):
        if not _is_text_leaf(values[index]):
            redacted.append(redact_pi_payload(values[index]))
            index += 1
            continue
        leaves: list[Any] = []
        while index < len(values) and _is_text_leaf(values[index]):
            leaves.append(values[index])
            index += 1
        combined = "".join(_text_leaf_value(leaf) for leaf in leaves)
        safe = _redact_inline_secrets(combined)
        if safe == combined:
            redacted.extend(
                _redact_text_leaf(leaf, _text_leaf_value(leaf)) for leaf in leaves
            )
            continue
        redacted.append(_redact_text_leaf(leaves[0], safe))
        redacted.extend(
            _redact_text_leaf(leaf, "") for leaf in leaves[1:]
        )
    return redacted


def redact_pi_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if is_sensitive_key(key)
                else redact_pi_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return _redact_text_sequence(value)
    if isinstance(value, str):
        return _redact_inline_secrets(value)
    return value


def _stream_safe_boundary(value: str) -> int:
    boundary = value.rfind("\n")
    if boundary < 0:
        return -1
    line_start = 0
    while line_start <= boundary:
        line_end = value.find("\n", line_start, boundary + 1)
        if line_end < 0:
            break
        line = value[line_start:line_end].rstrip("\r")
        match = _STREAM_DANGLING_ASSIGNMENT_RE.search(line)
        if match and is_sensitive_key(match.group("key")):
            continuation_end = value.find("\n", line_end + 1)
            return line_start if continuation_end < 0 else continuation_end + 1
        line_start = line_end + 1
    return boundary + 1


class PiEventStreamRedactor:
    """Buffer deltas until a safe boundary is available for redaction.

    A credential assignment can be split across two runtime deltas, so a
    stateless call for each delta cannot guarantee that either SSE or stored
    timeline output is safe.  The redactor keeps independent buffers for
    content and thinking channels, emits complete lines when possible, and
    flushes any final fragment through the shared redactor at stream end.
    """

    def __init__(self) -> None:
        self._buffers: dict[str, str] = {}

    def feed(self, chunk: str, *, channel: str = "content") -> str:
        if not chunk:
            return ""
        buffered = self._buffers.get(channel, "") + chunk
        self._buffers[channel] = buffered

        pem_begin = _STREAM_PEM_BEGIN_RE.search(buffered)
        if pem_begin:
            pem_end = _STREAM_PEM_END_RE.search(buffered, pem_begin.end())
            if pem_end is None:
                safe_prefix = buffered[: pem_begin.start()]
                self._buffers[channel] = buffered[pem_begin.start() :]
                return _redact_inline_secrets(safe_prefix)
            boundary = buffered.find("\n", pem_end.end())
            if boundary < 0:
                return ""
        else:
            safe_boundary = _stream_safe_boundary(buffered)
            if safe_boundary < 0:
                return ""
            boundary = safe_boundary - 1

        safe_text = buffered[: boundary + 1]
        self._buffers[channel] = buffered[boundary + 1 :]
        return _redact_inline_secrets(safe_text)

    def finish(self, *, channel: str = "content") -> str:
        buffered = self._buffers.pop(channel, "")
        return _redact_inline_secrets(buffered)

    def finish_all(self) -> dict[str, str]:
        return {
            channel: self.finish(channel=channel)
            for channel in tuple(self._buffers)
        }


_PERSIST_MAX_PARTS = 40
_PERSIST_MAX_ARGS_CHARS = 2_000
_PERSIST_MAX_OUTPUT_CHARS = 4_000
_PERSIST_MAX_THINKING_CHARS = 8_000
_PERSIST_MAX_CONTENT_CHARS = 100_000
_INTERRUPTED_TOOL_RESULT = "执行中断，未收到工具最终结果。"
_MISSING_TOOL_RESULT = "已生成工具调用，但未收到工具执行结果。"
_FAILED_TOOL_RESULT = trusted_chat_error_message("", error_kind="tool")

_MODEL_PROVIDER_ERROR_CODES = (
    "quota_exhausted",
    "rate_limited",
    "request_too_large",
    "provider_unavailable",
    "auth_invalid",
    "content_filtered",
    "model_failed",
    "model_unavailable",
)
MODEL_PROVIDER_ERROR_MESSAGES = {
    code: trusted_chat_error_message(code, error_kind="model")
    for code in _MODEL_PROVIDER_ERROR_CODES
}
NON_RETRYABLE_MODEL_PROVIDER_ERROR_CODES = frozenset(
    {"quota_exhausted", "request_too_large", "auth_invalid", "content_filtered", "model_failed", "model_unavailable"}
)
_MODEL_PROVIDER_ERROR_MARKERS = (
    (
        "model_unavailable",
        (
            "model_unavailable",
            "model_not_found",
            "modelnotfound",
            "model not found",
            "model not exist",
            "model does not exist",
            "model doesn't exist",
            "model is not available",
        ),
    ),
    (
        "content_filtered",
        (
            "content_filtered",
            "content_filter",
            "content_policy_violation",
            "content policy violation",
            "inappropriate content",
            "data_inspection_failed",
            "datainspectionfailed",
        ),
    ),
    (
        "quota_exhausted",
        (
            "insufficient_quota",
            "exceeded your current quota",
            "quota exhausted",
            "billing details",
            "余额不足",
            "额度不足",
        ),
    ),
    (
        "request_too_large",
        (
            "context_length_exceeded",
            "maximum context length",
            "max context length",
            "too many input tokens",
            "request too large",
            "上下文长度",
        ),
    ),
    (
        "auth_invalid",
        (
            "invalid_api_key",
            "invalid api key",
            "authentication_error",
            "authentication failed",
            "unauthorized",
            "status 401",
            "http 401",
        ),
    ),
    (
        "rate_limited",
        (
            "rate_limit_exceeded",
            "rate limit",
            "tokens per minute",
            "requests per minute",
            "too many requests",
            "tpm limit",
            "rpm limit",
            "限流",
        ),
    ),
    (
        "provider_unavailable",
        (
            "service unavailable",
            "temporarily unavailable",
            "server overloaded",
            "bad gateway",
            "gateway timeout",
            "status 502",
            "status 503",
            "status 504",
            "http 502",
            "http 503",
            "http 504",
        ),
    ),
)
_MODEL_PROVIDER_RETRY_AFTER_RE = re.compile(
    r"(?ix)(?:retry[_ -]?after|try\s+again\s+in|retry\s+in)"
    r"[^0-9]{0,24}(?P<value>\d+(?:\.\d+)?)"
    r"\s*(?P<unit>milliseconds?|ms|seconds?|secs?|s|minutes?|mins?|m|秒|分钟)?"
)
_MODEL_PROVIDER_HTTP_429_RE = re.compile(
    r"(?:^|\b(?:http|status|error|code)\s*[:=_-]?\s*)429\b",
    re.IGNORECASE,
)


def model_provider_retry_after_seconds(value: Any) -> int | None:
    """Extract a bounded provider cooldown from an exception or message."""
    seen: set[int] = set()
    current: Any = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidates = [
            getattr(current, "retry_after_seconds", None),
            getattr(current, "retry_after", None),
            current,
        ]
        for candidate in candidates:
            if isinstance(candidate, (int, float)) and not isinstance(
                candidate, bool
            ):
                seconds = float(candidate)
            else:
                match = _MODEL_PROVIDER_RETRY_AFTER_RE.search(
                    str(candidate or "")
                )
                if match is None:
                    continue
                seconds = float(match.group("value"))
                unit = (match.group("unit") or "seconds").lower()
                if unit in {"millisecond", "milliseconds", "ms"}:
                    seconds /= 1_000
                elif unit in {
                    "minute",
                    "minutes",
                    "min",
                    "mins",
                    "m",
                    "分钟",
                }:
                    seconds *= 60
            if seconds <= 0:
                continue
            rounded = int(seconds)
            if seconds > rounded:
                rounded += 1
            return max(1, min(3_600, rounded))
        current = getattr(current, "__cause__", None)
    return None




def classify_model_provider_error(value: Any) -> str:
    """Map provider-specific failures to stable chat recovery categories."""
    normalized = str(value or "").strip().lower()
    for code, markers in _MODEL_PROVIDER_ERROR_MARKERS:
        if any(marker in normalized for marker in markers):
            return code
    if re.search(
        r"\bmodel(?:\s+['\"`]?[\w./:-]+['\"`]?)?\s+"
        r"(?:does not exist|doesn't exist|not found|is not available)\b",
        normalized,
    ):
        return "model_unavailable"
    if _MODEL_PROVIDER_HTTP_429_RE.search(normalized):
        return "rate_limited"
    return "model_failed"


def model_provider_error_retryable(code: str) -> bool:
    return (
        code in MODEL_PROVIDER_ERROR_MESSAGES
        and code not in NON_RETRYABLE_MODEL_PROVIDER_ERROR_CODES
    )




def _clip_text(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    value = _redact_inline_secrets(value)
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n…(已截断)"


def _error_code_from_part(part: dict[str, Any]) -> str:
    code, _metadata = canonical_chat_error_metadata(
        part.get("metadata_text") or part.get("metadataText"),
        error_code=part.get("error_code") or part.get("errorCode"),
    )
    return code


def _safe_error_metadata_text(
    part: dict[str, Any],
    *,
    error_code: str,
) -> str | None:
    _code, metadata = canonical_chat_error_metadata(
        part.get("metadata_text") or part.get("metadataText"),
        error_code=error_code,
    )
    return metadata


def chat_error_part_to_sse_item(
    part: dict[str, Any], *, agent_name: str
) -> dict[str, Any] | None:
    if part.get("type") != "error":
        return None
    error_kind = str(part.get("error_kind") or "runtime")
    if error_kind not in _ERROR_KIND_PRIORITY:
        error_kind = "runtime"
    error_id = _clip_text(
        _redact_inline_secrets(str(part.get("error_id") or "runtime-error")),
        128,
    )
    error_code = _error_code_from_part(part)
    content: dict[str, Any] = {
        "type": "chat_error",
        "errorId": error_id or "runtime-error",
        "errorKind": error_kind,
        "text": trusted_chat_error_message(error_code, error_kind=error_kind),
    }
    metadata_text = _safe_error_metadata_text(part, error_code=error_code)
    if metadata_text:
        content["metadataText"] = metadata_text
    if error_code:
        content["errorCode"] = error_code
    if isinstance(part.get("retryable"), bool):
        content["retryable"] = part["retryable"]
    if isinstance(part.get("progress_preserved"), bool):
        content["progressPreserved"] = part["progress_preserved"]
    retry_after_seconds = part.get("retry_after_seconds")
    if (
        isinstance(retry_after_seconds, int)
        and not isinstance(retry_after_seconds, bool)
        and retry_after_seconds > 0
    ):
        content["retryAfterSeconds"] = retry_after_seconds
    if isinstance(part.get("auto_retry_scheduled"), bool):
        content["autoRetryScheduled"] = part["auto_retry_scheduled"]
    return {
        "type": "PiRuntimeEvent",
        "source": agent_name,
        "content": content,
    }


def _safe_error_event_payload(event: dict[str, Any]) -> dict[str, Any]:
    event_type = (
        "extension_error" if event.get("type") == "extension_error" else "error"
    )
    error_kind = "extension" if event_type == "extension_error" else "runtime"
    content: dict[str, Any] = {
        "type": event_type,
        "error": trusted_chat_error_message("", error_kind=error_kind),
    }
    for key in ("toolCallId", "toolName"):
        value = event.get(key)
        if value in (None, ""):
            continue
        content[key] = _clip_text(
            _redact_inline_secrets(str(value)),
            128,
        )
    if code := stable_chat_error_code(event.get("code")):
        content["code"] = code
    return content


def _format_tool_args(value: Any) -> str | None:
    value = redact_pi_payload(value)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        command = value.get("command")
        if isinstance(command, str) and command.strip():
            return command
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _format_tool_result(value: Any) -> str | None:
    value = redact_pi_payload(value)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text = "".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
            if text:
                return text
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def safe_error_text(
    value: Any,
    *,
    fallback: str = "Pi Runtime 执行失败。",
    limit: int = _PERSIST_MAX_OUTPUT_CHARS,
) -> str:
    if isinstance(value, BaseException):
        formatted = str(value)
    else:
        formatted = _format_tool_result(value)
    return redact_error_text(formatted, fallback=fallback, limit=limit)


def _merge_output_text(existing: Any, detail: str | None) -> str | None:
    current = str(existing or "").strip()
    addition = str(detail or "").strip()
    if not addition or addition in current:
        return current or None
    return _clip_text(
        f"{current}\n{addition}" if current else addition,
        _PERSIST_MAX_OUTPUT_CHARS,
    )


_ERROR_KIND_PRIORITY = {"runtime": 0, "extension": 1, "model": 2}

_CONTROLLED_STOP_ERROR_CODES = (
    "chat_token_budget_exceeded",
    "chat_request_budget_exceeded",
    "chat_token_usage_unavailable",
)
_CONTROLLED_STOP_ERROR_CODE_RE = re.compile(
    r"(?<![a-z0-9_])("
    + "|".join(re.escape(code) for code in _CONTROLLED_STOP_ERROR_CODES)
    + r")(?![a-z0-9_])",
    re.IGNORECASE,
)


def _controlled_stop_error_code(*values: Any) -> str | None:
    source = "\n".join(str(value or "") for value in values)
    match = _CONTROLLED_STOP_ERROR_CODE_RE.search(source)
    return match.group(1).lower() if match else None


def _upsert_error_part(
    parts: list[dict[str, Any]],
    *,
    error_id: str,
    error_kind: str,
    detail: str,
    metadata_text: str | None = None,
    error_code: str | None = None,
    retryable: bool | None = None,
    progress_preserved: bool | None = None,
    retry_after_seconds: int | None = None,
    auto_retry_scheduled: bool | None = None,
) -> None:
    index = next(
        (
            index
            for index, part in enumerate(parts)
            if part.get("type") == "error"
            and (
                part.get("error_id") == error_id
                or str(detail or "") in str(part.get("text") or "")
                or str(detail or "") in str(part.get("metadata_text") or "")
            )
        ),
        None,
    )
    existing = parts[index] if index is not None else {}
    existing_kind = str(existing.get("error_kind") or "runtime")
    preferred_kind = (
        error_kind
        if _ERROR_KIND_PRIORITY.get(error_kind, 0)
        >= _ERROR_KIND_PRIORITY.get(existing_kind, 0)
        else existing_kind
    )
    preserve_structured_detail = (
        existing.get("error_code") in MODEL_PROVIDER_ERROR_MESSAGES
        and error_code is None
    )
    part: dict[str, Any] = {
        "type": "error",
        "error_id": (
            error_id
            if preferred_kind == error_kind
            else str(existing.get("error_id") or error_id)
        )[:128],
        "error_kind": preferred_kind,
        "text": (
            str(existing.get("text") or "")
            if preserve_structured_detail
            else _merge_output_text(existing.get("text"), detail)
        )
        or "Pi Runtime 执行失败。",
    }
    if merged_metadata := _merge_output_text(
        existing.get("metadata_text"), metadata_text
    ):
        part["metadata_text"] = _clip_text(merged_metadata, _PERSIST_MAX_ARGS_CHARS)
    structured_fields = {
        "error_code": error_code,
        "retryable": retryable,
        "progress_preserved": progress_preserved,
        "retry_after_seconds": retry_after_seconds,
        "auto_retry_scheduled": auto_retry_scheduled,
    }
    for key, value in structured_fields.items():
        resolved = value if value is not None else existing.get(key)
        if resolved is not None:
            part[key] = resolved
    if index is None:
        parts.append(part)
    else:
        parts[index] = part


def _append_text_part(parts: list[dict[str, Any]], kind: str, chunk: str) -> None:
    chunk = _redact_inline_secrets(chunk)
    if not chunk:
        return
    if parts and parts[-1].get("type") == kind:
        parts[-1]["text"] = _redact_inline_secrets(
            str(parts[-1].get("text") or "") + chunk
        )
        return
    parts.append({"type": kind, "text": chunk})


def _upsert_tool_part(parts: list[dict[str, Any]], event: dict[str, Any]) -> None:
    tool_call_id = _redact_inline_secrets(
        str(event.get("toolCallId") or "")
    ).strip()
    if not tool_call_id:
        return
    tool_name = (
        _redact_inline_secrets(str(event.get("toolName") or "tool")).strip()
        or "tool"
    )
    index = next(
        (
            index
            for index, part in enumerate(parts)
            if part.get("type") == "tool" and part.get("tool_call_id") == tool_call_id
        ),
        None,
    )
    existing = parts[index] if index is not None else {}
    existing_status = str(existing.get("status") or "")
    existing_output = existing.get("output_text")
    event_type = str(event.get("type") or "")
    if event_type == "tool_execution_start":
        status = "error" if existing_status == "error" else "running"
        output_text = existing_output
    elif event_type == "tool_execution_update":
        status = "error" if existing_status == "error" else "running"
        update = _format_tool_result(event.get("partialResult"))
        output_text = (
            _merge_output_text(existing_output, update)
            if existing_status == "error"
            else update
        )
    elif event_type == "tool_execution_end":
        failed = bool(event.get("isError")) or existing_status == "error"
        status = "error" if failed else "done"
        result = _format_tool_result(event.get("result"))
        if failed and not result:
            result = _FAILED_TOOL_RESULT
        output_text = (
            _merge_output_text(existing_output, result)
            if failed and existing_output
            else result
        )
    else:
        return
    part: dict[str, Any] = {
        "type": "tool",
        "tool_call_id": tool_call_id[:128],
        "tool_name": str(existing.get("tool_name") or tool_name)[:128],
        "status": status,
        "text": "",
    }
    args_text = _format_tool_args(event.get("args")) or existing.get("args_text")
    output_text = output_text or existing_output
    if clipped_args := _clip_text(
        str(args_text) if args_text else None, _PERSIST_MAX_ARGS_CHARS
    ):
        part["args_text"] = clipped_args
    if clipped_output := _clip_text(
        str(output_text) if output_text else None, _PERSIST_MAX_OUTPUT_CHARS
    ):
        part["output_text"] = clipped_output
    if index is None:
        parts.append(part)
    else:
        parts[index] = part


def _message_update_tool_event(event: dict[str, Any]) -> dict[str, Any] | None:
    delta = event.get("assistantMessageEvent")
    if not isinstance(delta, dict):
        return None
    delta_type = str(delta.get("type") or "")
    if delta_type == "toolcall_start":
        return {
            "type": "tool_execution_start",
            "toolCallId": str(delta.get("id") or ""),
            "toolName": str(delta.get("toolName") or "tool"),
        }
    if delta_type != "toolcall_end":
        return None
    call = delta.get("toolCall")
    if not isinstance(call, dict):
        return None
    return {
        "type": "tool_execution_start",
        "toolCallId": str(call.get("id") or delta.get("id") or ""),
        "toolName": str(call.get("name") or call.get("toolName") or "tool"),
        "args": call.get("arguments"),
    }


def _record_extension_error(
    parts: list[dict[str, Any]],
    event: dict[str, Any],
) -> None:
    detail = trusted_chat_error_message("", error_kind="extension")
    running = [
        index
        for index, part in enumerate(parts)
        if part.get("type") == "tool" and part.get("status") == "running"
    ]
    if len(running) == 1:
        index = running[0]
        part = dict(parts[index])
        part["status"] = "error"
        part["output_text"] = (
            _merge_output_text(part.get("output_text"), detail) or detail
        )
        parts[index] = part
        return

    _upsert_error_part(
        parts,
        error_id=f"extension-error-{len(parts) + 1}",
        error_kind="extension",
        detail=detail,
    )


def _record_runtime_error(parts: list[dict[str, Any]], event: dict[str, Any]) -> None:
    error_code = stable_chat_error_code(event.get("code"))
    detail = trusted_chat_error_message(error_code, error_kind="runtime")
    tool_call_id = str(event.get("toolCallId") or "").strip()
    tool_name = str(event.get("toolName") or "").strip()
    candidates = [
        index
        for index, part in enumerate(parts)
        if part.get("type") == "tool"
        and (
            (tool_call_id and part.get("tool_call_id") == tool_call_id)
            or (
                not tool_call_id
                and tool_name
                and part.get("tool_name") == tool_name
                and part.get("status") == "running"
            )
        )
    ]
    if not candidates:
        candidates = [
            index
            for index, part in enumerate(parts)
            if part.get("type") == "tool" and part.get("status") == "running"
        ]
    if len(candidates) == 1:
        index = candidates[0]
        part = dict(parts[index])
        part["status"] = "error"
        part["output_text"] = (
            _merge_output_text(part.get("output_text"), detail) or detail
        )
        parts[index] = part
        return
    if any(
        part.get("type") == "error" and part.get("error_kind") == "model"
        for part in parts
    ):
        return

    if any(
        part.get("type") == "tool"
        and part.get("status") == "error"
        and detail in str(part.get("output_text") or "")
        for part in parts
    ):
        return
    metadata = (
        json.dumps(
            {"error_code": error_code},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if error_code
        else None
    )
    _upsert_error_part(
        parts,
        error_id=f"runtime-error-{len(parts) + 1}",
        error_kind="runtime",
        detail=detail,
        metadata_text=metadata,
        error_code=error_code or None,
    )


def _assistant_error_message(event: dict[str, Any]) -> dict[str, Any] | None:
    message = event.get("message")
    if (
        not isinstance(message, dict)
        or message.get("role") != "assistant"
        or message.get("stopReason") != "error"
    ):
        return None
    return message


def chat_parts_have_progress(parts: list[dict[str, Any]]) -> bool:
    """只把实际正文、已完成工具和交付文件算作已保留进展。"""
    return any(
        (part.get("type") == "content" and bool(str(part.get("text") or "").strip()))
        or (part.get("type") == "tool" and part.get("status") == "done")
        or part.get("type") == "file"
        for part in parts
    )


def _record_assistant_error(
    parts: list[dict[str, Any]], message: dict[str, Any]
) -> None:
    raw_provider_error = message.get("errorMessage")
    controlled_stop_code = _controlled_stop_error_code(raw_provider_error)
    error_code = controlled_stop_code or classify_model_provider_error(
        raw_provider_error
    )
    error_kind = "runtime" if controlled_stop_code else "model"
    redacted = redact_pi_payload(message)
    detail = trusted_chat_error_message(error_code, error_kind=error_kind)
    response_id = str(redacted.get("responseId") or "").strip()
    metadata_text = json.dumps(
        {"error_code": error_code},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    _upsert_error_part(
        parts,
        error_id=f"assistant-error:{response_id or len(parts) + 1}",
        error_kind=error_kind,
        detail=detail,
        metadata_text=metadata_text,
        error_code=error_code,
        retryable=(
            model_provider_error_retryable(error_code)
            if not controlled_stop_code
            else None
        ),
        progress_preserved=chat_parts_have_progress(parts),
        retry_after_seconds=(
            model_provider_retry_after_seconds(raw_provider_error)
            if not controlled_stop_code
            else None
        ),
    )


def _record_model_retry_wait(
    parts: list[dict[str, Any]], event: dict[str, Any]
) -> None:
    retry_after = event.get("retryAfterSeconds")
    if not isinstance(retry_after, int) or isinstance(retry_after, bool):
        retry_after = None
    existing = next(
        (
            part
            for part in reversed(parts)
            if part.get("type") == "error"
            and part.get("error_code") == "rate_limited"
        ),
        None,
    )
    _upsert_error_part(
        parts,
        error_id=str(
            (existing or {}).get("error_id") or "provider-rate-limit"
        ),
        error_kind="model",
        detail=MODEL_PROVIDER_ERROR_MESSAGES["rate_limited"],
        metadata_text=(
            str((existing or {}).get("metadata_text") or "") or None
        ),
        error_code="rate_limited",
        retryable=True,
        progress_preserved=chat_parts_have_progress(parts),
        retry_after_seconds=retry_after,
        auto_retry_scheduled=True,
    )


def _remove_rate_limit_error(parts: list[dict[str, Any]]) -> None:
    parts[:] = [
        part
        for part in parts
        if part.get("type") != "error"
        or part.get("error_code") != "rate_limited"
    ]


def pi_events_to_chat_parts(
    events: list[dict[str, Any]],
    *,
    final_content: str = "",
    interrupted: bool = False,
    final_error: dict[str, Any] | None = None,
    stream_redactor: PiEventStreamRedactor | None = None,
) -> list[dict[str, Any]]:
    """Fold redacted Pi events into the bounded timeline stored with a chat reply."""
    parts: list[dict[str, Any]] = []
    final_content = _redact_inline_secrets(final_content)
    for event in events:
        event_type = event.get("type")
        if assistant_error := _assistant_error_message(event):
            if stream_redactor is not None:
                for channel, text in stream_redactor.finish_all().items():
                    if channel in {"content", "thinking"}:
                        _append_text_part(parts, channel, text)
            _record_assistant_error(parts, assistant_error)
        if event_type == "message_update":
            delta = event.get("assistantMessageEvent")
            if not isinstance(delta, dict):
                continue
            delta_type = delta.get("type")
            if delta_type == "text_delta" and delta.get("delta"):
                chunk = str(delta["delta"])
                if stream_redactor is not None:
                    chunk = stream_redactor.feed(chunk, channel="content")
                _append_text_part(parts, "content", chunk)
            elif delta_type == "thinking_delta" and delta.get("delta"):
                chunk = str(delta["delta"])
                if stream_redactor is not None:
                    chunk = stream_redactor.feed(chunk, channel="thinking")
                _append_text_part(parts, "thinking", chunk)
            elif tool_event := _message_update_tool_event(event):
                _upsert_tool_part(parts, tool_event)
            continue
        if event_type in _TOOL_EVENT_TYPES:
            _upsert_tool_part(parts, event)
        elif event_type == "model_retry_wait":
            _record_model_retry_wait(parts, event)
        elif event_type in {"model_retry_resumed", "model_retry_cancelled"}:
            _remove_rate_limit_error(parts)
        elif event_type == "extension_error":
            _record_extension_error(parts, event)
        elif event_type == "error":
            _record_runtime_error(parts, event)
    if stream_redactor is not None:
        for channel, text in stream_redactor.finish_all().items():
            if channel in {"content", "thinking"}:
                _append_text_part(parts, channel, text)
    if final_error:
        error_kind = str(final_error.get("error_kind") or "runtime")
        if error_kind not in _ERROR_KIND_PRIORITY:
            error_kind = "runtime"
        error_id = _clip_text(
            _redact_inline_secrets(str(final_error.get("error_id") or "runtime-error")),
            128,
        )
        error_code = _error_code_from_part(final_error)
        detail = trusted_chat_error_message(error_code, error_kind=error_kind)
        metadata_text = _safe_error_metadata_text(
            final_error,
            error_code=error_code,
        )
        controlled_stop_code = _controlled_stop_error_code(
            error_code,
            metadata_text,
        )
        if controlled_stop_code:
            parts[:] = [
                part
                for part in parts
                if part.get("type") != "error"
                or _controlled_stop_error_code(
                    part.get("error_id"),
                    part.get("error_code"),
                    part.get("metadata_text"),
                )
                != controlled_stop_code
            ]
        provider_error = next(
            (
                part
                for part in reversed(parts)
                if part.get("type") == "error"
                and part.get("error_kind") == "model"
                and part.get("error_code") in MODEL_PROVIDER_ERROR_MESSAGES
            ),
            None,
        )
        duplicate_provider_failure = provider_error is not None and (
            error_code in {"upstream_error", "protocol_error"}
            or error_code == provider_error.get("error_code")
        )
        if duplicate_provider_failure and provider_error is not None:
            provider_error["auto_retry_scheduled"] = False
        else:
            _upsert_error_part(
                parts,
                error_id=error_id or "runtime-error",
                error_kind=error_kind,
                detail=detail,
                metadata_text=metadata_text,
                error_code=error_code or None,
                retryable=(
                    final_error.get("retryable")
                    if isinstance(final_error.get("retryable"), bool)
                    else None
                ),
                progress_preserved=(
                    final_error.get("progress_preserved")
                    if isinstance(final_error.get("progress_preserved"), bool)
                    else None
                ),
                retry_after_seconds=(
                    final_error.get("retry_after_seconds")
                    if isinstance(final_error.get("retry_after_seconds"), int)
                    else None
                ),
                auto_retry_scheduled=(
                    final_error.get("auto_retry_scheduled")
                    if isinstance(final_error.get("auto_retry_scheduled"), bool)
                    else None
                ),
            )

    unresolved_result = (
        _INTERRUPTED_TOOL_RESULT if interrupted else _MISSING_TOOL_RESULT
    )
    for part in parts:
        if part.get("type") != "tool" or part.get("status") != "running":
            continue
        part["status"] = "error"
        partial = str(part.get("output_text") or "").strip()
        part["output_text"] = (
            f"{partial}\n{unresolved_result}" if partial else unresolved_result
        )

    assembled = "".join(
        str(part.get("text") or "") for part in parts if part.get("type") == "content"
    )
    if final_content and final_content != assembled:
        parts = [
            part for part in parts if part.get("type") in {"thinking", "tool", "error"}
        ]
        parts.append({"type": "content", "text": final_content})
    elif final_content and not parts:
        parts.append({"type": "content", "text": final_content})

    for part in parts:
        kind = part.get("type")
        if kind == "thinking":
            part["text"] = _clip_text(
                str(part.get("text") or ""), _PERSIST_MAX_THINKING_CHARS
            )
        elif kind == "content":
            part["text"] = _clip_text(
                str(part.get("text") or ""), _PERSIST_MAX_CONTENT_CHARS
            )
        elif kind == "error":
            part["text"] = _clip_text(
                str(part.get("text") or ""), _PERSIST_MAX_OUTPUT_CHARS
            )
            if part.get("metadata_text"):
                part["metadata_text"] = _clip_text(
                    str(part["metadata_text"]), _PERSIST_MAX_ARGS_CHARS
                )
    return parts[-_PERSIST_MAX_PARTS:]


def pi_event_to_sse_item(
    event: dict[str, Any],
    *,
    agent_name: str,
    stream_redactor: PiEventStreamRedactor | None = None,
) -> dict[str, Any] | None:
    event_type = event.get("type")
    if assistant_error := _assistant_error_message(event):
        parts: list[dict[str, Any]] = []
        _record_assistant_error(parts, assistant_error)
        return chat_error_part_to_sse_item(parts[-1], agent_name=agent_name)
    if event_type == "message_update":
        delta = event.get("assistantMessageEvent") or {}
        delta_type = delta.get("type")
        if delta_type == "text_delta" and delta.get("delta"):
            content = str(delta["delta"])
            if stream_redactor is not None:
                content = stream_redactor.feed(content, channel="content")
            if not content:
                return None
            return {
                "type": "ModelClientStreamingChunkEvent",
                "source": agent_name,
                "content": _redact_inline_secrets(content),
            }
        if delta_type == "thinking_delta" and delta.get("delta"):
            content = str(delta["delta"])
            if stream_redactor is not None:
                content = stream_redactor.feed(content, channel="thinking")
            if not content:
                return None
            return {
                "type": "PiThinkingChunkEvent",
                "source": agent_name,
                "content": _redact_inline_secrets(content),
            }
        if tool_event := _message_update_tool_event(event):
            return {
                "type": "PiToolExecutionEvent",
                "source": agent_name,
                "content": redact_pi_payload(tool_event),
            }
        return None
    if event_type in {"thinking_start", "thinking_delta", "thinking_end"}:
        return {
            "type": "PiThinkingEvent",
            "source": agent_name,
            "content": redact_pi_payload(event),
        }
    if event_type == "extension_ui_request":
        return {
            "type": "PiApprovalRequestEvent",
            "source": agent_name,
            "content": redact_pi_payload(event),
        }
    if event_type in _TOOL_EVENT_TYPES:
        content = redact_pi_payload(event)
        if isinstance(content, dict) and (
            event_type == "tool_execution_end" and event.get("isError")
        ):
            content["result"] = _FAILED_TOOL_RESULT
        return {
            "type": "PiToolExecutionEvent",
            "source": agent_name,
            "content": content,
        }
    if (
        event_type == "runtime"
        and event.get("event") == "execution_queue"
        and event.get("status") in {"waiting", "running"}
    ):
        return {
            "type": "PiRuntimeEvent",
            "source": agent_name,
            "content": redact_pi_payload(event),
        }
    if event_type in {"extension_error", "error"}:
        return {
            "type": "PiRuntimeEvent",
            "source": agent_name,
            "content": _safe_error_event_payload(event),
        }
    if event_type in {
        "compaction_start",
        "compaction_end",
        "auto_retry_start",
        "auto_retry_end",
        "model_retry_wait",
        "model_retry_resumed",
        "model_retry_cancelled",
    }:
        return {
            "type": "PiRuntimeEvent",
            "source": agent_name,
            "content": redact_pi_payload(event),
        }
    return None
