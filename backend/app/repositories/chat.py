from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from sqlalchemy import ScalarSelect, and_, case, func, or_, select, update
from sqlalchemy.orm import Session, selectinload, with_expression

from app.core.security import as_utc, iso, new_token, now
from app.core.user_messages import (
    canonical_chat_error_metadata,
    redact_error_text,
    trusted_chat_error_message,
)
from app.models.chat import (
    ChatCategory,
    ChatMessage,
    ChatSession,
    ChatSessionPublication,
)
from app.schemas.evidence import RetrievalTraceResponse

logger = logging.getLogger(__name__)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _load_writing_context(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    from app.schemas.chat import ChatWritingContext
    from pydantic import ValidationError

    try:
        return ChatWritingContext.model_validate_json(raw).model_dump()
    except ValidationError:
        return None


_TIMELINE_ID_PREFIXES = (
    "assistant-error:",
    "runtime-error-",
    "extension-error-",
    "tool:",
)
_CUSTOMER_TOOL_FAILURE_TEXT = trusted_chat_error_message("", error_kind="tool")


def _redact_timeline_id(raw_id: str) -> str:
    """Bound and redact a persisted timeline ID for safe reads.

    Role-prefixed prefixes (assistant-error:, runtime-error-, ...) are stable
    identifiers produced by the backend, so they are preserved; any remaining
    suffix is treated as untrusted history and redacted/bounded like error
    text. Never raise so a malformed historical row still normalizes.
    """
    if not raw_id:
        return ""
    for prefix in _TIMELINE_ID_PREFIXES:
        if raw_id.startswith(prefix):
            suffix = redact_error_text(raw_id[len(prefix) :], limit=96).strip()
            return f"{prefix}{suffix}" if suffix else prefix
    return redact_error_text(raw_id, limit=128).strip()


def _load_trace(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return None
        return RetrievalTraceResponse(**parsed).model_dump()
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        # Keep malformed historical JSON from breaking session reads. Do not
        # log the value because it may contain user-provided content.
        logger.warning(
            "invalid chat retrieval trace ignored error=%s", type(exc).__name__
        )
        return None


def _dump_trace(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    try:
        return _dump(RetrievalTraceResponse(**value).model_dump())
    except (TypeError, ValueError) as exc:
        logger.warning(
            "invalid chat retrieval trace ignored error=%s", type(exc).__name__
        )
        return None


def _normalize_parts(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:200]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip()
        if kind == "reply_boundary":
            root = str(item.get("root_assistant_id") or "").strip()
            source = str(item.get("source_user_message_id") or "").strip()
            root_user = str(item.get("root_user_id") or "").strip()
            raw_sources = item.get("source_user_message_ids")
            source_ids = list(dict.fromkeys(
                value.strip() for value in raw_sources
                if isinstance(value, str) and value.strip() and len(value.strip()) <= 64
            )) if isinstance(raw_sources, list) else []
            if root and source and len(root) <= 64 and len(source) <= 64:
                out.append({
                    "type": kind,
                    "root_assistant_id": root,
                    **({"root_user_id": root_user} if root_user and len(root_user) <= 64 else {}),
                    "source_user_message_id": source,
                    "source_user_message_ids": source_ids,
                    "incomplete": bool(item.get("incomplete")),
                })
            continue
        if kind == "presentation_template":
            revision_id = str(item.get("revision_id") or "").strip()
            if revision_id and len(revision_id) <= 64:
                part = {"type": kind, "revision_id": revision_id}
                # 公共请求 schema 不接受此类型；内部副本必须完整保留以支持重试和分支。
                if isinstance(item.get("deck_content"), str):
                    part["deck_content"] = item["deck_content"]
                out.append(part)
            continue
        if kind in ("thinking", "content"):
            text = str(item.get("text") or "")
            if not text:
                continue
            out.append({"type": kind, "text": text})
            continue
        if kind == "file":
            name = str(item.get("name") or "").strip()
            href = str(item.get("href") or "").strip()
            mime = str(item.get("mime") or "").strip()
            size = item.get("size")
            if (
                not name
                or len(name) > 255
                or not href.startswith("/api/files/")
                or len(href) > 2048
                or not mime
                or len(mime) > 255
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
                or size > 128 * 1024 * 1024
            ):
                continue
            out.append(
                {
                    "type": "file",
                    "name": name,
                    "href": href,
                    "mime": mime,
                    "size": size,
                }
            )
            continue
        if kind == "error":
            error_id = redact_error_text(
                item.get("error_id") or item.get("errorId"),
                limit=128,
            ).strip()
            error_kind = str(
                item.get("error_kind") or item.get("errorKind") or ""
            ).strip()
            if not error_id or error_kind not in ("model", "runtime", "extension"):
                continue
            error_code, metadata_text = canonical_chat_error_metadata(
                item.get("metadata_text") or item.get("metadataText"),
                error_code=item.get("error_code") or item.get("errorCode"),
            )
            part = {
                "type": "error",
                "error_id": error_id,
                "error_kind": error_kind,
                "text": trusted_chat_error_message(
                    error_code,
                    error_kind=error_kind,
                ),
            }
            if metadata_text:
                part["metadata_text"] = metadata_text
            # 错误恢复字段必须跨持久化保留，不能只在 live SSE 中可见。
            if error_code:
                part["error_code"] = error_code
            for key, alias in (
                ("retryable", "retryable"),
                ("progress_preserved", "progressPreserved"),
                ("auto_retry_scheduled", "autoRetryScheduled"),
            ):
                value = item.get(key, item.get(alias))
                if isinstance(value, bool):
                    part[key] = value
            retry_after = item.get(
                "retry_after_seconds", item.get("retryAfterSeconds")
            )
            if (
                isinstance(retry_after, int)
                and not isinstance(retry_after, bool)
                and retry_after >= 0
            ):
                part["retry_after_seconds"] = retry_after
            out.append(part)
            continue
        if kind != "tool":
            continue
        # History predates safe writers: an ID may embed raw credentials. Strip
        # prefixes that identify timeline roles, then redact/bound like the
        # canonical error branch so the surviving opaque part is usable for
        # replay matching without leaking secrets.
        tool_call_id = _redact_timeline_id(
            str(item.get("tool_call_id") or item.get("toolCallId") or "").strip()
        )
        tool_name = str(item.get("tool_name") or item.get("toolName") or "").strip()[
            :128
        ]
        status = str(item.get("status") or "").strip()
        if (
            not tool_call_id
            or not tool_name
            or status not in ("running", "done", "error")
        ):
            continue
        legacy_error_kind = (
            "model"
            if tool_name == "runtime:model"
            and tool_call_id.startswith("assistant-error:")
            else (
                "runtime"
                if tool_name == "runtime" and tool_call_id.startswith("runtime-error-")
                else (
                    "extension"
                    if tool_name.startswith("extension:")
                    and tool_call_id.startswith("extension-error-")
                    else None
                )
            )
        )
        if legacy_error_kind:
            out.append(
                {
                    "type": "error",
                    "error_id": tool_call_id,
                    "error_kind": legacy_error_kind,
                    "text": trusted_chat_error_message(
                        "",
                        error_kind=legacy_error_kind,
                    ),
                }
            )
            continue
        part: dict[str, Any] = {
            "type": "tool",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": status,
            "text": "",
        }
        # Match full-fidelity chat sync; recovery summaries apply their own limits.
        args_text = redact_error_text(
            item.get("args_text") or item.get("argsText"),
            limit=16_000,
        )
        output_text = redact_error_text(
            item.get("output_text") or item.get("outputText"),
            limit=80_000,
        )
        if args_text:
            part["args_text"] = args_text
        if output_text:
            part["output_text"] = output_text
        out.append(part)
    return out


def _customer_safe_parts(
    parts: list[dict[str, Any]], raw_parts: list[Any], *, role: object
) -> list[dict[str, Any]]:
    """清理客户展示副本，不改动模型恢复所需的内部工具失败信息。"""
    customer_parts: list[dict[str, Any]] = []
    for part in parts:
        if part.get("type") in {"presentation_template", "reply_boundary"}:
            continue
        item = dict(part)
        if item.get("type") == "tool" and item.get("status") == "error":
            item["output_text"] = _CUSTOMER_TOOL_FAILURE_TEXT
            item.pop("args_text", None)
        elif item.get("type") == "error" and "auto_retry_scheduled" in item:
            item["auto_retry_scheduled"] = False
        elif item.get("type") in ("content", "thinking"):
            item["text"] = _customer_safe_content(
                role=role,
                content=item.get("text"),
                raw_parts=raw_parts,
                parts=parts,
            )
        customer_parts.append(item)
    return customer_parts


def message_tool_executions(
    message: ChatMessage,
    *,
    max_items: int = 6,
    max_args_chars: int = 300,
    max_output_chars: int = 700,
) -> list[dict[str, str]]:
    """Return a bounded execution summary from a persisted assistant timeline."""
    parts = _normalize_parts(_load_list(getattr(message, "parts_json", None)))
    executions: list[dict[str, str]] = []
    for part in parts:
        if part.get("type") != "tool":
            continue
        item = {
            "tool_name": str(part.get("tool_name") or "tool"),
            "status": str(part.get("status") or "running"),
        }
        args_text = str(part.get("args_text") or "").strip()
        output_text = str(part.get("output_text") or "").strip()
        if args_text and max_args_chars > 0:
            item["args_text"] = args_text[:max_args_chars]
        if output_text and max_output_chars > 0:
            item["output_text"] = output_text[:max_output_chars]
        executions.append(item)
    if max_items <= 0:
        return []
    return executions[-max_items:]


def message_runtime_errors(
    message: ChatMessage,
    *,
    max_items: int = 6,
    max_metadata_chars: int = 300,
    max_text_chars: int = 700,
) -> list[dict[str, str]]:
    """Return bounded non-tool runtime failures from a persisted timeline."""
    parts = _normalize_parts(_load_list(getattr(message, "parts_json", None)))
    errors: list[dict[str, str]] = []
    for part in parts:
        if part.get("type") != "error":
            continue
        item = {
            "type": "error",
            "error_id": str(part.get("error_id") or "runtime-error"),
            "error_kind": str(part.get("error_kind") or "runtime"),
            "text": str(part.get("text") or "")[:max_text_chars],
        }
        metadata_text = str(part.get("metadata_text") or "").strip()
        if metadata_text and max_metadata_chars > 0:
            item["metadata_text"] = metadata_text[:max_metadata_chars]
        errors.append(item)
    if max_items <= 0:
        return []
    return errors[-max_items:]


_ALLOWED_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_PERSIST_SESSION_ID = re.compile(r"^chs_[A-Za-z0-9_-]{8,56}$")
ZERO_USAGE = {
    "provider": "agent_chat",
    "model": None,
    "input_tokens": 0,
    "input_uncached_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "cost": "0",
    "latency_ms": 0,
}


class ChatMessagesRevisionConflict(Exception):
    """Raised when a full snapshot was based on an older server revision."""

    def __init__(
        self,
        *,
        current_revision: int,
        current_snapshot: dict[str, Any],
    ) -> None:
        super().__init__("chat messages revision conflict")
        self.current_revision = current_revision
        self.current_snapshot = current_snapshot


@dataclass(frozen=True)
class ChatMessagePage:
    """A bounded active-message window plus the variants needed to render it."""

    messages: list[ChatMessage]
    variant_messages: list[ChatMessage]
    has_more_messages: bool
    next_before_message_id: str | None


def _normalize_images(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw[:4]:
        if not isinstance(item, dict):
            continue
        data = str(item.get("data") or "").strip()
        mime = str(item.get("mimeType") or "").strip().lower()
        if not data or mime not in _ALLOWED_IMAGE_MIMES:
            continue
        if len(data) > 6_000_000:
            continue
        out.append({"type": "image", "data": data, "mimeType": mime})
    return out


def get_owned_message_image(
    db: Session,
    *,
    session_id: str,
    message_id: str,
    owner_user_id: str,
    image_index: int,
) -> dict[str, str] | None:
    """Return one persisted image only when both message and session are owned."""
    if image_index < 0 or image_index >= 4:
        return None
    message = db.scalar(
        select(ChatMessage)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatMessage.id == message_id,
            ChatMessage.session_id == session_id,
            ChatSession.owner_user_id == owner_user_id,
            ChatMessage.owner_user_id == owner_user_id,
        )
    )
    if message is None:
        return None
    images = _normalize_images(_load_list(message.images_json))
    return images[image_index] if image_index < len(images) else None




def _coerce_int(value: object, default: int = 0) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0, number)


# Point-in-time context measurements, not cumulative counters: compaction can
# lower the token count and switching models changes the window. Every merge
# path (database sync, browser outbox) must overwrite these with the newer
# snapshot instead of taking a maximum.
CONTEXT_SNAPSHOT_MINIMUMS = {"context_input_tokens": 0, "context_window": 1}
CONTEXT_SNAPSHOT_KEYS = tuple(CONTEXT_SNAPSHOT_MINIMUMS)


def context_snapshot_fields(raw: object) -> dict[str, int | None]:
    """Project the context snapshot keys, dropping invalid or absent values."""
    source = raw if isinstance(raw, dict) else {}
    fields: dict[str, int | None] = {}
    for key, minimum in CONTEXT_SNAPSHOT_MINIMUMS.items():
        value = source.get(key)
        fields[key] = value if type(value) is int and value >= minimum else None
    return fields


def normalize_usage(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        if not raw.strip() or raw.strip() in ("{}", "null"):
            return None
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    raw = cast(dict[str, Any], raw)
    from app.services.usage_normalize import normalize_usage_buckets

    buckets = normalize_usage_buckets(raw)
    latency_ms = _coerce_int(raw.get("latency_ms", raw.get("run_time_ms")))
    model = raw.get("model")
    model_str = str(model).strip() if model is not None else None
    normalized: dict[str, Any] = {
        "provider": str(raw.get("provider") or "agent_chat"),
        "model": model_str or None,
        **buckets,
        **context_snapshot_fields(raw),
        "cost": str(raw.get("cost") if raw.get("cost") is not None else "0"),
        "latency_ms": latency_ms,
    }
    for key in ("answer_model", "vision_model"):
        value = str(raw.get(key) or "").strip()[:160]
        if value:
            normalized[key] = value
    status = str(raw.get("status") or "completed").strip()
    normalized["status"] = status if status in {"completed", "failed"} else "failed"
    error_code = str(raw.get("error_code") or "").strip()[:64]
    if error_code:
        normalized["error_code"] = error_code
    warning_threshold = raw.get("token_warning_threshold")
    warning_observed = raw.get("token_warning_observed_tokens")
    if (
        raw.get("token_warning") is True
        and type(warning_threshold) is int
        and warning_threshold > 0
        and type(warning_observed) is int
        and warning_observed > warning_threshold
    ):
        normalized["token_warning"] = True
        normalized["token_warning_threshold"] = warning_threshold
        normalized["token_warning_observed_tokens"] = warning_observed
    route_mode = str(raw.get("route_mode") or "").strip()
    if route_mode in {"direct", "vision_fallback", "ocr_assisted"}:
        normalized["route_mode"] = route_mode
    if "vision_fallback_used" in raw:
        normalized["vision_fallback_used"] = bool(raw.get("vision_fallback_used"))
    calls = raw.get("calls")
    if isinstance(calls, list):
        normalized_calls: list[dict[str, Any]] = []
        for item in calls[:8]:
            if not isinstance(item, dict):
                continue
            purpose = str(item.get("purpose") or "").strip()
            if purpose not in {"answer_generation", "vision_parse"}:
                continue
            call = {
                "purpose": purpose,
                "provider": str(item.get("provider") or "")[:128],
                "model": str(item.get("model") or "").strip()[:160] or None,
                "endpoint_id": str(item.get("endpoint_id") or "").strip()[:128] or None,
                "input_tokens": _coerce_int(item.get("input_tokens")),
                "input_uncached_tokens": _coerce_int(item.get("input_uncached_tokens")),
                "cache_read_tokens": _coerce_int(item.get("cache_read_tokens")),
                "cache_write_tokens": _coerce_int(item.get("cache_write_tokens")),
                "output_tokens": _coerce_int(item.get("output_tokens")),
                "total_tokens": _coerce_int(item.get("total_tokens")),
                "cost": str(item.get("cost") if item.get("cost") is not None else "0"),
                "latency_ms": _coerce_int(item.get("latency_ms")),
                "status": str(item.get("status") or "completed")
                if str(item.get("status") or "completed") in {"completed", "failed"}
                else "failed",
            }
            error_code = str(item.get("error_code") or "").strip()[:64]
            if error_code:
                call["error_code"] = error_code
            for key in ("price_version_id", "currency"):
                value = str(item.get(key) or "").strip()[:128]
                if value:
                    call[key] = value
            normalized_calls.append(call)
        if normalized_calls:
            normalized["calls"] = normalized_calls
    return normalized


def _priced_usage(db: Session, raw: object) -> dict[str, Any] | None:
    from app.services.pricing import fill_usage_cost

    usage = normalize_usage(raw)
    if usage and isinstance(usage.get("calls"), list):
        # Price each actual provider call independently.  Pricing the aggregate
        # against the answer model would misattribute the vision helper cost.
        calls = usage["calls"]
        total_cost = Decimal("0")
        totals = {
            "input_tokens": 0,
            "input_uncached_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        for call in calls:
            fill_usage_cost(db, call)
            for key in totals:
                totals[key] += _coerce_int(call.get(key))
            try:
                total_cost += Decimal(str(call.get("cost") or "0"))
            except (InvalidOperation, ValueError, TypeError):
                pass
        usage.update(totals)
        usage["cost"] = (
            format(total_cost.normalize(), "f").rstrip("0").rstrip(".") or "0"
        )
        return usage
    return fill_usage_cost(db, usage)


def create_category(
    db: Session, *, owner_user_id: str, name: str, sort_order: int = 0
) -> ChatCategory:
    stamp = now()
    category = ChatCategory(
        id=f"chc_{new_token(10)}",
        owner_user_id=owner_user_id,
        name=name.strip(),
        sort_order=sort_order,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(category)
    db.flush()
    return category


def list_categories(db: Session, owner_user_id: str) -> list[ChatCategory]:
    return list(
        db.scalars(
            select(ChatCategory)
            .where(ChatCategory.owner_user_id == owner_user_id)
            .order_by(ChatCategory.sort_order.asc(), ChatCategory.created_at.asc())
        )
    )


def get_owned_category(
    db: Session, category_id: str, owner_user_id: str
) -> ChatCategory | None:
    return db.scalar(
        select(ChatCategory).where(
            ChatCategory.id == category_id, ChatCategory.owner_user_id == owner_user_id
        )
    )


def update_category(
    db: Session,
    category: ChatCategory,
    *,
    name: str | None = None,
    sort_order: int | None = None,
) -> ChatCategory:
    if name is not None:
        category.name = name.strip()
    if sort_order is not None:
        category.sort_order = sort_order
    category.updated_at = now()
    db.flush()
    return category


def delete_category(db: Session, category: ChatCategory) -> None:
    db.delete(category)


def create_session(
    db: Session,
    *,
    owner_user_id: str,
    title: str = "新对话",
    category_id: str | None = None,
    agent_session_id: str | None = None,
    preferred_model: str | None = None,
) -> ChatSession:
    stamp = now()
    session = ChatSession(
        id=f"chs_{new_token(10)}",
        owner_user_id=owner_user_id,
        title=title.strip() or "新对话",
        title_finalized=False,
        agent_session_id=agent_session_id,
        preferred_model=(preferred_model or "").strip() or None,
        category_id=category_id,
        pinned=False,
        pinned_at=None,
        archived=False,
        archived_at=None,
        writing_project_id=None,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(session)
    db.flush()
    return session


def get_owned_session(
    db: Session,
    session_id: str,
    owner_user_id: str,
    *,
    for_update: bool = False,
) -> ChatSession | None:
    statement = (
        select(ChatSession)
        .options(selectinload(ChatSession.category), selectinload(ChatSession.messages))
        .where(ChatSession.id == session_id, ChatSession.owner_user_id == owner_user_id)
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _counted_message_expression() -> ScalarSelect[int]:
    """Count a session's readable messages for sidebar and title projection.

    A regenerated answer keeps its superseded sibling in the table, so counting
    rows would make the badge grow by one on every regeneration. Only the
    variant the user actually reads counts here; usage reporting still
    aggregates every attempt directly from ``chat_messages.usage``.
    """
    return (
        select(func.count(ChatMessage.id))
        .where(
            ChatMessage.session_id == ChatSession.id,
            ChatMessage.variant_active.is_(True),
        )
        .correlate(ChatSession)
        .scalar_subquery()
    )


def get_owned_session_summary(
    db: Session,
    session_id: str,
    owner_user_id: str,
) -> ChatSession | None:
    """Load one owned session for summary rendering.

    Unlike :func:`get_owned_session` this does NOT eager-load the messages
    relationship; it attaches the counted ``summary_message_count`` expression
    instead, so a title poll or other summary read does not pull the whole
    message history out of the database.
    """
    message_count = _counted_message_expression()
    statement = (
        select(ChatSession)
        .options(
            selectinload(ChatSession.category),
            with_expression(ChatSession.summary_message_count, message_count),
        )
        .where(
            ChatSession.id == session_id,
            ChatSession.owner_user_id == owner_user_id,
        )
    )
    return db.scalar(statement)
_ROLE_ORDER_EXPRESSION = case(
    (ChatMessage.role == "user", 0),
    (ChatMessage.role == "assistant", 1),
    (ChatMessage.role == "system", 2),
    else_=9,
)


def _message_precedes(anchor: ChatMessage):
    anchor_role = _ROLE_ORDER.get(anchor.role, 9)
    return or_(
        ChatMessage.created_at < anchor.created_at,
        and_(
            ChatMessage.created_at == anchor.created_at,
            _ROLE_ORDER_EXPRESSION < anchor_role,
        ),
        and_(
            ChatMessage.created_at == anchor.created_at,
            _ROLE_ORDER_EXPRESSION == anchor_role,
            ChatMessage.id < anchor.id,
        ),
    )


def _message_follows(anchor: ChatMessage):
    anchor_role = _ROLE_ORDER.get(anchor.role, 9)
    return or_(
        ChatMessage.created_at > anchor.created_at,
        and_(
            ChatMessage.created_at == anchor.created_at,
            _ROLE_ORDER_EXPRESSION > anchor_role,
        ),
        and_(
            ChatMessage.created_at == anchor.created_at,
            _ROLE_ORDER_EXPRESSION == anchor_role,
            ChatMessage.id > anchor.id,
        ),
    )


def _owned_message_anchor(
    db: Session,
    *,
    session_id: str,
    owner_user_id: str,
    message_id: str,
) -> ChatMessage | None:
    anchor = db.scalar(
        select(ChatMessage)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatMessage.id == message_id,
            ChatMessage.session_id == session_id,
            ChatSession.owner_user_id == owner_user_id,
        )
    )
    if (
        anchor is not None
        and not bool(getattr(anchor, "variant_active", True))
        and anchor.variant_group_id
    ):
        active = db.scalar(
            select(ChatMessage).where(
                ChatMessage.session_id == session_id,
                ChatMessage.variant_group_id == anchor.variant_group_id,
                ChatMessage.variant_active.is_(True),
            )
        )
        if active is not None:
            return active
    return anchor


def get_session_message_page(
    db: Session,
    *,
    session_id: str,
    owner_user_id: str,
    limit: int = 40,
    before_message_id: str | None = None,
    around_message_id: str | None = None,
) -> ChatMessagePage | None:
    """Load one owner-scoped active-message window without hydrating the transcript."""
    if before_message_id and around_message_id:
        raise ValueError("before_message_id 与 around_message_id 不能同时使用")
    page_limit = min(max(int(limit), 1), 100)
    owned = db.scalar(
        select(ChatSession.id).where(
            ChatSession.id == session_id,
            ChatSession.owner_user_id == owner_user_id,
        )
    )
    if owned is None:
        return None

    anchor_id = (around_message_id or before_message_id or "").strip()
    anchor = (
        _owned_message_anchor(
            db,
            session_id=session_id,
            owner_user_id=owner_user_id,
            message_id=anchor_id,
        )
        if anchor_id
        else None
    )
    if anchor_id and anchor is None:
        raise LookupError("消息不存在")

    base = select(ChatMessage).where(
        ChatMessage.session_id == session_id,
        ChatMessage.variant_active.is_(True),
    )
    ascending = (
        ChatMessage.created_at.asc(),
        _ROLE_ORDER_EXPRESSION.asc(),
        ChatMessage.id.asc(),
    )
    descending = (
        ChatMessage.created_at.desc(),
        _ROLE_ORDER_EXPRESSION.desc(),
        ChatMessage.id.desc(),
    )
    if around_message_id and anchor is not None:
        older_desc = list(
            db.scalars(
                base.where(_message_precedes(anchor))
                .order_by(*descending)
                .limit(page_limit + 1)
            )
        )
        newer = list(
            db.scalars(
                base.where(_message_follows(anchor))
                .order_by(*ascending)
                .limit(page_limit)
            )
        )
        before_count = min(page_limit // 2, len(older_desc))
        after_count = min(page_limit - 1 - before_count, len(newer))
        remaining = page_limit - 1 - before_count - after_count
        if remaining:
            extra_before = min(remaining, len(older_desc) - before_count)
            before_count += extra_before
            remaining -= extra_before
        if remaining:
            after_count += min(remaining, len(newer) - after_count)
        messages = [
            *reversed(older_desc[:before_count]),
            anchor,
            *newer[:after_count],
        ]
        has_more = len(older_desc) > before_count
    else:
        statement = base
        if before_message_id and anchor is not None:
            statement = statement.where(_message_precedes(anchor))
        statement = statement.order_by(*descending).limit(page_limit + 1)
        newest_first = list(db.scalars(statement))
        has_more = len(newest_first) > page_limit
        messages = list(reversed(newest_first[:page_limit]))

    next_before = messages[0].id if has_more and messages else (
        anchor.id if has_more and anchor is not None else None
    )
    group_ids = {
        str(message.variant_group_id)
        for message in messages
        if message.variant_group_id
    }
    variant_messages = (
        list(
            db.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.variant_group_id.in_(group_ids),
                )
                .order_by(
                    ChatMessage.variant_group_id,
                    ChatMessage.variant_index,
                    ChatMessage.created_at,
                    ChatMessage.id,
                )
            )
        )
        if group_ids
        else []
    )
    return ChatMessagePage(
        messages=messages,
        variant_messages=variant_messages,
        has_more_messages=has_more,
        next_before_message_id=next_before,
    )




def can_restore_session_for_owner(
    db: Session, session_id: str, owner_user_id: str
) -> bool:
    """Return whether a missing client session id may be restored for an owner."""
    normalized = (session_id or "").strip()
    if not _PERSIST_SESSION_ID.fullmatch(normalized):
        return False
    existing = db.get(ChatSession, normalized)
    return existing is None or existing.owner_user_id == owner_user_id


def restore_session_for_owner(
    db: Session,
    *,
    session_id: str,
    owner_user_id: str,
    title: str | None = None,
    agent_session_id: str | None = None,
    writing_project_id: str | None = None,
    preferred_model: str | None = None,
) -> ChatSession | None:
    """Recreate a missing chat session so persist can reuse the browser's ID."""
    normalized = (session_id or "").strip()
    if not _PERSIST_SESSION_ID.fullmatch(normalized):
        return None
    existing = db.get(ChatSession, normalized)
    if existing is not None:
        return existing if existing.owner_user_id == owner_user_id else None
    stamp = now()
    session = ChatSession(
        id=normalized,
        owner_user_id=owner_user_id,
        title=(title or "").strip() or "新对话",
        title_finalized=False,
        agent_session_id=(agent_session_id or "").strip() or None,
        preferred_model=(preferred_model or "").strip() or None,
        category_id=None,
        pinned=False,
        pinned_at=None,
        archived=False,
        archived_at=None,
        writing_project_id=(writing_project_id or "").strip() or None,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(session)
    db.flush()
    return session


def list_sessions(
    db: Session,
    owner_user_id: str,
    *,
    view: str = "active",
    search: str = "",
    category_id: str | None = None,
    uncategorized: bool = False,
    limit: int = 8,
    offset: int = 0,
) -> list[ChatSession]:
    message_count = _counted_message_expression()
    statement = (
        select(ChatSession)
        .options(
            selectinload(ChatSession.category),
            with_expression(ChatSession.summary_message_count, message_count),
        )
        .where(
            ChatSession.owner_user_id == owner_user_id,
            ChatSession.archived.is_(view == "archived"),
        )
    )
    if uncategorized:
        statement = statement.where(ChatSession.category_id.is_(None))
    elif category_id:
        statement = statement.where(ChatSession.category_id == category_id)
    normalized_search = search.strip()
    if normalized_search:
        escaped = (
            normalized_search.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        message_match = (
            select(ChatMessage.id)
            .where(
                ChatMessage.session_id == ChatSession.id,
                ChatMessage.content.ilike(pattern, escape="\\"),
            )
            .exists()
        )
        statement = statement.where(
            or_(
                ChatSession.title.ilike(pattern, escape="\\"),
                message_match,
            )
        )
    return list(
        db.scalars(
            statement.order_by(
                ChatSession.pinned.desc(),
                ChatSession.updated_at.desc(),
                ChatSession.id.asc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
    )


def update_session(
    db: Session,
    session: ChatSession,
    *,
    title: str | None = None,
    override_frozen_title: bool = False,
    category_id: str | None = None,
    clear_category: bool = False,
    pinned: bool | None = None,
    agent_session_id: str | None = None,
    clear_agent_session: bool = False,
    writing_project_id: str | None = None,
    preferred_model: str | None = None,
    archived: bool | None = None,
) -> ChatSession:
    if title is not None and not (
        session.title_finalized and not override_frozen_title
    ):
        session.title = title.strip() or session.title
        if override_frozen_title:
            session.title_finalized = True
    if clear_category:
        session.category_id = None
    elif category_id is not None:
        session.category_id = category_id
    if pinned is not None:
        session.pinned = pinned
        session.pinned_at = now() if pinned else None
    if archived is not None:
        session.archived = archived
        session.archived_at = now() if archived else None
    if clear_agent_session:
        session.agent_session_id = None
    elif agent_session_id is not None:
        session.agent_session_id = agent_session_id
    if writing_project_id is not None:
        session.writing_project_id = writing_project_id
    if preferred_model is not None:
        session.preferred_model = preferred_model.strip() or None
    session.updated_at = now()
    db.flush()
    return session


def delete_session(db: Session, session: ChatSession) -> None:
    from app.services import external_object_cleanup
    from app.repositories import user_memory

    session = _claim_next_messages_revision(db, session.id)
    user_memory.invalidate_message_sources(
        db,
        owner_user_id=session.owner_user_id,
        message_ids=list(db.scalars(
            select(ChatMessage.id).where(ChatMessage.session_id == session.id)
        )),
    )

    external_object_cleanup.enqueue_session_media(db, session.id)
    db.delete(session)



def session_title_finalized(db: Session, session: ChatSession) -> bool:
    """Return whether the session title was finalized by the LLM summarizer.

    A finalized title must never be overwritten by browser snapshot writes;
    only an explicit user rename may replace it (via ``update_session`` with
    ``override_frozen_title=True``).
    """
    return bool(session.title_finalized)


def finalize_session_title(
    db: Session,
    session: ChatSession,
    *,
    title: str | None,
) -> str | None:
    """Persist the LLM-summarized session title once and freeze it atomically.

    Returns the stored title when this call won the update, or ``None`` when
    no title was supplied or another writer already finalized the session.
    The conditional UPDATE guards against two concurrent turns finalizing the
    same session with different titles.
    """
    normalized = (title or "").strip()[:255]
    if not normalized or session.title_finalized:
        return None
    from sqlalchemy import update

    rowcount = db.execute(
        update(ChatSession)
        .where(ChatSession.id == session.id, ChatSession.title_finalized.is_(False))
        .values(
            title=normalized,
            title_finalized=True,
            updated_at=now(),
        )
    ).rowcount
    db.flush()
    if rowcount != 1:
        db.expire_all()
        return None
    session.title = normalized
    session.title_finalized = True
    return normalized


def fork_session(
    db: Session,
    *,
    source_session: ChatSession,
    owner_user_id: str,
    fork_message_id: str,
    title: str | None = None,
) -> ChatSession:
    """Fork a chat session up to (and including) the selected message.

    Copies the session metadata and every message up to the fork point into a
    brand-new session with a fresh Pi runtime identity (``agent_session_id``
    is cleared). ``forked_from_*`` records the lineage so the first follow-up
    can replay the prefix as context.
    """
    messages = active_messages(source_session.messages or [])
    if not messages:
        raise ValueError("源会话没有可分支的消息")
    fork_index = next(
        (index for index, item in enumerate(messages) if item.id == fork_message_id),
        None,
    )
    if fork_index is None:
        raise ValueError("分支点消息不存在于源会话")
    prefix = messages[: fork_index + 1]

    stamp = now()
    base_title = (title or "").strip() or source_session.title.strip() or "新对话"
    session = ChatSession(
        id=f"chs_{new_token(10)}",
        owner_user_id=owner_user_id,
        title=f"{base_title}（分支）",
        messages_revision=1,
        title_finalized=bool(source_session.title_finalized),
        agent_session_id=None,
        preferred_model=source_session.preferred_model,
        category_id=source_session.category_id,
        pinned=False,
        pinned_at=None,
        archived=False,
        archived_at=None,
        writing_project_id=source_session.writing_project_id,
        forked_from_session_id=source_session.id,
        forked_from_message_id=fork_message_id,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(session)
    db.flush()

    # Copy the prefix messages preserving role/content/scope but minting fresh
    # ids so the two sessions never share message rows.
    used_ids: set[str] = set()
    for item in prefix:
        message_id = _new_message_id(db, used_ids)
        used_ids.add(message_id)
        db.add(
            ChatMessage(
                id=message_id,
                session_id=session.id,
                owner_user_id=owner_user_id,
                role=item.role,
                content=item.content,
                thinking=item.thinking,
                parts_json=item.parts_json,
                space_ids_json=item.space_ids_json or "[]",
                document_ids_json=item.document_ids_json or "[]",
                references_json=item.references_json or "[]",
                session_references_json=item.session_references_json or "[]",
                writing_references_json=item.writing_references_json or "[]",
                images_json=item.images_json or "[]",
                retrieval_summary=item.retrieval_summary,
                retrieval_trace_json=item.retrieval_trace_json,
                writing_project_id=item.writing_project_id,
                writing_context_json=item.writing_context_json,
                usage=item.usage,
                created_at=item.created_at,
            )
        )
    db.flush()
    db.expire(session)
    return session


def _new_message_id(db: Session, reserved_ids: set[str]) -> str:
    while True:
        candidate = f"chm_{new_token(10)}"
        if candidate in reserved_ids:
            continue
        if db.get(ChatMessage, candidate) is not None:
            continue
        reserved_ids.add(candidate)
        return candidate


def _refresh_session_for_message_write(
    db: Session, session_id: str
) -> ChatSession:
    statement = (
        select(ChatSession)
        .options(selectinload(ChatSession.category), selectinload(ChatSession.messages))
        .where(ChatSession.id == session_id)
        .execution_options(populate_existing=True)
    )
    refreshed = db.scalar(statement)
    if refreshed is None:
        raise ValueError("chat session does not exist")
    return refreshed


def claim_messages_revision(
    db: Session, session_id: str, base_revision: int
) -> int | None:
    """Atomically claim a specific message revision for one transaction."""
    expected = int(base_revision)
    result = db.execute(
        update(ChatSession)
        .where(
            ChatSession.id == session_id,
            ChatSession.messages_revision == expected,
        )
        .values(
            messages_revision=ChatSession.messages_revision + 1,
            updated_at=now(),
        )
        .execution_options(synchronize_session=False)
        .returning(ChatSession.messages_revision)
    )
    return result.scalar_one_or_none()


def _claim_next_messages_revision(db: Session, session_id: str) -> ChatSession:
    """Atomically claim the next revision for a legacy, revision-less writer."""
    result = db.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(
            messages_revision=ChatSession.messages_revision + 1,
            updated_at=now(),
        )
        .execution_options(synchronize_session=False)
        .returning(ChatSession.messages_revision)
    )
    if result.scalar_one_or_none() is None:
        raise ValueError("chat session does not exist")
    return _refresh_session_for_message_write(db, session_id)


def _advance_messages_revision(session: ChatSession) -> int:
    revision = int(getattr(session, "messages_revision", 0) or 0) + 1
    session.messages_revision = revision
    return revision


def advance_messages_revision_for_session(
    db: Session, session_id: str
) -> int | None:
    """Advance a session revision for an in-place persisted message update."""
    result = db.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(
            messages_revision=ChatSession.messages_revision + 1,
            updated_at=now(),
        )
        .execution_options(synchronize_session=False)
        .returning(ChatSession.messages_revision)
    )
    return result.scalar_one_or_none()


def _richer_text(current: object, incoming: object) -> str | None:
    """Keep the more complete text when two writers update one message id."""
    current_text = str(current or "")
    incoming_text = str(incoming or "")
    if not current_text:
        return incoming_text or None
    if not incoming_text:
        return current_text
    if len(incoming_text) > len(current_text):
        return incoming_text
    if len(current_text) > len(incoming_text):
        return current_text
    return max(current_text, incoming_text)


def _server_text(current: object, incoming: object) -> str | None:
    """Keep a non-empty persisted value authoritative over a local replay."""
    current_text = str(current or "")
    incoming_text = str(incoming or "")
    return current_text or incoming_text or None


def _json_key(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _merge_json_list(
    current: object,
    incoming: object,
    *,
    key: Any | None = None,
) -> list[Any]:
    """Union JSON list values while preserving stable order and richer rows."""
    current_items = current if isinstance(current, list) else []
    incoming_items = incoming if isinstance(incoming, list) else []
    merged: list[Any] = []
    positions: dict[str, int] = {}
    for item in [*current_items, *incoming_items]:
        item_key = str(key(item)) if key is not None else _json_key(item)
        position = positions.get(item_key)
        if position is None:
            positions[item_key] = len(merged)
            merged.append(item)
            continue
        previous = merged[position]
        if isinstance(previous, dict) and isinstance(item, dict):
            combined = dict(previous)
            for name, value in item.items():
                if name not in combined or not combined[name]:
                    combined[name] = value
                elif isinstance(combined[name], str) and isinstance(value, str):
                    combined[name] = _server_text(combined[name], value)
            merged[position] = combined
        elif isinstance(previous, str) and isinstance(item, str):
            merged[position] = _server_text(previous, item)
    return merged


def _part_key(part: object) -> str:
    if not isinstance(part, dict):
        return _json_key(part)
    kind = str(part.get("type") or "")
    if kind == "tool":
        call_id = str(part.get("tool_call_id") or part.get("toolCallId") or "")
        if call_id:
            return f"tool:{call_id}"
    if kind == "error":
        error_id = str(part.get("error_id") or part.get("errorId") or "")
        if error_id:
            return f"error:{error_id}"
    if kind == "file":
        href = str(part.get("href") or "")
        if href:
            return f"file:{href}"
    return _json_key(part)


def _merge_parts(current: object, incoming: object) -> list[dict[str, Any]]:
    values = _merge_json_list(current, incoming, key=_part_key)
    out: list[dict[str, Any]] = []
    status_priority = {"running": 0, "done": 1, "error": 2}
    for value in values:
        if not isinstance(value, dict):
            continue
        item = dict(value)
        if item.get("type") == "tool":
            current_matches = [
                candidate
                for candidate in (current if isinstance(current, list) else [])
                if isinstance(candidate, dict)
                and _part_key(candidate) == _part_key(value)
            ]
            incoming_matches = [
                candidate
                for candidate in (incoming if isinstance(incoming, list) else [])
                if isinstance(candidate, dict)
                and _part_key(candidate) == _part_key(value)
            ]
            server_failed_output = next(
                (
                    str(candidate.get("output_text") or "")
                    for candidate in current_matches
                    if candidate.get("status") == "error"
                    and str(candidate.get("output_text") or "").strip()
                ),
                "",
            )
            placeholder_replay = any(
                candidate.get("status") == "error"
                and str(candidate.get("output_text") or "")
                == _CUSTOMER_TOOL_FAILURE_TEXT
                for candidate in incoming_matches
            )
            for source, candidate in [
                *(("current", candidate) for candidate in current_matches),
                *(("incoming", candidate) for candidate in incoming_matches),
            ]:
                for name in ("args_text", "argsText", "output_text", "outputText"):
                    value_text = candidate.get(name)
                    if not isinstance(value_text, str):
                        continue
                    if (
                        source == "incoming"
                        and server_failed_output
                        and placeholder_replay
                        and name in {"output_text", "outputText"}
                    ):
                        continue
                    item[name] = _richer_text(item.get(name), value_text)
                status = str(candidate.get("status") or "")
                if status_priority.get(status, -1) > status_priority.get(
                    str(item.get("status") or ""), -1
                ):
                    item["status"] = status
            if server_failed_output and placeholder_replay:
                item["output_text"] = server_failed_output
        out.append(item)
    return out


def _merge_usage(
    db: Session,
    current: object,
    incoming: object,
) -> dict[str, Any] | None:
    incoming_snapshot_keys = frozenset(
        key
        for key in CONTEXT_SNAPSHOT_KEYS
        if isinstance(incoming, dict) and key in incoming
    )
    current_usage = normalize_usage(current)
    incoming_usage = _priced_usage(db, incoming)
    if current_usage is None:
        return incoming_usage
    if incoming_usage is None:
        return current_usage
    merged = dict(current_usage)
    numeric_keys = {
        "input_tokens",
        "input_uncached_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
    }
    for name, value in incoming_usage.items():
        if name == "calls":
            merged[name] = _merge_json_list(
                merged.get(name),
                value,
                key=lambda call: (
                    call.get("purpose"),
                    call.get("provider"),
                    call.get("model"),
                )
                if isinstance(call, dict)
                else _json_key(call),
            )
        elif name in CONTEXT_SNAPSHOT_MINIMUMS:
            # A context measurement is a snapshot, not a cumulative counter.
            # Compaction can make it smaller or explicitly unknown, and a model
            # switch changes the window. Legacy payloads that omit a key do not
            # carry a replacement snapshot for it.
            if name in incoming_snapshot_keys:
                merged[name] = value
        elif name in numeric_keys:
            merged[name] = max(_coerce_int(merged.get(name)), _coerce_int(value))
        elif name == "cost":
            try:
                merged[name] = str(
                    max(
                        Decimal(str(merged.get(name) or 0)),
                        Decimal(str(value or 0)),
                    )
                )
            except (InvalidOperation, ValueError, TypeError):
                merged[name] = _server_text(merged.get(name), value) or "0"
        elif not merged.get(name):
            merged[name] = value
        elif isinstance(merged.get(name), str) and isinstance(value, str):
            merged[name] = _server_text(merged[name], value)
    return normalize_usage(merged)


def _deep_merge_server_value(current: object, incoming: object) -> object:
    if isinstance(current, dict) and isinstance(incoming, dict):
        merged = dict(current)
        for name, value in incoming.items():
            if name not in merged or merged[name] is None:
                merged[name] = value
            else:
                merged[name] = _deep_merge_server_value(merged[name], value)
        return merged
    if isinstance(current, list) and isinstance(incoming, list):
        return _merge_json_list(current, incoming)
    if isinstance(current, bool) and isinstance(incoming, bool):
        return current or incoming
    if isinstance(current, (int, float)) and not isinstance(current, bool) and isinstance(
        incoming, (int, float)
    ) and not isinstance(incoming, bool):
        return max(current, incoming)
    if isinstance(current, str) and isinstance(incoming, str):
        return _server_text(current, incoming)
    return current if current is not None else incoming


def _merge_trace(current: object, incoming: object) -> dict[str, Any] | None:
    current_trace = _load_trace(current if isinstance(current, str) else None)
    incoming_trace = (
        _load_trace(_dump_trace(incoming))
        if isinstance(incoming, dict)
        else None
    )
    if current_trace is None:
        return incoming_trace
    if incoming_trace is None:
        return current_trace
    merged = _deep_merge_server_value(current_trace, incoming_trace)
    return cast(dict[str, Any], merged)


def _merge_existing_message_payload(
    db: Session,
    current: ChatMessage,
    payload: dict[str, Any],
) -> bool:
    # 冻结回答只允许服务端专用收尾补充；浏览器旧快照不能改正文或账单。
    if reply_boundary(current) is not None:
        return False
    values: dict[str, Any] = {}
    if "content" in payload:
        values["content"] = _server_text(current.content, payload.get("content")) or ""
    if "thinking" in payload:
        values["thinking"] = _server_text(current.thinking, payload.get("thinking"))
    if "parts" in payload:
        parts = _merge_parts(
            _normalize_parts(_load_list(current.parts_json)),
            _normalize_parts(payload.get("parts") or []),
        )
        values["parts_json"] = _dump(parts) if parts else current.parts_json
    for key, field in (
        ("space_ids", "space_ids_json"),
        ("document_ids", "document_ids_json"),
        ("references", "references_json"),
        ("session_references", "session_references_json"),
        ("writing_references", "writing_references_json"),
    ):
        if key in payload:
            values[field] = _dump(
                _merge_json_list(_load_list(getattr(current, field)), payload.get(key) or [])
            )
    if "images" in payload:
        values["images_json"] = _dump(
            _normalize_images(
                _merge_json_list(
                    _load_list(current.images_json),
                    _normalize_images(payload.get("images") or []),
                )
            )
        )
    if "retrieval_summary" in payload:
        values["retrieval_summary"] = _server_text(
            current.retrieval_summary, payload.get("retrieval_summary")
        )
    if "retrieval_trace" in payload:
        merged_trace = _merge_trace(current.retrieval_trace_json, payload.get("retrieval_trace"))
        values["retrieval_trace_json"] = _dump_trace(merged_trace)
    if "writing_project_id" in payload:
        values["writing_project_id"] = current.writing_project_id or payload.get(
            "writing_project_id"
        )
    if "usage" in payload:
        usage = _merge_usage(db, current.usage, payload.get("usage"))
        values["usage"] = _dump(usage) if usage else current.usage
    if "dispatch_id" in payload:
        values["dispatch_id"] = current.dispatch_id or payload.get("dispatch_id")
    changed = any(getattr(current, field) != value for field, value in values.items())
    if values.get("content", current.content) != current.content:
        from app.repositories import user_memory

        user_memory.invalidate_message_sources(
            db,
            owner_user_id=current.owner_user_id,
            message_ids=[current.id],
            surviving_hashes={current.id: user_memory.source_hash(values["content"])},
        )
    if changed:
        for field, value in values.items():
            setattr(current, field, value)
    return changed


def _merge_legacy_messages(
    db: Session,
    session: ChatSession,
    payloads: list[dict[str, Any]],
) -> list[ChatMessage]:
    session = _claim_next_messages_revision(db, session.id)
    requested_ids = {
        str(item.get("id") or "").strip()
        for item in payloads
        if str(item.get("id") or "").strip()
    }
    existing = (
        {
            item.id: item
            for item in db.scalars(
                select(ChatMessage).where(ChatMessage.id.in_(requested_ids))
            ).all()
        }
        if requested_ids
        else {}
    )
    missing: list[dict[str, Any]] = []
    changed_messages: list[ChatMessage] = []
    for item in payloads:
        message_id = str(item.get("id") or "").strip()
        current = existing.get(message_id) if message_id else None
        if current is None or current.session_id != session.id:
            missing.append(item)
            continue
        if current.owner_user_id != session.owner_user_id:
            missing.append(item)
            continue
        if current.role != str(item.get("role") or ""):
            raise ValueError("chat message id conflicts with another message")
        if _merge_existing_message_payload(db, current, item):
            changed_messages.append(current)

    # Flush same-id updates before refreshing the session to append new rows;
    # otherwise ``populate_existing`` could reload the old database value and
    # silently discard the in-memory merge.
    if changed_messages:
        db.flush()
    created = (
        append_messages(db, session, missing, advance_revision=False)
        if missing
        else []
    )
    if created or changed_messages:
        session.updated_at = now()
        db.flush()
        if changed_messages:
            from app.repositories.personal_search import enqueue_chat_history_index_jobs

            enqueue_chat_history_index_jobs(db, changed_messages)
    return created


def replace_messages(
    db: Session,
    session: ChatSession,
    payloads: list[dict[str, Any]],
    *,
    base_revision: int | None = None,
) -> list[ChatMessage]:
    if base_revision is None:
        # Legacy clients do not have a concurrency token. They may only add
        # or replay rows by id; never let their partial snapshot delete rows
        # written by another tab or server worker.
        return _merge_legacy_messages(db, session, payloads)

    claimed_revision = claim_messages_revision(db, session.id, base_revision)
    if claimed_revision is None:
        current = _refresh_session_for_message_write(db, session.id)
        current_page = get_session_message_page(
            db,
            session_id=current.id,
            owner_user_id=current.owner_user_id,
        )
        raise ChatMessagesRevisionConflict(
            current_revision=int(getattr(current, "messages_revision", 0) or 0),
            current_snapshot=session_detail_dict(
                current,
                message_page=current_page,
            ),
        )
    session = _refresh_session_for_message_write(db, session.id)

    current_messages = list(
        db.scalars(select(ChatMessage).where(ChatMessage.session_id == session.id))
    )
    # 即使客户端持有最新 revision，旧的完整快照也不能删除或覆写已冻结段。
    frozen = {
        message.id: message for message in current_messages
        if message.owner_user_id == session.owner_user_id and reply_boundary(message)
        and (not message.variant_group_id or message.variant_active)
    }
    source_ids = {
        str(source or "")
        for message in frozen.values()
        for boundary in [reply_boundary(message) or {}]
        for source in [
            boundary.get("source_user_message_id"),
            boundary.get("root_user_id"),
            *(boundary.get("source_user_message_ids") or []),
        ]
    }
    frozen.update({
        message.id: message for message in current_messages
        if message.id in source_ids and message.role == "user"
        and message.owner_user_id == session.owner_user_id
    })
    payloads = [
        _frozen_reply_payload(frozen[str(item.get("id"))])
        if str(item.get("id")) in frozen else item
        for item in payloads
    ]
    incoming_frozen_ids = {str(item.get("id")) for item in payloads}
    payloads.extend(
        _frozen_reply_payload(message) for key, message in frozen.items()
        if key not in incoming_frozen_ids
    )
    if frozen:
        payloads.sort(key=lambda item: _message_datetime(item.get("created_at")) or now())
    from app.repositories import user_memory

    # 比较语义输入；重复 ID 仅取第一条，与后续碰撞时分配新 ID 的语义一致。
    incoming_by_id: dict[str, dict[str, Any]] = {}
    for item in payloads:
        message_id = str(item.get("id") or "").strip()
        if message_id:
            incoming_by_id.setdefault(message_id, item)
    # 浏览器只收到模板 revision 投影，不持有标准内容；快照替换不得擦除服务端元数据。
    for message in current_messages:
        incoming = incoming_by_id.get(message.id)
        if (
            incoming is None
            or message.owner_user_id != session.owner_user_id
            or str(incoming.get("role") or "") != message.role
        ):
            continue
        internal = [
            part for part in _normalize_parts(_load_list(message.parts_json))
            if part["type"] == "presentation_template"
        ]
        if internal:
            incoming["parts"] = internal + [
                part for part in _normalize_parts(incoming.get("parts") or [])
                if part["type"] != "presentation_template"
            ]
    surviving_hashes = {
        message.id: user_memory.source_hash(message.content)
        for message in current_messages
        if message.owner_user_id == session.owner_user_id
        and message.role == "user"
        and (incoming := incoming_by_id.get(message.id)) is not None
        and str(incoming.get("role") or "") == "user"
        and str(incoming.get("content") or "") == message.content
    }
    user_memory.invalidate_message_sources(
        db,
        owner_user_id=session.owner_user_id,
        message_ids=[message.id for message in current_messages],
        surviving_hashes=surviving_hashes,
    )
    # 浏览器只持有活动回答；完整快照仍需保留存续变体组的非活动历史。
    incoming_ids = {str(item.get("id") or "").strip() for item in payloads}
    incoming_ids.discard("")
    surviving_variants: dict[str, tuple[str, int]] = {
        str(message.id): (
            str(message.variant_group_id),
            _coerce_int(getattr(message, "variant_index", 0)),
        )
        for message in current_messages
        if message.id in incoming_ids and message.variant_group_id
    }
    surviving_groups = {group_id for group_id, _ in surviving_variants.values()}
    # 同 ID 原地写回，保留消息身份及原生 PPT 文件等外键依赖；仅删除快照真正移除的行。
    retained_messages = {
        message.id: message for message in current_messages
        if message.id in incoming_ids and message.owner_user_id == session.owner_user_id
    }
    for message in current_messages:
        if (
            message.variant_group_id
            and str(message.variant_group_id) in surviving_groups
            and message.id not in incoming_ids
        ):
            # Inactive sibling: keep it so switching back to an earlier answer
            # still works after a client snapshot write.
            continue
        if message.id in retained_messages:
            continue
        db.delete(message)
    db.flush()
    created = _write_messages(
        db, session, payloads, advance_revision=False, replace_existing=retained_messages,
    )
    for message in created:
        preserved = surviving_variants.get(str(message.id))
        if preserved is None:
            continue
        group_id, variant_index = preserved
        message.variant_group_id = group_id
        message.variant_index = variant_index
        message.variant_active = True
    if surviving_variants:
        db.flush()
    return created


def append_messages(
    db: Session,
    session: ChatSession,
    payloads: list[dict[str, Any]],
    *,
    advance_revision: bool = True,
) -> list[ChatMessage]:
    return _write_messages(db, session, payloads, advance_revision=advance_revision)


def _write_messages(
    db: Session,
    session: ChatSession,
    payloads: list[dict[str, Any]],
    *,
    advance_revision: bool,
    replace_existing: dict[str, ChatMessage] | None = None,
) -> list[ChatMessage]:
    """新增与完整快照共用字段构造、ID 碰撞处理及严格时间顺序。"""
    if advance_revision:
        session = _claim_next_messages_revision(db, session.id)
    else:
        session = _refresh_session_for_message_write(db, session.id)
    requested_ids = {str(item.get("id") or "").strip() for item in payloads}
    requested_ids.discard("")
    existing_ids: set[str] = set()
    if requested_ids:
        existing_ids = set(
            db.scalars(
                select(ChatMessage.id).where(ChatMessage.id.in_(requested_ids))
            ).all()
        )
    reserved_ids = requested_ids | existing_ids
    if replace_existing:
        existing_ids.difference_update(replace_existing)
    used_ids: set[str] = set()
    created: list[ChatMessage] = []
    # Append-only writes can carry a browser timestamp older than the durable
    # snapshot (clock skew or a delayed retry). Keep every new row after the
    # session's latest message, not only after the previous item in this batch.
    last_stamp = max(
        (
            as_utc(stamp)
            for stamp in db.scalars(
                select(ChatMessage.created_at).where(
                    ChatMessage.session_id == session.id,
                    *([ChatMessage.id.not_in(replace_existing)] if replace_existing else []),
                )
            ).all()
        ),
        default=None,
    )
    for item in payloads:
        parsed_stamp = _message_datetime(item.get("created_at"))
        stamp = as_utc(parsed_stamp) if parsed_stamp is not None else now()
        if last_stamp is not None and stamp <= last_stamp:
            stamp = last_stamp + timedelta(milliseconds=1)
        last_stamp = stamp
        message_id = str(item.get("id") or "").strip()
        if not message_id or message_id in used_ids or message_id in existing_ids:
            message_id = _new_message_id(db, reserved_ids)
        used_ids.add(message_id)
        parts = _normalize_parts(item.get("parts") or [])
        values = dict(
            role=str(item["role"]),
            content=str(item.get("content") or ""),
            thinking=item.get("thinking"),
            parts_json=_dump(parts) if parts else None,
            space_ids_json=_dump(item.get("space_ids") or []),
            document_ids_json=_dump(item.get("document_ids") or []),
            references_json=_dump(item.get("references") or []),
            session_references_json=_dump(item.get("session_references") or []),
            writing_references_json=_dump(item.get("writing_references") or []),
            images_json=_dump(_normalize_images(item.get("images") or [])),
            retrieval_summary=item.get("retrieval_summary"),
            retrieval_trace_json=_dump_trace(item.get("retrieval_trace")),
            dispatch_id=item.get("dispatch_id"),
            writing_project_id=item.get("writing_project_id"),
            writing_context_json=(
                _dump(item["writing_context"]) if item.get("writing_context") else None
            ),
            usage=_dump(usage)
            if (usage := _priced_usage(db, item.get("usage")))
            else None,
            created_at=stamp,
        )
        message = replace_existing.get(message_id) if replace_existing is not None else None
        if message is None:
            message = ChatMessage(
                id=message_id, session_id=session.id,
                owner_user_id=session.owner_user_id, **values,
            )
        else:
            for field, value in values.items():
                setattr(message, field, value)
        db.add(message)
        created.append(message)
    session.updated_at = now()
    db.flush()
    if created and advance_revision:
        _advance_messages_revision(session)
    from app.repositories.personal_search import enqueue_chat_history_index_jobs

    enqueue_chat_history_index_jobs(db, created)
    return created


def append_missing_messages(
    db: Session,
    session: ChatSession,
    payloads: list[dict[str, Any]],
    *,
    preserve_existing: bool = False,
) -> list[ChatMessage]:
    """Append messages idempotently without replacing an existing snapshot."""
    if not payloads:
        return []
    session = _claim_next_messages_revision(db, session.id)
    message_ids: list[str] = []
    for item in payloads:
        message_id = str(item.get("id") or "").strip()
        if not message_id:
            raise ValueError("chat message id is required")
        message_ids.append(message_id)

    existing = {
        item.id: item
        for item in db.scalars(
            select(ChatMessage).where(ChatMessage.id.in_(message_ids))
        ).all()
    }
    missing: list[dict[str, Any]] = []
    for item, message_id in zip(payloads, message_ids, strict=True):
        current = existing.get(message_id)
        if current is None:
            missing.append(item)
            continue
        if (
            current.session_id != session.id
            or current.owner_user_id != session.owner_user_id
        ):
            missing.append(item)
            continue
        if current.role != str(item.get("role") or "") or (
            not preserve_existing and current.content != str(item.get("content") or "")
        ):
            raise ValueError("chat message id conflicts with another message")
    return append_messages(db, session, missing, advance_revision=False) if missing else []


def _apply_assistant_message_payload(
    db: Session,
    session: ChatSession,
    current: ChatMessage,
    payload: dict[str, Any],
) -> None:
    session = _claim_next_messages_revision(db, session.id)
    existing = db.get(ChatMessage, current.id)
    if existing is None:
        raise ValueError("chat message does not exist")
    current = existing
    parts = _normalize_parts(payload.get("parts") or [])
    current.content = str(payload.get("content") or "")
    current.thinking = payload.get("thinking")
    current.parts_json = _dump(parts) if parts else None
    current.space_ids_json = _dump(payload.get("space_ids") or [])
    current.document_ids_json = _dump(payload.get("document_ids") or [])
    current.references_json = _dump(payload.get("references") or [])
    current.session_references_json = _dump(payload.get("session_references") or [])
    current.writing_references_json = _dump(payload.get("writing_references") or [])
    current.images_json = _dump(_normalize_images(payload.get("images") or []))
    current.retrieval_summary = payload.get("retrieval_summary")
    current.retrieval_trace_json = _dump_trace(payload.get("retrieval_trace"))
    current.dispatch_id = payload.get("dispatch_id")
    current.writing_project_id = payload.get("writing_project_id")
    current.usage = (
        _dump(usage) if (usage := _priced_usage(db, payload.get("usage"))) else None
    )
    session.updated_at = now()
    _advance_messages_revision(session)
    db.flush()

    from app.repositories.personal_search import enqueue_chat_history_index_jobs

    enqueue_chat_history_index_jobs(db, [current])


def replace_dispatch_assistant_message(
    db: Session,
    session: ChatSession,
    payload: dict[str, Any],
) -> bool:
    """Replace a persisted clarification with its resolved chat reply.

    The dispatch id, owner, session, message id, and assistant role must all
    match. This narrow update avoids weakening append-only idempotency for
    ordinary chat persistence.
    """
    message_id = str(payload.get("id") or "").strip()
    dispatch_id = str(payload.get("dispatch_id") or "").strip()
    if not message_id or not dispatch_id:
        return False
    if str(payload.get("role") or "") != "assistant":
        raise ValueError("dispatch replacement requires an assistant message")
    current = db.get(ChatMessage, message_id)
    if current is None:
        return False
    if (
        current.session_id != session.id
        or current.owner_user_id != session.owner_user_id
        or current.role != "assistant"
        or current.dispatch_id != dispatch_id
    ):
        raise ValueError("dispatch assistant message does not match clarification")

    _apply_assistant_message_payload(db, session, current, payload)
    return True


def replace_incomplete_assistant_message(
    db: Session,
    session: ChatSession,
    payload: dict[str, Any],
    *,
    recovery_user_message_id: str | None = None,
) -> bool:
    """Replace only a proven failed/partial assistant reply during stable-id retry."""
    message_id = str(payload.get("id") or "").strip()
    if not message_id:
        return False
    if str(payload.get("role") or "") != "assistant":
        raise ValueError("retry replacement requires an assistant message")
    current = db.get(ChatMessage, message_id)
    if current is None:
        return False
    if (
        current.session_id != session.id
        or current.owner_user_id != session.owner_user_id
        or current.role != "assistant"
    ):
        raise ValueError("retry assistant message does not match session")
    usage = normalize_usage(current.usage)
    raw_parts = _load_list(current.parts_json)
    has_structured_error = any(
        isinstance(part, dict) and part.get("type") == "error" for part in raw_parts
    )
    if (
        (usage is not None and usage.get("status") != "failed")
        or (usage is None and not has_structured_error)
    ):
        return False
    current_dispatch = str(current.dispatch_id or "").strip()
    incoming_dispatch = str(payload.get("dispatch_id") or "").strip()
    if current_dispatch != incoming_dispatch and (current_dispatch or not incoming_dispatch):
        raise ValueError("retry assistant dispatch does not match")
    if incoming_dispatch and (
        current_dispatch != incoming_dispatch or recovery_user_message_id
    ):
        recovery_user_id = str(recovery_user_message_id or "").strip()
        recovery_user = db.get(ChatMessage, recovery_user_id) if recovery_user_id else None
        if (
            recovery_user is None
            or recovery_user.session_id != session.id
            or recovery_user.owner_user_id != session.owner_user_id
            or recovery_user.role != "user"
            or recovery_user.created_at >= current.created_at
        ):
            raise ValueError("retry assistant dispatch lacks an owned user binding")
        recovery_user_dispatch = str(recovery_user.dispatch_id or "").strip()
        if recovery_user_dispatch and recovery_user_dispatch != incoming_dispatch:
            raise ValueError("retry user dispatch does not match")
        intervening_message = db.scalar(
            select(ChatMessage.id)
            .where(
                ChatMessage.session_id == session.id,
                ChatMessage.owner_user_id == session.owner_user_id,
                ChatMessage.created_at > recovery_user.created_at,
                ChatMessage.created_at < current.created_at,
            )
            .limit(1)
        )
        if intervening_message is not None:
            raise ValueError("retry assistant is not paired with the recovery user")
        recovery_user.dispatch_id = incoming_dispatch
        # 后续 revision claim 会刷新 ORM 状态，先 flush 身份绑定但不提交事务。
        db.flush()

    _apply_assistant_message_payload(db, session, current, payload)
    return True


def register_message_variant(
    db: Session,
    session: ChatSession,
    *,
    source_message_id: str,
    new_message_id: str,
    activate: bool = True,
) -> bool:
    """Record a regenerated assistant reply as a new variant of the old one.

    ``source_message_id`` is the answer being replaced and ``new_message_id``
    the freshly persisted reply. The superseded answer stays in the table as an
    inactive sibling so the user can switch back to it and so every attempt's
    token usage keeps counting toward cost reporting.

    ``activate=False`` keeps the previous answer visible, which is what a failed
    regeneration must do: the user asked for a better answer, not for their
    working answer to be replaced by an error notice.

    Returns ``False`` when the pair does not describe a replaceable answer;
    callers treat that as "not a regeneration" rather than as an error.
    """
    source = db.get(ChatMessage, source_message_id)
    new = db.get(ChatMessage, new_message_id)
    if source is None or new is None:
        return False
    if (
        source.id == new.id
        or source.session_id != session.id
        or new.session_id != session.id
        or source.owner_user_id != session.owner_user_id
        or new.owner_user_id != session.owner_user_id
        or source.role != "assistant"
        or new.role != "assistant"
    ):
        return False
    # A retried regeneration reuses the same new id; an already inactive source
    # means this pair was registered before, so leave the group untouched.
    if not bool(getattr(source, "variant_active", True)):
        return False

    group_id = str(getattr(source, "variant_group_id", None) or source.id)
    if not getattr(source, "variant_group_id", None):
        # The original answer joins its own group at index 0.
        source.variant_group_id = group_id
        source.variant_index = 0
    db.flush()

    siblings = list(
        db.scalars(
            select(ChatMessage).where(
                ChatMessage.variant_group_id == group_id,
                ChatMessage.id != new.id,
            )
        )
    )
    if activate:
        for sibling in siblings:
            sibling.variant_active = False
    new.variant_group_id = group_id
    new.variant_index = (
        max(
            (
                _coerce_int(getattr(sibling, "variant_index", 0))
                for sibling in siblings
            ),
            default=0,
        )
        + 1
    )
    new.variant_active = bool(activate)
    _advance_messages_revision(session)
    db.flush()
    return True


def activate_message_variant(
    db: Session,
    session: ChatSession,
    *,
    message_id: str,
) -> bool:
    """Select one variant of a message group as the visible answer."""
    target = db.get(ChatMessage, message_id)
    if target is None:
        return False
    if (
        target.session_id != session.id
        or target.owner_user_id != session.owner_user_id
    ):
        return False
    group_id = str(getattr(target, "variant_group_id", None) or "")
    if not group_id:
        return False
    members = list(
        db.scalars(
            select(ChatMessage).where(ChatMessage.variant_group_id == group_id)
        )
    )
    if not any(str(member.id) == str(target.id) for member in members):
        return False
    changed = False
    for member in members:
        selected = str(member.id) == str(target.id)
        if bool(getattr(member, "variant_active", True)) != selected:
            changed = True
        member.variant_active = selected
    if not changed:
        return False
    _advance_messages_revision(session)
    db.flush()
    return True


def _message_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def persist_chat_turn(
    db: Session,
    *,
    session_id: str,
    owner_user_id: str,
    payloads: list[dict[str, Any]],
    title: str | None = None,
    agent_session_id: str | None = None,
    clear_agent_session: bool = False,
    writing_project_id: str | None = None,
    preferred_model: str | None = None,
    replace: bool = False,
    base_revision: int | None = None,
    snapshot_complete: bool = False,
    preserve_existing: bool = True,
) -> ChatSession | None:
    """Persist an owner-scoped chat turn in the caller's transaction.

    A valid missing ``chs_*`` session is recreated with the requested owner.
    Append-only writes treat existing message ids as idempotency keys; full
    replacements use a session-row revision compare-and-swap when provided.
    """
    normalized_session_id = (session_id or "").strip()
    session = get_owned_session(
        db,
        normalized_session_id,
        owner_user_id,
    )
    if session is None:
        session = restore_session_for_owner(
            db,
            session_id=normalized_session_id,
            owner_user_id=owner_user_id,
            title=title,
            agent_session_id=agent_session_id,
            writing_project_id=writing_project_id,
            preferred_model=preferred_model,
        )
    if session is None:
        return None

    if replace:
        replace_messages(
            db,
            session,
            payloads,
            base_revision=base_revision if snapshot_complete else None,
        )
    else:
        append_missing_messages(
            db,
            session,
            payloads,
            preserve_existing=preserve_existing,
        )
    update_session(
        db,
        session,
        title=title,
        agent_session_id=agent_session_id,
        clear_agent_session=clear_agent_session,
        writing_project_id=writing_project_id,
        preferred_model=preferred_model,
    )

    db.flush()
    db.expire_all()

    # ``selectinload`` may otherwise reuse a relationship collection loaded
    # before a same-id legacy merge.  Refresh the complete snapshot before the
    # response so the caller sees the values that were actually flushed.
    return _refresh_session_for_message_write(db, normalized_session_id)


def _customer_safe_content(
    *,
    role: object,
    content: object,
    raw_parts: list[Any],
    parts: list[dict[str, Any]],
) -> str:
    content_text = str(content or "")
    if str(role or "") != "assistant" or not content_text.strip():
        return content_text
    raw_errors: list[tuple[str, str]] = []
    for item in raw_parts:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "error":
            raw_errors.append(("error", str(item.get("text") or "").strip()))
            continue
        if item.get("type") == "tool" and item.get("status") == "error":
            raw_errors.append(
                (
                    "tool",
                    str(
                        item.get("output_text") or item.get("outputText") or ""
                    ).strip(),
                )
            )
    stripped_content = content_text.strip()
    matched_kind: str | None = None
    preserved_content = ""
    for error_kind, raw_text in raw_errors:
        if not raw_text:
            continue
        if raw_text == stripped_content:
            matched_kind = error_kind
            break
        error_suffix = f"\n{raw_text}"
        if stripped_content.endswith(error_suffix):
            matched_kind = error_kind
            preserved_content = stripped_content[: -len(error_suffix)].rstrip()
            break
    if matched_kind is None:
        return content_text
    if preserved_content:
        return preserved_content
    if matched_kind == "tool":
        return _CUSTOMER_TOOL_FAILURE_TEXT
    safe_error = next(
        (
            str(part.get("text") or "")
            for part in parts
            if part.get("type") == "error" and part.get("text")
        ),
        "",
    )
    return safe_error or trusted_chat_error_message("", error_kind="runtime")


def _looks_like_legacy_interface_error(value: object) -> bool:
    """识别旧发布快照里缺少结构化 part 的明显接口错误载荷。"""
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if not isinstance(parsed, dict):
        return False
    fields = {str(key).strip().lower(): item for key, item in parsed.items()}
    if any(fields.get(key) for key in ("error", "errors", "error_code", "errorcode")):
        return True
    status = fields.get("status_code", fields.get("status", fields.get("code")))
    if isinstance(status, int) and not isinstance(status, bool):
        return 400 <= status <= 599
    if isinstance(status, str):
        return status.strip().lower() in ("error", "failed", "failure")
    detail = fields.get("detail")
    if isinstance(detail, dict):
        return _looks_like_legacy_interface_error(json.dumps(detail))
    return isinstance(detail, str) and _looks_like_legacy_interface_error(detail)


def message_context_content(message: ChatMessage) -> str:
    """保留正常/部分正文，但排除单独持久化的运行时错误原文。"""
    raw_parts = _load_list(getattr(message, "parts_json", None))
    parts = _customer_safe_parts(_normalize_parts(raw_parts), raw_parts, role=message.role)
    return _customer_safe_content(
        role=message.role,
        content=message.content,
        raw_parts=raw_parts,
        parts=parts,
    )


def reply_boundary(message: ChatMessage) -> dict[str, Any] | None:
    """从既有 parts 字段读取服务端冻结标记，不新增数据库列。"""
    return next(
        (part for part in _load_list(message.parts_json)
         if isinstance(part, dict) and part.get("type") == "reply_boundary"),
        None,
    )


def _frozen_reply_payload(message: ChatMessage) -> dict[str, Any]:
    payload = message_to_dict(message)
    payload["parts"] = _load_list(message.parts_json)
    payload["dispatch_id"] = message.dispatch_id
    return payload


def finish_reply_metadata(
    db: Session, session: ChatSession, payload: dict[str, Any], *, root_assistant_id: str
) -> None:
    """仅补充同一运行的账单和文件卡，绝不替换冻结正文及其引用。"""
    current = db.get(ChatMessage, str(payload.get("id") or ""))
    if current is None:
        return
    boundary = reply_boundary(current)
    if (
        boundary is None and current.session_id == session.id
        and current.owner_user_id == session.owner_user_id and current.role == "assistant"
        and current.content == str(payload.get("content") or "")
        and any(
            part.get("type") == "reply_boundary" and part.get("root_assistant_id") == root_assistant_id
            for part in _normalize_parts(payload.get("parts") or [])
        )
    ):
        # 浏览器的待保存追加可能抢先落库；只提升同正文记录，不信任客户端冻结声明。
        _apply_assistant_message_payload(db, session, current, payload)
        boundary = reply_boundary(current)
    if (
        current.session_id != session.id or current.owner_user_id != session.owner_user_id
        or current.role != "assistant" or boundary is None
        or boundary.get("root_assistant_id") != root_assistant_id
    ):
        raise ValueError("reply metadata does not match the owned run")
    changed = False
    if payload.get("references"):
        references = _dump(_merge_json_list(
            _load_list(current.references_json), payload["references"],
        ))
        if references != current.references_json:
            current.references_json = references
            changed = True
    if payload.get("writing_project_id") and not current.writing_project_id:
        current.writing_project_id = payload["writing_project_id"]
        changed = True
    if payload.get("usage") is not None:
        usage = _priced_usage(db, payload["usage"])
        serialized = _dump(usage) if usage else None
        if current.usage != serialized:
            current.usage = serialized
            changed = True
    parts = _load_list(current.parts_json)
    hrefs = {part.get("href") for part in parts if isinstance(part, dict)}
    for part in _normalize_parts(payload.get("parts") or []):
        if part["type"] == "file" and part.get("href") not in hrefs:
            parts.append(part)
            hrefs.add(part["href"])
            changed = True
    if changed:
        current.parts_json = _dump(parts)
        _advance_messages_revision(session)
        db.flush()


def order_runtime_reply_messages(
    db: Session, session: ChatSession, *, root_user_id: str,
    root_assistant_id: str, message_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """只重排已验证的本次运行消息；普通追加仍遵守严格尾部时间约束。"""
    anchor = db.get(ChatMessage, root_user_id)
    if (
        anchor is None or anchor.session_id != session.id
        or anchor.owner_user_id != session.owner_user_id or anchor.role != "user"
    ):
        raise ValueError("runtime reply order requires an owned user anchor")
    stamp = as_utc(anchor.created_at)
    records: dict[str, dict[str, Any]] = {}
    changed = False
    for message_id in dict.fromkeys(message_ids):
        current = db.get(ChatMessage, message_id)
        if current is None:
            stamp += timedelta(milliseconds=1)
            continue
        if current.session_id != session.id or current.owner_user_id != session.owner_user_id:
            raise ValueError("runtime reply order contains an unowned message")
        if current.role == "assistant":
            boundary = reply_boundary(current)
            if boundary is None or boundary.get("root_assistant_id") != root_assistant_id:
                raise ValueError("runtime reply order contains a different run")
        elif current.role != "user":
            raise ValueError("runtime reply order contains an invalid role")
        if current.id != root_user_id:
            stamp += timedelta(milliseconds=1)
            if as_utc(current.created_at) != stamp:
                current.created_at = stamp
                changed = True
        records[current.id] = message_to_dict(current)
    if changed:
        _advance_messages_revision(session)
        db.flush()
    return records


def message_to_dict(
    message: ChatMessage,
    variant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_parts = _load_list(getattr(message, "parts_json", None))
    internal_parts = _normalize_parts(raw_parts)
    parts = _customer_safe_parts(internal_parts, raw_parts, role=message.role)
    usage = normalize_usage(getattr(message, "usage", None))
    thinking = (
        _customer_safe_content(
            role=message.role,
            content=message.thinking,
            raw_parts=raw_parts,
            parts=parts,
        )
        if message.thinking
        else None
    )
    if message.role == "assistant" and not raw_parts:
        if _looks_like_legacy_interface_error(thinking) or (
            usage is not None and usage.get("status") == "failed"
        ):
            # 无结构化记录的旧失败回合不公开未经核验的思考；原始值仍保留在内部。
            thinking = None
    return {
        "metadata": (
            {
                "assistant_message_frozen": True,
                "pi_incomplete": bool(boundary.get("incomplete")),
                "server_persisted": True,
                "source_user_message_id": boundary["source_user_message_id"],
            }
            if (boundary := reply_boundary(message)) else {}
        ),
        "variant": variant,
        "id": message.id,
        "template_revision_id": next(
            (part["revision_id"] for part in internal_parts
             if part.get("type") == "presentation_template"), None
        ),
        "role": message.role,
        "content": _customer_safe_content(
            role=message.role,
            content=message.content,
            raw_parts=raw_parts,
            parts=parts,
        ),
        "thinking": thinking,
        "parts": parts,
        "space_ids": _load_list(message.space_ids_json),
        "document_ids": _load_list(getattr(message, "document_ids_json", None) or "[]"),
        "references": _load_list(message.references_json),
        "session_references": _load_list(
            getattr(message, "session_references_json", None) or "[]"
        ),
        "writing_references": _load_list(
            getattr(message, "writing_references_json", None) or "[]"
        ),
        "images": _normalize_images(_load_list(getattr(message, "images_json", None))),
        "retrieval_summary": message.retrieval_summary,
        "retrieval_trace": _load_trace(getattr(message, "retrieval_trace_json", None)),
        "writing_project_id": message.writing_project_id,
        "writing_context": _load_writing_context(message.writing_context_json),
        "usage": usage,
        "created_at": iso(message.created_at),
    }


def session_summary_dict(session: ChatSession) -> dict[str, Any]:
    summary_message_count = getattr(session, "summary_message_count", None)
    return {
        "id": session.id,
        "title": session.title,
        "messages_revision": _coerce_int(getattr(session, "messages_revision", 0)),
        "agent_session_id": session.agent_session_id,
        "preferred_model": session.preferred_model,
        "category_id": session.category_id,
        "category_name": session.category.name if session.category else None,
        "pinned": bool(session.pinned),
        "pinned_at": iso(session.pinned_at) if session.pinned_at else None,
        "archived": bool(session.archived),
        "archived_at": iso(session.archived_at) if session.archived_at else None,
        "writing_project_id": session.writing_project_id,
        "message_count": (
            _coerce_int(summary_message_count)
            if summary_message_count is not None
            else len(active_messages(session.messages or []))
        ),
        "created_at": iso(session.created_at),
        "updated_at": iso(session.updated_at),
    }


_ROLE_ORDER = {"user": 0, "assistant": 1, "system": 2}


def ordered_messages(messages: list[ChatMessage] | None) -> list[ChatMessage]:
    """Sort a session's messages into their stable conversational order."""
    # created_at 相同时按角色稳定排序，避免助手回复排到用户问句前面
    return sorted(
        messages or [],
        key=lambda item: (
            as_utc(item.created_at),
            _ROLE_ORDER.get(item.role, 9),
            item.id,
        ),
    )


def active_messages(messages: list[ChatMessage] | None) -> list[ChatMessage]:
    """Return only the selected variant of each message, in conversation order.

    Regenerated answers live as sibling rows in one variant group. Inactive
    siblings must never reach the conversation view, the model context, a fork
    prefix, or a publication snapshot.
    """
    return [
        item
        for item in ordered_messages(messages)
        if bool(getattr(item, "variant_active", True))
    ]


def variant_index_by_message_id(
    messages: list[ChatMessage] | None,
) -> dict[str, dict[str, Any]]:
    """Group messages into variant sets keyed by every member's message id.

    Single-variant messages (no group) get no entry so the conversation payload
    stays byte-identical for pre-existing data.
    """
    groups: dict[str, list[Any]] = {}
    for item in ordered_messages(messages):
        group_id = getattr(item, "variant_group_id", None)
        if not group_id:
            continue
        groups.setdefault(str(group_id), []).append(item)
    index: dict[str, dict[str, Any]] = {}
    for group_id, members in groups.items():
        members.sort(
            key=lambda item: (
                _coerce_int(getattr(item, "variant_index", 0)),
                as_utc(item.created_at),
                item.id,
            )
        )
        ordered_ids = [str(item.id) for item in members]
        active_id = next(
            (
                str(item.id)
                for item in members
                if bool(getattr(item, "variant_active", True))
            ),
            ordered_ids[-1],
        )
        for position, item in enumerate(members):
            index[str(item.id)] = {
                "group_id": group_id,
                "index": position + 1,
                "total": len(members),
                "active": str(item.id) == active_id,
                "ids": ordered_ids,
            }
    return index


def session_detail_dict(
    session: ChatSession,
    *,
    message_page: ChatMessagePage | None = None,
) -> dict[str, Any]:
    detail = session_summary_dict(session)
    if message_page is None:
        variant_messages = session.messages or []
        messages = active_messages(session.messages or [])
        has_more_messages = False
        next_before_message_id = None
    else:
        variant_messages = message_page.variant_messages
        messages = message_page.messages
        has_more_messages = message_page.has_more_messages
        next_before_message_id = message_page.next_before_message_id
    variants = variant_index_by_message_id(variant_messages)
    detail["messages"] = [
        message_to_dict(item, variants.get(item.id)) for item in messages
    ]
    detail["has_more_messages"] = has_more_messages
    detail["next_before_message_id"] = next_before_message_id
    return detail


def create_publication(
    db: Session,
    session: ChatSession,
    *,
    shared_by_user_id: str,
    validated_space_ids: list[str],
    title: str | None = None,
    summary: str | None = None,
    share_scope: str | None,
    allow_cite: bool,
) -> ChatSessionPublication:
    messages = active_messages(session.messages or [])
    snapshot: list[dict[str, Any]] = []
    for message in messages:
        images = _load_list(getattr(message, "images_json", None) or "[]")
        raw_parts = _load_list(getattr(message, "parts_json", None))
        internal_parts = _normalize_parts(raw_parts)
        safe_content = _customer_safe_content(
            role=message.role,
            content=message.content,
            raw_parts=raw_parts,
            parts=_customer_safe_parts(internal_parts, raw_parts, role=message.role),
        )
        if not safe_content.strip() and not images:
            continue
        snapshot.append(
            {
                "id": message.id,
                "role": message.role,
                "content": safe_content,
                "references": _load_list(message.references_json),
                "session_references": _load_list(
                    getattr(message, "session_references_json", None) or "[]"
                ),
                "writing_references": _load_list(
                    getattr(message, "writing_references_json", None) or "[]"
                ),
                "images": images,
                "space_ids": _load_list(
                    getattr(message, "space_ids_json", None) or "[]"
                ),
                "created_at": iso(message.created_at),
            }
        )
    snapshot_json = _dump(snapshot)
    # Space ACL is checked by the API immediately before this repository call.
    # Do not derive publication scope from the persisted message JSON.
    space_ids = list(dict.fromkeys(validated_space_ids))
    resolved_share_scope = share_scope or ("space" if space_ids else "all_internal")
    if resolved_share_scope == "space" and not space_ids:
        raise ValueError("space-scoped publication requires a knowledge space")
    previous_versions = list(
        db.scalars(
            select(ChatSessionPublication.version).where(
                ChatSessionPublication.source_session_id == session.id
            )
        )
    )
    version = max(previous_versions, default=0) + 1
    stamp = now()
    publication = ChatSessionPublication(
        id=f"chp_{new_token(10)}",
        source_session_id=session.id,
        source_owner_user_id=session.owner_user_id,
        shared_by_user_id=shared_by_user_id,
        title=(title or session.title).strip() or "共享会话",
        summary=(summary or "").strip(),
        version=version,
        snapshot_json=snapshot_json,
        content_hash=hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
        share_scope=resolved_share_scope,
        space_ids_json=_dump(space_ids),
        allow_cite=allow_cite,
        allow_remix=True,
        status="active",
        published_at=stamp,
        revoked_at=None,
        created_at=stamp,
    )
    db.add(publication)
    db.flush()
    return publication


def list_publications(db: Session) -> list[ChatSessionPublication]:
    return list(
        db.scalars(
            select(ChatSessionPublication)
            .where(ChatSessionPublication.status == "active")
            .order_by(ChatSessionPublication.published_at.desc())
        )
    )


def get_publication(db: Session, publication_id: str) -> ChatSessionPublication | None:
    return db.scalar(
        select(ChatSessionPublication).where(
            ChatSessionPublication.id == publication_id,
            ChatSessionPublication.status == "active",
        )
    )


def get_owned_publication(
    db: Session,
    publication_id: str,
    owner_user_id: str,
) -> ChatSessionPublication | None:
    return db.scalar(
        select(ChatSessionPublication).where(
            ChatSessionPublication.id == publication_id,
            ChatSessionPublication.source_owner_user_id == owner_user_id,
            ChatSessionPublication.status == "active",
        )
    )


def revoke_publication(db: Session, publication: ChatSessionPublication) -> None:
    publication.status = "revoked"
    publication.revoked_at = now()
    db.flush()


def publication_snapshot(publication: ChatSessionPublication) -> list[dict[str, Any]]:
    parsed = _load_list(publication.snapshot_json)
    snapshot: list[dict[str, Any]] = []
    for value in parsed:
        if not isinstance(value, dict):
            continue
        item = dict(value)
        raw_parts = value.get("parts")
        raw_parts = raw_parts if isinstance(raw_parts, list) else []
        parts = _customer_safe_parts(
            _normalize_parts(raw_parts), raw_parts, role=value.get("role")
        )
        content = _customer_safe_content(
            role=value.get("role"),
            content=value.get("content"),
            raw_parts=raw_parts,
            parts=parts,
        )
        if (
            str(value.get("role") or "") == "assistant"
            and content == str(value.get("content") or "")
            and not raw_parts
            and _looks_like_legacy_interface_error(content)
        ):
            content = trusted_chat_error_message(
                "upstream_error",
                error_kind="runtime",
            )
        item["content"] = content
        if "parts" in item:
            item["parts"] = parts
        snapshot.append(item)
    return snapshot


def category_dict(category: ChatCategory) -> dict[str, Any]:
    return {
        "id": category.id,
        "name": category.name,
        "sort_order": category.sort_order,
        "created_at": iso(category.created_at),
        "updated_at": iso(category.updated_at),
    }
