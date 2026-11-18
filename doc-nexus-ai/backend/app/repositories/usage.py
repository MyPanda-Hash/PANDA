from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from io import BytesIO
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import as_utc, new_token, now
from app.models.artifacts import Artifact
from app.models.auth import User
from app.models.chat import ChatMessage
from app.models.chat_control import ChatControlOperation
from app.models.chat_search import ChatHistoryUsageEvent
from app.models.documents import Document, DocumentJob, DocumentVersion
from app.models.qa import QaTurn
from app.models.schedules import ScheduleRun
from app.models.user_memory import UserMemoryOperation
from app.repositories import documents as document_repository
from app.models.writing import WritingChapter, WritingOperation, WritingOutline, WritingProject, WritingVersion
from app.services.evidence_export import EvidenceValidation, validate_project_evidence
from app.services.markdown_docx import (
    FONT_TABLE_XML,
    SETTINGS_XML,
    STYLES_XML,
    render_project_docx_body,
)
from app.services.presentation_deck import render_project_pptx


ZERO_USAGE = {
    "provider": "deterministic_metadata",
    "input_tokens": 0,
    "input_uncached_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "cost": "0",
    "latency_ms": 0,
}

# Non-real model identifiers used internally as provider/fixture markers.
# Excluded from model-dimension aggregation so they don't pollute the UI.
_SKIP_MODEL_NAMES = frozenset({
    "deterministic_metadata",
    "deterministic_writing_fixture",
    "none",
    "unknown",
})



@dataclass(frozen=True)
class OperationReadModel:
    id: str
    owner_user_id: str
    feature: str
    action: str
    status: str
    usage: dict[str, Any]
    resource_path: str
    created_at: datetime
    completed_at: datetime | None


@dataclass
class UserUsageSummary:
    user_id: str
    username: str
    display_name: str
    status: str
    operation_count: int = 0
    input_tokens: int = 0
    input_uncached_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: str = "0"
    by_feature: dict[str, int] = field(default_factory=dict)
    by_model: dict[str, int] = field(default_factory=dict)
    by_model_detail: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelUsageSummary:
    model: str
    input_tokens: int = 0
    input_uncached_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: str = "0"
    operation_count: int = 0


@dataclass(frozen=True)
class AdminUsageSummaryResult:
    items: list[UserUsageSummary]
    total_users: int
    users_with_usage: int
    total_operations: int
    total_input_tokens: int
    total_input_uncached_tokens: int
    total_cache_read_tokens: int
    total_cache_write_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost: str
    by_model: list[ModelUsageSummary] = field(default_factory=list)


def _load_usage(value: str) -> dict[str, Any]:
    from app.services.usage_normalize import normalize_usage_buckets

    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    merged = {**ZERO_USAGE, **parsed}
    # Re-derive exclusive buckets from provider fields; ignore placeholder zeros
    # introduced by ZERO_USAGE defaults.
    source = dict(parsed)
    source.setdefault("input_tokens", merged.get("input_tokens", 0))
    source.setdefault("output_tokens", merged.get("output_tokens", 0))
    source.setdefault("total_tokens", merged.get("total_tokens", 0))
    if "prompt_tokens" in parsed:
        source["prompt_tokens"] = parsed["prompt_tokens"]
    if "completion_tokens" in parsed:
        source["completion_tokens"] = parsed["completion_tokens"]
    buckets = normalize_usage_buckets(source)
    return {**merged, **buckets}


def _public_status(status: str) -> str:
    return "cancelled" if status == "stopped" else "completed" if status == "ready" else status


def _usage_date_bounds(
    date_from: datetime | None,
    date_to: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """Normalize usage date filters for cross-database comparison.

    SQLite round-trips naive UTC datetimes; PostgreSQL returns tz-aware values;
    FastAPI date query params arrive naive at midnight. All are normalized to UTC.
    Date-only ``date_to`` values expand to the next UTC day so the end date is inclusive.
    """
    start = as_utc(date_from) if date_from else None
    end = None
    if date_to:
        end = as_utc(date_to)
        if end.hour == end.minute == end.second == end.microsecond == 0:
            end = end + timedelta(days=1)
    return start, end


def _matches(item: OperationReadModel, *, feature: str | None, model: str | None, status: str | None, date_from: datetime | None, date_to: datetime | None) -> bool:
    if feature and item.feature != feature:
        return False
    if model:
        names = {
            str(item.usage.get("provider", "")),
            str(item.usage.get("model", "")),
        }
        calls = item.usage.get("calls")
        if isinstance(calls, list):
            for call in calls:
                if isinstance(call, dict):
                    names.update(
                        {
                            str(call.get("provider", "")),
                            str(call.get("model", "")),
                        }
                    )
        if model not in names:
            return False
    if status and item.status != status:
        return False
    created_at = as_utc(item.created_at)
    start, end = _usage_date_bounds(date_from, date_to)
    if start and created_at < start:
        return False
    return not bool(end and created_at >= end)


def _parse_cost(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _writing_operations(db: Session, owner_user_id: str | None = None) -> list[OperationReadModel]:
    query = select(WritingOperation, WritingProject).join(WritingProject, WritingProject.id == WritingOperation.project_id)
    if owner_user_id is not None:
        query = query.where(WritingProject.owner_user_id == owner_user_id)
    rows = db.execute(query).all()
    return [OperationReadModel(id=operation.id, owner_user_id=project.owner_user_id, feature="writing", action=operation.kind, status=_public_status(operation.status), usage=_load_usage(operation.usage), resource_path=f"/writing/{project.id}", created_at=operation.created_at, completed_at=operation.completed_at) for operation, project in rows]


def _qa_operations(db: Session, owner_user_id: str | None = None) -> list[OperationReadModel]:
    query = select(QaTurn)
    if owner_user_id is not None:
        query = query.where(QaTurn.owner_user_id == owner_user_id)
    return [OperationReadModel(id=turn.id, owner_user_id=turn.owner_user_id, feature="qa", action="ask", status=turn.status, usage=_load_usage(turn.usage), resource_path=f"/qa/{turn.session_id}", created_at=turn.created_at, completed_at=turn.completed_at) for turn in db.scalars(query)]


def _document_operations(db: Session, owner_user_id: str | None = None) -> list[OperationReadModel]:
    query = (
        select(DocumentJob, Document, DocumentVersion)
        .join(Document, Document.id == DocumentJob.document_id)
        .join(DocumentVersion, DocumentVersion.id == DocumentJob.version_id)
    )
    if owner_user_id is not None:
        query = query.where(DocumentJob.created_by_user_id == owner_user_id)
    rows = db.execute(query).all()
    operations: list[OperationReadModel] = []
    for job, document, version in rows:
        usage = dict(ZERO_USAGE)
        raw_vision_usage = getattr(version, "vision_usage_json", "")
        if raw_vision_usage and str(raw_vision_usage).strip() not in {"", "{}", "null"}:
            parsed_usage = _load_usage(raw_vision_usage)
            if isinstance(parsed_usage.get("calls"), list) and parsed_usage["calls"]:
                usage = {
                    **parsed_usage,
                    "provider": "document_processing",
                    "model": None,
                    "status": _public_status(job.status),
                }
        operations.append(
            OperationReadModel(
                id=job.id,
                owner_user_id=job.created_by_user_id,
                feature="documents",
                action=job.kind,
                status=_public_status(job.status),
                usage=usage,
                resource_path=document_repository.document_resource_path(document),
                created_at=job.created_at,
                completed_at=job.updated_at if job.status == "ready" else None,
            )
        )
    return operations


def _chat_operations(db: Session, owner_user_id: str | None = None) -> list[OperationReadModel]:
    query = select(ChatMessage).where(ChatMessage.role == "assistant")
    if owner_user_id is not None:
        query = query.where(ChatMessage.owner_user_id == owner_user_id)
    messages = db.scalars(query).all()
    operations: list[OperationReadModel] = []
    for message in messages:
        raw = getattr(message, "usage", None)
        if not raw or str(raw).strip() in ("", "{}", "null"):
            continue
        usage = _load_usage(raw)
        if int(usage.get("total_tokens") or 0) == 0 and int(usage.get("latency_ms") or 0) == 0:
            continue
        if not usage.get("provider") or usage["provider"] == ZERO_USAGE["provider"]:
            usage = {**usage, "provider": "agent_chat"}
        operations.append(
            OperationReadModel(
                id=message.id,
                owner_user_id=message.owner_user_id,
                feature="chat",
                action="message",
                status=str(usage.get("status") or "completed")
                if str(usage.get("status") or "completed") in {"completed", "failed", "cancelled"}
                else "failed",
                usage=usage,
                resource_path=f"/chat/{message.session_id}",
                created_at=message.created_at,
                completed_at=message.created_at,
            )
        )
    return operations




def _chat_control_operations(
    db: Session, owner_user_id: str | None = None
) -> list[OperationReadModel]:
    query = select(ChatControlOperation)
    if owner_user_id is not None:
        query = query.where(ChatControlOperation.owner_user_id == owner_user_id)
    operations: list[OperationReadModel] = []
    for operation in db.scalars(query):
        usage = _load_usage(operation.usage)
        usage = {
            **usage,
            "status": operation.status,
            "error_code": operation.error_code,
        }
        operations.append(
            OperationReadModel(
                id=operation.id,
                owner_user_id=operation.owner_user_id,
                feature="chat",
                action=operation.action,
                status=operation.status,
                usage=usage,
                resource_path=f"/chat/{operation.session_id}",
                created_at=operation.created_at,
                completed_at=operation.completed_at,
            )
        )
    return operations
def _usage_calls(usage: dict[str, Any]) -> list[dict[str, Any]]:
    """Return actual provider calls, or the legacy aggregate as one call."""
    raw_calls = usage.get("calls")
    if isinstance(raw_calls, list):
        calls = [item for item in raw_calls if isinstance(item, dict)]
        if calls:
            return calls
    return [usage]


def _chat_history_operations(
    db: Session, owner_user_id: str | None = None
) -> list[OperationReadModel]:
    query = select(ChatHistoryUsageEvent)
    if owner_user_id is not None:
        query = query.where(ChatHistoryUsageEvent.owner_user_id == owner_user_id)
    operations: list[OperationReadModel] = []
    for event in db.scalars(query):
        provider = (event.provider or "nexus").strip() or "nexus"
        usage = {
            "provider": provider,
            "model": (event.model or "").strip() or None,
            "price_version_id": (event.price_version_id or "").strip() or None,
            "currency": (event.currency or "").strip() or None,
            "input_tokens": int(event.input_tokens or 0),
            "input_uncached_tokens": int(event.input_uncached_tokens or 0),
            "cache_read_tokens": int(event.cache_read_tokens or 0),
            "cache_write_tokens": int(event.cache_write_tokens or 0),
            "output_tokens": int(event.output_tokens or 0),
            "total_tokens": int(event.total_tokens or 0),
            "cost": str(event.cost or "0"),
            "latency_ms": int(event.latency_ms or 0),
        }
        operations.append(
            OperationReadModel(
                id=event.id,
                owner_user_id=event.owner_user_id,
                feature="chat_history",
                action=event.operation_kind or event.query_type or "search",
                status=event.status or "succeeded",
                usage=usage,
                resource_path=(
                    f"/chat/{event.message_id}"
                    if event.message_id
                    else "/chat/history"
                ),
                created_at=event.created_at,
                completed_at=event.created_at,
            )
        )
    return operations


def _memory_operations(
    db: Session, owner_user_id: str | None = None
) -> list[OperationReadModel]:
    query = select(UserMemoryOperation)
    if owner_user_id is not None:
        query = query.where(UserMemoryOperation.owner_user_id == owner_user_id)
    return [
        OperationReadModel(
            id=operation.id,
            owner_user_id=operation.owner_user_id,
            feature="memory",
            action=operation.kind,
            status=operation.status,
            usage=_load_usage(operation.usage_json),
            resource_path="/chat",
            created_at=operation.created_at,
            completed_at=operation.completed_at,
        )
        for operation in db.scalars(query)
    ]


def _schedule_operations(
    db: Session, owner_user_id: str | None = None
) -> list[OperationReadModel]:
    query = select(ScheduleRun).where(ScheduleRun.usage_json.is_not(None))
    if owner_user_id is not None:
        query = query.where(ScheduleRun.owner_user_id == owner_user_id)
    return [
        OperationReadModel(
            id=run.id,
            owner_user_id=run.owner_user_id,
            feature="schedule",
            action=run.trigger_kind,
            status=run.status,
            usage=_load_usage(run.usage_json or "{}"),
            resource_path=f"/schedules/{run.schedule_id}/runs/{run.id}",
            created_at=run.created_at,
            completed_at=run.completed_at,
        )
        for run in db.scalars(query)
    ]


def _collect_operations(
    db: Session,
    *,
    owner_user_id: str | None = None,
    feature: str | None = None,
    model: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[OperationReadModel]:
    from app.services.pricing import priced_usage_snapshot

    operations = (
        _writing_operations(db, owner_user_id)
        + _qa_operations(db, owner_user_id)
        + _document_operations(db, owner_user_id)
        + _chat_operations(db, owner_user_id)
        + _chat_control_operations(db, owner_user_id)
        + _chat_history_operations(db, owner_user_id)
        + _memory_operations(db, owner_user_id)
        + _schedule_operations(db, owner_user_id)
    )
    filtered = [
        item
        for item in operations
        if _matches(item, feature=feature, model=model, status=status, date_from=date_from, date_to=date_to)
    ]
    # Historical rows often store cost "0"/"unknown"; estimate from the active price version on read.
    priced: list[OperationReadModel] = []
    for item in filtered:
        usage = priced_usage_snapshot(db, item.usage)
        if usage is item.usage:
            priced.append(item)
            continue
        priced.append(
            OperationReadModel(
                id=item.id,
                owner_user_id=item.owner_user_id,
                feature=item.feature,
                action=item.action,
                status=item.status,
                usage=usage,
                resource_path=item.resource_path,
                created_at=item.created_at,
                completed_at=item.completed_at,
            )
        )
    return priced


def list_operations(db: Session, *, owner_user_id: str, feature: str | None = None, model: str | None = None, status: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None, offset: int = 0, limit: int = 50) -> tuple[list[OperationReadModel], int | None]:
    filtered = _collect_operations(
        db,
        owner_user_id=owner_user_id,
        feature=feature,
        model=model,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    filtered.sort(key=lambda item: (as_utc(item.created_at), item.id), reverse=True)
    page = filtered[offset : offset + limit]
    return page, offset + limit if offset + limit < len(filtered) else None


def summarize_usage_by_user(
    db: Session,
    *,
    feature: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_zero: bool = True,
) -> AdminUsageSummaryResult:
    """Aggregate token usage across all users. Returns metadata only (no question/body content)."""
    users = list(db.scalars(select(User).order_by(User.created_at.asc(), User.id.asc())))
    operations = _collect_operations(db, feature=feature, date_from=date_from, date_to=date_to)

    buckets: dict[str, dict[str, Any]] = {
        user.id: {
            "operation_count": 0,
            "input_tokens": 0,
            "input_uncached_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost": Decimal("0"),
            "by_feature": defaultdict(int),
            "by_model": defaultdict(int),
            "by_model_detail": defaultdict(lambda: {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost": Decimal("0"),
                "operation_count": 0,
            }),
        }
        for user in users
    }
    model_buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "operation_count": 0,
        "input_tokens": 0,
        "input_uncached_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost": Decimal("0"),
    })
    for item in operations:
        bucket = buckets.get(item.owner_user_id)
        if bucket is None:
            continue
        usage = item.usage
        bucket["operation_count"] += 1
        bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
        bucket["input_uncached_tokens"] += int(
            usage.get("input_uncached_tokens", usage.get("input_tokens") or 0) or 0
        )
        bucket["cache_read_tokens"] += int(usage.get("cache_read_tokens") or 0)
        bucket["cache_write_tokens"] += int(usage.get("cache_write_tokens") or 0)
        bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
        tokens = int(usage.get("total_tokens") or 0)
        bucket["total_tokens"] += tokens
        bucket["cost"] += _parse_cost(usage.get("cost"))
        bucket["by_feature"][item.feature] += tokens
        for call in _usage_calls(usage):
            call_tokens = int(call.get("total_tokens") or 0)
            # Memory attempts may have unknown outcomes but must not fabricate
            # a model dimension from a provider name or reservation estimate.
            if item.feature == "memory" and (
                not call.get("model") or call.get("usage_status") != "reported"
            ):
                continue
            model_name = str(call.get("model") or call.get("provider") or "unknown").strip()
            model_name_lower = model_name.lower()
            if call_tokens <= 0 or not model_name or model_name_lower in _SKIP_MODEL_NAMES:
                continue
            bucket["by_model"][model_name] += call_tokens
            detail = bucket["by_model_detail"][model_name]
            detail["input_tokens"] += int(call.get("input_tokens") or 0)
            detail["output_tokens"] += int(call.get("output_tokens") or 0)
            detail["total_tokens"] += call_tokens
            detail["cost"] += _parse_cost(call.get("cost"))
            detail["operation_count"] += 1
            mb = model_buckets[model_name]
            mb["operation_count"] += 1
            mb["input_tokens"] += int(call.get("input_tokens") or 0)
            mb["input_uncached_tokens"] += int(
                call.get("input_uncached_tokens", call.get("input_tokens") or 0) or 0
            )
            mb["cache_read_tokens"] += int(call.get("cache_read_tokens") or 0)
            mb["cache_write_tokens"] += int(call.get("cache_write_tokens") or 0)
            mb["output_tokens"] += int(call.get("output_tokens") or 0)
            mb["total_tokens"] += call_tokens
            mb["cost"] += _parse_cost(call.get("cost"))

    items: list[UserUsageSummary] = []
    for user in users:
        bucket = buckets[user.id]
        if not include_zero and bucket["operation_count"] == 0 and bucket["total_tokens"] == 0:
            continue
        items.append(
            UserUsageSummary(
                user_id=user.id,
                username=user.username,
                display_name=user.display_name,
                status=user.status,
                operation_count=bucket["operation_count"],
                input_tokens=bucket["input_tokens"],
                input_uncached_tokens=bucket["input_uncached_tokens"],
                cache_read_tokens=bucket["cache_read_tokens"],
                cache_write_tokens=bucket["cache_write_tokens"],
                output_tokens=bucket["output_tokens"],
                total_tokens=bucket["total_tokens"],
                cost=format(bucket["cost"].normalize(), "f") if bucket["cost"] else "0",
                by_feature=dict(sorted(bucket["by_feature"].items())),
                by_model=dict(sorted(bucket["by_model"].items())),
                by_model_detail={
                    name: {
                        "input_tokens": d["input_tokens"],
                        "output_tokens": d["output_tokens"],
                        "total_tokens": d["total_tokens"],
                        "cost": format(d["cost"].normalize(), "f") if d["cost"] else "0",
                        "operation_count": d["operation_count"],
                    }
                    for name, d in sorted(bucket["by_model_detail"].items(), key=lambda x: x[1]["total_tokens"], reverse=True)
                    if d["total_tokens"] > 0
                },
            )
        )
    items.sort(key=lambda item: (item.total_tokens, item.operation_count, item.username), reverse=True)

    total_cost = sum((_parse_cost(item.cost) for item in items), Decimal("0"))
    by_model_items = sorted(
        [
            ModelUsageSummary(
                model=name,
                input_tokens=b["input_tokens"],
                input_uncached_tokens=b["input_uncached_tokens"],
                cache_read_tokens=b["cache_read_tokens"],
                cache_write_tokens=b["cache_write_tokens"],
                output_tokens=b["output_tokens"],
                total_tokens=b["total_tokens"],
                cost=format(b["cost"].normalize(), "f") if b["cost"] else "0",
                operation_count=b["operation_count"],
            )
            for name, b in model_buckets.items()
            if b["total_tokens"] > 0
        ],
        key=lambda m: m.total_tokens,
        reverse=True,
    )
    return AdminUsageSummaryResult(
        items=items,
        total_users=len(users),
        users_with_usage=sum(1 for item in items if item.total_tokens > 0 or item.operation_count > 0),
        total_operations=sum(item.operation_count for item in items),
        total_input_tokens=sum(item.input_tokens for item in items),
        total_input_uncached_tokens=sum(item.input_uncached_tokens for item in items),
        total_cache_read_tokens=sum(item.cache_read_tokens for item in items),
        total_cache_write_tokens=sum(item.cache_write_tokens for item in items),
        total_output_tokens=sum(item.output_tokens for item in items),
        total_tokens=sum(item.total_tokens for item in items),
        total_cost=format(total_cost.normalize(), "f") if total_cost else "0",
        by_model=by_model_items,
    )


def owned_operation(db: Session, operation_id: str, owner_user_id: str) -> OperationReadModel | None:
    items, _ = list_operations(db, owner_user_id=owner_user_id, limit=10_000)
    return next((item for item in items if item.id == operation_id), None)


def operation_usage_event(operation: OperationReadModel) -> dict[str, Any]:
    usage = operation.usage
    payload = {
        "provider": str(usage["provider"]),
        "model": str(usage.get("model") or usage["provider"]),
        "purpose": operation.action,
        "status": operation.status,
        "input_tokens": int(usage["input_tokens"]),
        "input_uncached_tokens": int(usage.get("input_uncached_tokens", usage["input_tokens"]) or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
        "output_tokens": int(usage["output_tokens"]),
        "total_tokens": int(usage["total_tokens"]),
        "cost": str(usage["cost"]),
        "latency_ms": int(usage["latency_ms"]),
    }
    if isinstance(usage.get("calls"), list) and usage["calls"]:
        payload["calls"] = usage["calls"]
    return payload



DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX_RENDERER_VERSION = "markdown-docx/3"
PPTX_RENDERER_VERSION = "presentation-deck/1"


def _zip_entry(archive: ZipFile, path: str, content: str) -> None:
    entry = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = ZIP_DEFLATED
    archive.writestr(entry, content.encode("utf-8"))


def _render_docx_snapshot(
    project: WritingProject,
    snapshots: list[tuple[WritingChapter, WritingVersion]],
) -> bytes:
    body = render_project_docx_body(
        project.title,
        [(chapter.title, version.content) for chapter, version in snapshots],
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{body}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr></w:body>"
        "</w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/fontTable.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"/>'
        '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    document_relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/fontTable" Target="fontTable.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
        "</Relationships>"
    )
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        _zip_entry(archive, "[Content_Types].xml", content_types)
        _zip_entry(archive, "_rels/.rels", relationships)
        _zip_entry(archive, "word/document.xml", document)
        _zip_entry(archive, "word/_rels/document.xml.rels", document_relationships)
        _zip_entry(archive, "word/styles.xml", STYLES_XML)
        _zip_entry(archive, "word/fontTable.xml", FONT_TABLE_XML)
        _zip_entry(archive, "word/settings.xml", SETTINGS_XML)
    return output.getvalue()


def _render_pptx_snapshot(
    project: WritingProject,
    snapshots: list[tuple[WritingChapter, WritingVersion]],
) -> tuple[bytes, dict[str, Any]]:
    """Render an ordinary PowerPoint with the built-in presentation theme."""
    from app.services.writing_brief import decode_brief

    brief = decode_brief(getattr(project, "brief_json", None))
    audience = str(brief.get("audience") or "")
    chapters = [(chapter.title, version.content) for chapter, version in snapshots]

    return render_project_pptx(project.title, chapters, audience=audience)


def _export_source_sha256(
    project: WritingProject,
    snapshots: list[tuple[WritingChapter, WritingVersion]],
) -> str:
    source = {
        "project_id": project.id,
        "title": project.title,
        "chapters": [
            {
                "chapter_id": chapter.id,
                "title": chapter.title,
                "version_id": version.id,
                "content": version.content,
            }
            for chapter, version in snapshots
        ],
    }
    return hashlib.sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _snapshot_versions_for_export(db: Session, project: WritingProject) -> list[tuple[WritingChapter, WritingVersion]]:
    chapters = list(db.scalars(select(WritingChapter).join(WritingOutline, WritingChapter.outline_id == WritingOutline.id).where(WritingOutline.project_id == project.id).order_by(WritingChapter.position)))
    snapshots: list[tuple[WritingChapter, WritingVersion]] = []
    created_at = now()
    for chapter in chapters:
        latest = db.scalar(select(WritingVersion).where(WritingVersion.chapter_id == chapter.id).order_by(WritingVersion.created_at.desc(), WritingVersion.id.desc()).limit(1))
        if latest is None or latest.content != chapter.content:
            latest = WritingVersion(id=f"wve_{new_token(10)}", chapter_id=chapter.id, content=chapter.content, kind="export_snapshot", created_at=created_at)
            db.add(latest)
        snapshots.append((chapter, latest))
    db.flush()
    return snapshots


def _render_export_payload(
    db: Session,
    project: WritingProject,
    output_format: str,
    *,
    user: User,
    evidence_validation: EvidenceValidation | None = None,
    draft: bool = False,
) -> dict[str, Any]:
    output_format = (output_format or "docx").strip().lower()
    if output_format not in {"docx", "pptx"}:
        raise ValueError("unsupported writing export format")
    validation = evidence_validation or validate_project_evidence(db, user=user, project=project)
    # 导出不再因证据未通过而硬阻断：未通过核验的文件标记为 draft（未核验草稿），
    # 核验结论随 manifest 与 operation 留痕；发布仍要求通过核验。
    blocked = bool(validation.blockers)
    snapshots = _snapshot_versions_for_export(db, project)
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "format": output_format,
        "renderer_version": (
            PPTX_RENDERER_VERSION
            if output_format == "pptx"
            else DOCX_RENDERER_VERSION
        ),
        "source_sha256": _export_source_sha256(project, snapshots),
        "evidence_sources": validation.sources,
        "evidence_blockers": validation.blockers,
        "document_status": "draft" if (draft or blocked) else "issued",
    }
    if output_format == "pptx":
        content, deck_spec = _render_pptx_snapshot(project, snapshots)
        manifest["deck_spec"] = deck_spec
        manifest["template"] = {"mode": "builtin"}
        manifest["page_count"] = len(deck_spec["slides"])
        manifest["warnings"] = []
        manifest["coverage"] = {}
        suffix = "pptx"
        content_type = PPTX_CONTENT_TYPE
        artifact_type = "writing_presentation"
    else:
        content = _render_docx_snapshot(project, snapshots)
        suffix = "docx"
        content_type = DOCX_CONTENT_TYPE
        artifact_type = "writing_export"
    content_sha256 = hashlib.sha256(content).hexdigest()
    manifest["content_sha256"] = content_sha256
    return {
        "content": content,
        "content_sha256": content_sha256,
        "content_type": content_type,
        "artifact_type": artifact_type,
        "suffix": suffix,
        "source_version_ids": [version.id for _, version in snapshots],
        "manifest": manifest,
    }




def create_export_artifact(
    db: Session,
    *,
    user: User,
    project: WritingProject,
    operation: WritingOperation,
    format: str = "docx",
    evidence_validation: EvidenceValidation | None = None,
    draft: bool = False,
) -> Artifact:
    output_format = (format or "docx").strip().lower()
    rendered = _render_export_payload(
        db, project, output_format, user=user,
        evidence_validation=evidence_validation, draft=draft,
    )
    created_at = now()
    artifact = Artifact(
        id=f"art_{new_token(10)}",
        owner_user_id=project.owner_user_id,
        project_id=project.id,
        operation_id=operation.id,
        name=f"{project.title}.{rendered['suffix']}",
        artifact_type=rendered["artifact_type"],
        status="ready",
        storage_key=(
            f"writing-exports/{project.owner_user_id}/{operation.id}."
            f"{rendered['suffix']}"
        ),
        content=rendered["content"],
        checksum_sha256=rendered["content_sha256"],
        content_type=rendered["content_type"],
        content_size=len(rendered["content"]),
        source_version_ids=json.dumps(rendered["source_version_ids"]),
        manifest_json=json.dumps(rendered["manifest"], ensure_ascii=False),
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(artifact)
    db.flush()
    return artifact


def regenerate_export_artifact(
    db: Session,
    *,
    user: User,
    artifact: Artifact,
    project: WritingProject,
    operation: WritingOperation,
) -> Artifact:
    output_format = "pptx" if artifact.content_type == PPTX_CONTENT_TYPE else "docx"
    rendered = _render_export_payload(
        db, project, output_format, user=user,
    )
    artifact.operation_id = operation.id
    artifact.name = f"{project.title}.{rendered['suffix']}"
    artifact.artifact_type = rendered["artifact_type"]
    artifact.status = "ready"
    artifact.storage_key = (
        f"writing-exports/{project.owner_user_id}/{operation.id}."
        f"{rendered['suffix']}"
    )
    artifact.content = rendered["content"]
    artifact.checksum_sha256 = rendered["content_sha256"]
    artifact.content_type = rendered["content_type"]
    artifact.content_size = len(rendered["content"])
    artifact.source_version_ids = json.dumps(rendered["source_version_ids"])
    artifact.manifest_json = json.dumps(rendered["manifest"], ensure_ascii=False)
    # Regeneration always retires the publication, even for identical bytes.
    artifact.share_id = None
    artifact.share_scope = None
    artifact.share_space_ids_json = None
    artifact.share_checksum = None
    artifact.updated_at = now()
    db.flush()
    return artifact


def artifact_manifest(artifact: Artifact) -> dict[str, Any]:
    try:
        parsed = json.loads(getattr(artifact, "manifest_json", "{}") or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def list_owned_artifacts(db: Session, owner_user_id: str, *, trash: bool = False) -> list[Artifact]:
    condition = Artifact.deleted_at.is_not(None) if trash else Artifact.deleted_at.is_(None)
    return list(db.scalars(select(Artifact).where(Artifact.owner_user_id == owner_user_id, condition).order_by(Artifact.created_at.desc(), Artifact.id.desc())))


def owned_artifact(db: Session, artifact_id: str, owner_user_id: str, *, include_deleted: bool = False) -> Artifact | None:
    statement = select(Artifact).where(Artifact.id == artifact_id, Artifact.owner_user_id == owner_user_id)
    if not include_deleted:
        statement = statement.where(Artifact.deleted_at.is_(None))
    return db.scalar(statement)


def shared_artifact(db: Session, share_id: str, user: User) -> Artifact | None:
    from app.services.authorization import PermissionService

    artifact = db.scalar(select(Artifact).where(Artifact.share_id == share_id, Artifact.deleted_at.is_(None)))
    if artifact is None or user.status != "active" or artifact.status != "ready" or artifact.share_checksum != artifact.checksum_sha256:
        return None
    owner = db.get(User, artifact.owner_user_id)
    if owner is None or owner.status != "active":
        return None
    if user.id == artifact.owner_user_id or artifact.share_scope == "all_internal":
        return artifact
    if artifact.share_scope != "space":
        return None
    space_ids = json.loads(artifact.share_space_ids_json or "[]")
    if space_ids and all(
        PermissionService.check_space_owner(db, space_id, user.id)
        or PermissionService.has_space_member_role(db, user, space_id, minimum_role="reader")
        for space_id in space_ids
    ):
        return artifact
    return None


def artifact_source_version_ids(artifact: Artifact) -> list[str]:
    try:
        parsed = json.loads(artifact.source_version_ids or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in parsed if isinstance(item, str)]


def has_downloadable_snapshot(artifact: Artifact) -> bool:
    return bool(artifact.content and artifact.checksum_sha256 and artifact.content_type and artifact.content_size == len(artifact.content) and hashlib.sha256(artifact.content).hexdigest() == artifact.checksum_sha256)
