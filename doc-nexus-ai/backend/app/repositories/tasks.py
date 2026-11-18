from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import as_utc, new_token, now
from app.models.documents import Document, DocumentJob, DocumentVersion
from app.models.image_generation import ImageGenerationTask
from app.models.schedules import Schedule, ScheduleRun
from app.models.user_closure import TaskAcknowledgement
from app.models.video_generation import VideoGenerationTask
from app.models.writing import WritingOperation, WritingProject
from app.repositories import documents as document_repository


TaskSource = Literal["document", "writing", "image", "video", "schedule"]
TaskView = Literal["attention", "active", "history"]

PUBLIC_TASK_KINDS = frozenset(
    {
        "document_processing",
        "outline",
        "document",
        "rewrite",
        "verify",
        "export",
        "batch_generation",
        "manual_save",
        "version_restore",
        "restore_backup",
        "image_generation",
        "video_generation",
        "schedule_reminder",
        "schedule_skill",
    }
)
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "skipped"})
UNREAD_RESULT_STATUSES = TERMINAL_STATUSES - {"skipped"}
ACTIVE_STATUSES = frozenset({"queued", "running", "processing"})
# 供应商是否已接单无法确认：没有任何后台流程会让它继续推进，只能由用户显式确认。
UNRESOLVED_STATUSES = frozenset({"outcome_unknown"})
ACKNOWLEDGEABLE_STATUSES = TERMINAL_STATUSES | UNRESOLVED_STATUSES
_ATTENTION_STATUSES = frozenset({"awaiting_user"})
_RECENT_TERMINAL_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class TaskProjection:
    id: str
    source: TaskSource
    source_id: str
    owner_user_id: str
    kind: str
    status: str
    label: str
    resource_path: str | None
    retryable: bool
    retry_action: dict[str, str] | None
    cancel_action: dict[str, str] | None
    error_message_safe: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    unread: bool = False
    requires_action: bool = False
    # 仅定时任务：本次执行的原定触发时间（与 RunDTO.scheduled_for 同义）。
    scheduled_for: datetime | None = None


def _normalize_status(status: str, *, source: TaskSource) -> str:
    """Map source-specific states to the task-center status vocabulary."""
    value = str(status or "").strip().lower()
    if value == "awaiting_user":
        return "awaiting_user"
    if source == "schedule":
        if value in {
            "queued",
            "running",
            "completed",
            "failed",
            "cancelled",
            "outcome_unknown",
            "skipped",
        }:
            return value
        return "running"
    if source == "document":
        if value in {"completed", "ready"}:
            return "completed"
        if value == "failed":
            return "failed"
        if value == "cancelled":
            return "cancelled"
        if value == "queued":
            return "queued"
        if value in {
            "processing",
            "scanning",
            "parsing",
            "extracting_text",
            "rendering_pages",
            "running_ocr",
            "quality_check",
            "chunking",
            "indexing",
        }:
            return "processing"
        return "running"
    if source == "writing":
        if value in {"completed", "ready"}:
            return "completed"
        if value == "failed":
            return "failed"
        if value in {"cancelled", "stopped"}:
            return "cancelled"
        if value in {"queued", "dispatching"}:
            return "queued"
        if value in {"processing", "cancelling"}:
            return "processing"
        return "running"
    if value == "outcome_unknown":
        return "outcome_unknown"
    if value in {"succeeded", "completed", "ready"}:
        return "completed"
    if value == "failed":
        return "failed"
    if value in {"canceled", "cancelled"}:
        return "cancelled"
    if value in {"pending", "queued"}:
        return "queued"
    if value == "processing":
        return "processing"
    return "running"




def _safe_message(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).strip()[:500] or None


def _document_items(
    db: Session,
    *,
    owner_user_id: str,
    limit: int | None,
    kind: str | None = None,
    source_id: str | None = None,
) -> list[TaskProjection]:
    statement = (
        select(DocumentJob, Document, DocumentVersion)
        .join(Document, Document.id == DocumentJob.document_id)
        .join(DocumentVersion, DocumentVersion.id == DocumentJob.version_id)
        .where(
            DocumentJob.created_by_user_id == owner_user_id,
            Document.status == "active",
        )
    )
    if kind:
        statement = statement.where(DocumentJob.kind == kind)
    if source_id:
        statement = statement.where(DocumentJob.id == source_id)
    statement = statement.order_by(
        DocumentJob.updated_at.desc(), DocumentJob.id.desc()
    )
    if limit is not None:
        statement = statement.limit(limit)
    items: list[TaskProjection] = []
    for job, document, version in db.execute(statement).all():
        snapshot = document_repository.task_snapshot(
            db, job, user_id=owner_user_id, is_admin=False
        )
        status = _normalize_status(str(snapshot["status"]), source="document")
        completed = (
            as_utc(job.updated_at)
            if status in TERMINAL_STATUSES
            else None
        )
        items.append(
            TaskProjection(
                id=f"document:{job.id}",
                source="document",
                source_id=job.id,
                owner_user_id=owner_user_id,
                kind=job.kind,
                status=status,
                label=f"文档处理：{document.name}",
                resource_path=document_repository.document_resource_path(document),
                retryable=bool(snapshot.get("retryable")),
                retry_action=snapshot.get("retry_action"),
                cancel_action=snapshot.get("cancel_action"),
                error_message_safe=_safe_message(version.failure_message),
                created_at=as_utc(job.created_at),
                updated_at=as_utc(job.updated_at),
                completed_at=completed,
            )
        )
    return items


def _writing_items(
    db: Session,
    *,
    owner_user_id: str,
    limit: int | None,
    kind: str | None = None,
    source_id: str | None = None,
) -> list[TaskProjection]:
    statement = (
        select(WritingOperation, WritingProject)
        .join(WritingProject, WritingProject.id == WritingOperation.project_id)
        .where(WritingProject.owner_user_id == owner_user_id)
    )
    if kind:
        statement = statement.where(WritingOperation.kind == kind)
    if source_id:
        statement = statement.where(WritingOperation.id == source_id)
    operation_updated_at = func.coalesce(
        WritingOperation.completed_at,
        WritingOperation.started_at,
        WritingOperation.created_at,
    )
    statement = statement.order_by(
        operation_updated_at.desc(), WritingOperation.id.desc()
    )
    if limit is not None:
        statement = statement.limit(limit)
    items: list[TaskProjection] = []
    for operation, project in db.execute(statement).all():
        status = _normalize_status(operation.status, source="writing")
        active = status in ACTIVE_STATUSES
        items.append(
            TaskProjection(
                id=f"writing:{operation.id}",
                source="writing",
                source_id=operation.id,
                owner_user_id=owner_user_id,
                kind=operation.kind,
                status=status,
                label=f"写作：{project.title}",
                resource_path=f"/writing/{project.id}",
                retryable=False,
                retry_action=None,
                cancel_action=(
                    {
                        "path": f"/writing/operations/{operation.id}:cancel",
                        "method": "POST",
                    }
                    if active
                    else None
                ),
                error_message_safe=_safe_message(operation.error_message_safe),
                created_at=as_utc(operation.created_at),
                updated_at=as_utc(
                    operation.completed_at
                    or operation.started_at
                    or operation.created_at
                ),
                completed_at=(
                    as_utc(operation.completed_at)
                    if operation.completed_at
                    else None
                ),
            )
        )
    return items


def _image_items(
    db: Session,
    *,
    owner_user_id: str,
    limit: int | None,
    kind: str | None = None,
    source_id: str | None = None,
) -> list[TaskProjection]:
    if kind and kind != "image_generation":
        return []
    statement = select(ImageGenerationTask).where(
        ImageGenerationTask.owner_user_id == owner_user_id
    )
    if source_id:
        statement = statement.where(ImageGenerationTask.id == source_id)
    statement = statement.order_by(
        ImageGenerationTask.updated_at.desc(), ImageGenerationTask.id.desc()
    )
    if limit is not None:
        statement = statement.limit(limit)
    items: list[TaskProjection] = []
    for task in db.scalars(statement):
        status = _normalize_status(task.status, source="image")
        items.append(
            TaskProjection(
                id=f"image:{task.id}",
                source="image",
                source_id=task.id,
                owner_user_id=owner_user_id,
                kind="image_generation",
                status=status,
                label="图片生成任务",
                resource_path=f"/chat/{task.chat_session_id}",
                retryable=status == "failed",
                retry_action=(
                    {
                        "path": f"/image-generation/tasks/{task.id}/retry",
                        "method": "POST",
                    }
                    if status == "failed"
                    else None
                ),
                cancel_action=None,
                error_message_safe=_safe_message(task.error_message),
                created_at=as_utc(task.created_at),
                updated_at=as_utc(task.updated_at),
                completed_at=(
                    as_utc(task.completed_at) if task.completed_at else None
                ),
            )
        )
    return items


def _video_items(
    db: Session,
    *,
    owner_user_id: str,
    limit: int | None,
    kind: str | None = None,
    source_id: str | None = None,
) -> list[TaskProjection]:
    if kind and kind != "video_generation":
        return []
    statement = select(VideoGenerationTask).where(
        VideoGenerationTask.owner_user_id == owner_user_id
    )
    if source_id:
        statement = statement.where(VideoGenerationTask.id == source_id)
    statement = statement.order_by(
        VideoGenerationTask.updated_at.desc(), VideoGenerationTask.id.desc()
    )
    if limit is not None:
        statement = statement.limit(limit)
    items: list[TaskProjection] = []
    for task in db.scalars(statement):
        status = _normalize_status(task.status, source="video")
        items.append(
            TaskProjection(
                id=f"video:{task.id}",
                source="video",
                source_id=task.id,
                owner_user_id=owner_user_id,
                kind="video_generation",
                status=status,
                label="视频生成任务",
                resource_path=f"/chat/{task.chat_session_id}",
                retryable=status == "failed",
                retry_action=(
                    {
                        "path": f"/video-generation/tasks/{task.id}/retry",
                        "method": "POST",
                    }
                    if status == "failed"
                    else None
                ),
                cancel_action=None,
                error_message_safe=_safe_message(task.error_message),
                created_at=as_utc(task.created_at),
                updated_at=as_utc(task.updated_at),
                completed_at=(
                    as_utc(task.completed_at) if task.completed_at else None
                ),
            )
        )
    return items


def _schedule_items(
    db: Session,
    *,
    owner_user_id: str,
    limit: int | None,
    kind: str | None = None,
    source_id: str | None = None,
) -> list[TaskProjection]:
    if kind and kind not in {"schedule_reminder", "schedule_skill"}:
        return []
    # 周期提醒每触发一次都会留下一条待处理记录；只有该计划最新一条仍未处理的提醒
    # 进入「提醒待处理」，更早的未处理提醒降级为未读结果，标为已读即离开「需要关注」。
    newest_unhandled_run_id = (
        select(ScheduleRun.id)
        .where(
            ScheduleRun.schedule_id == Schedule.id,
            ScheduleRun.owner_user_id == owner_user_id,
            ScheduleRun.status == "completed",
            ScheduleRun.handled_at.is_(None),
            ScheduleRun.snoozed_until.is_(None),
        )
        .order_by(ScheduleRun.scheduled_for.desc(), ScheduleRun.id.desc())
        .limit(1)
        .correlate(Schedule)
        .scalar_subquery()
    )
    # 该计划已经处理过的最晚一次触发；比它更早的未处理提醒已被后来的处理覆盖，
    # 不能再被提升为「提醒待处理」，否则用户处理最新一条后会逐条反弹。
    newest_handled_at = (
        select(func.max(ScheduleRun.scheduled_for))
        .where(
            ScheduleRun.schedule_id == Schedule.id,
            ScheduleRun.owner_user_id == owner_user_id,
            ScheduleRun.handled_at.is_not(None),
        )
        .correlate(Schedule)
        .scalar_subquery()
    )
    statement = (
        select(
            ScheduleRun,
            Schedule,
            newest_unhandled_run_id.label("newest_unhandled_run_id"),
            newest_handled_at.label("newest_handled_at"),
        )
        .join(Schedule, Schedule.id == ScheduleRun.schedule_id)
        .where(
            ScheduleRun.owner_user_id == owner_user_id,
            Schedule.owner_user_id == owner_user_id,
            ScheduleRun.snoozed_until.is_(None),
        )
    )
    if kind:
        statement = statement.where(
            Schedule.kind == kind.removeprefix("schedule_")
        )
    if source_id:
        statement = statement.where(ScheduleRun.id == source_id)
    statement = statement.order_by(
        ScheduleRun.updated_at.desc(), ScheduleRun.id.desc()
    )
    if limit is not None:
        statement = statement.limit(limit)
    items: list[TaskProjection] = []
    for run, schedule, newest_unhandled_id, newest_handled_for in db.execute(statement).all():
        status = _normalize_status(run.status, source="schedule")
        active = status in ACTIVE_STATUSES
        label_prefix = "提醒" if schedule.kind == "reminder" else "定时任务"
        items.append(
            TaskProjection(
                id=f"schedule:{run.id}",
                source="schedule",
                source_id=run.id,
                owner_user_id=owner_user_id,
                kind=f"schedule_{schedule.kind}",
                status=status,
                requires_action=(
                    schedule.kind == "reminder"
                    and status == "completed"
                    and run.handled_at is None
                    and run.id == newest_unhandled_id
                    and (
                        newest_handled_for is None
                        or as_utc(run.scheduled_for) > as_utc(newest_handled_for)
                    )
                ),
                label=f"{label_prefix}：{schedule.name}",
                resource_path=f"/schedules/{schedule.id}/runs/{run.id}",
                retryable=status == "failed" and bool(run.retryable),
                retry_action=(
                    {
                        "path": f"/schedules/{schedule.id}/runs/{run.id}/retry",
                        "method": "POST",
                    }
                    if status == "failed" and run.retryable
                    else None
                ),
                cancel_action=(
                    {
                        "path": f"/schedules/{schedule.id}/runs/{run.id}/cancel",
                        "method": "POST",
                    }
                    if active
                    else None
                ),
                # 任务中心只投影状态；错误详情由带当前资料 ACL 的 run 页面返回。
                error_message_safe=None,
                created_at=as_utc(run.created_at),
                updated_at=as_utc(run.updated_at),
                completed_at=(
                    as_utc(run.completed_at) if run.completed_at else None
                ),
                scheduled_for=as_utc(run.scheduled_for),
            )
        )
    return items


def _source_items(
    db: Session,
    *,
    owner_user_id: str,
    limit: int | None,
    kind: str | None = None,
    source: TaskSource | None = None,
    source_id: str | None = None,
) -> list[TaskProjection]:
    sources = (source,) if source else (
        "document",
        "writing",
        "image",
        "video",
        "schedule",
    )
    result: list[TaskProjection] = []
    for item_source in sources:
        if item_source == "document":
            result.extend(
                _document_items(
                    db,
                    owner_user_id=owner_user_id,
                    limit=limit,
                    kind=kind,
                    source_id=source_id,
                )
            )
        elif item_source == "writing":
            result.extend(
                _writing_items(
                    db,
                    owner_user_id=owner_user_id,
                    limit=limit,
                    kind=kind,
                    source_id=source_id,
                )
            )
        elif item_source == "image":
            result.extend(
                _image_items(
                    db,
                    owner_user_id=owner_user_id,
                    limit=limit,
                    kind=kind,
                    source_id=source_id,
                )
            )
        elif item_source == "video":
            result.extend(
                _video_items(
                    db,
                    owner_user_id=owner_user_id,
                    limit=limit,
                    kind=kind,
                    source_id=source_id,
                )
            )
        else:
            result.extend(
                _schedule_items(
                    db,
                    owner_user_id=owner_user_id,
                    limit=limit,
                    kind=kind,
                    source_id=source_id,
                )
            )
    return result


def _with_unread(
    db: Session, *, owner_user_id: str, items: list[TaskProjection]
) -> list[TaskProjection]:
    keys = [item.id for item in items if item.status in ACKNOWLEDGEABLE_STATUSES]
    if not keys:
        return items
    acknowledgements = {
        row.task_key: row
        for row in db.scalars(
            select(TaskAcknowledgement).where(
                TaskAcknowledgement.owner_user_id == owner_user_id,
                TaskAcknowledgement.task_key.in_(keys),
            )
        )
    }
    return [
        TaskProjection(
            **{
                **item.__dict__,
                "unread": item.status in ACKNOWLEDGEABLE_STATUSES
                and (
                    acknowledgements.get(item.id) is None
                    or as_utc(acknowledgements[item.id].acknowledged_at)
                    < as_utc(item.updated_at)
                ),
            }
        )
        for item in items
    ]


def is_attention_task(item: TaskProjection, *, cutoff: datetime | None = None) -> bool:
    """任务是否仍需要用户关注：进行中、等待操作，或尚未确认的结果。

    cutoff 只作用于已结束但未确认的条目（列表用时间窗口忽略过旧提醒）。
    """
    if (
        item.requires_action
        or item.status in ACTIVE_STATUSES
        or item.status in _ATTENTION_STATUSES
    ):
        return True
    if item.status not in ACKNOWLEDGEABLE_STATUSES or not item.unread:
        return False
    return cutoff is None or as_utc(item.updated_at) >= cutoff


def list_tasks(
    db: Session,
    *,
    owner_user_id: str,
    view: TaskView = "attention",
    kind: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[TaskProjection], int | None, int, int, int]:
    if kind and kind not in PUBLIC_TASK_KINDS:
        raise ValueError("不支持的任务类型")
    items = _with_unread(
        db,
        owner_user_id=owner_user_id,
        items=_source_items(
            db,
            owner_user_id=owner_user_id,
            limit=None,
            kind=kind,
        ),
    )
    items.sort(key=lambda item: (as_utc(item.updated_at), item.id), reverse=True)
    cutoff = now() - _RECENT_TERMINAL_WINDOW
    active_count = sum(item.status in ACTIVE_STATUSES for item in items)
    # 未读结果只统计已有真实结果的终态：结果未知算「需关注」，不冒充未读结果。
    unread_count = sum(
        item.status in UNREAD_RESULT_STATUSES
        and item.unread
        and as_utc(item.updated_at) >= cutoff
        for item in items
    )
    attention_items = [
        item for item in items if is_attention_task(item, cutoff=cutoff)
    ]
    if view == "active":
        filtered = [item for item in items if item.status in ACTIVE_STATUSES]
    elif view == "attention":
        filtered = attention_items
    else:
        filtered = [
            item for item in items if item.status in ACKNOWLEDGEABLE_STATUSES
        ]
    page = filtered[offset : offset + limit]
    next_offset = offset + limit if len(filtered) > offset + limit else None
    return page, next_offset, active_count, unread_count, len(attention_items)


def task_by_key(
    db: Session, *, owner_user_id: str, task_key: str
) -> TaskProjection | None:
    source, separator, source_id = task_key.partition(":")
    if (
        not separator
        or source not in {"document", "writing", "image", "video", "schedule"}
        or not source_id
    ):
        return None
    items = _source_items(
        db,
        owner_user_id=owner_user_id,
        limit=1,
        source=source,  # type: ignore[arg-type]
        source_id=source_id,
    )
    if not items:
        return None
    return _with_unread(db, owner_user_id=owner_user_id, items=items)[0]


def acknowledge_tasks(
    db: Session, *, owner_user_id: str, task_ids: list[str]
) -> list[str]:
    unique_ids = list(dict.fromkeys(task_ids))
    projections = {
        task.id: task
        for task in (
            task_by_key(db, owner_user_id=owner_user_id, task_key=task_id)
            for task_id in unique_ids
        )
        if task is not None
    }
    if len(projections) != len(unique_ids):
        raise KeyError("任务不存在")
    stamp = now()
    existing = {
        row.task_key: row
        for row in db.scalars(
            select(TaskAcknowledgement).where(
                TaskAcknowledgement.owner_user_id == owner_user_id,
                TaskAcknowledgement.task_key.in_(unique_ids),
            )
        )
    }
    for task_id in unique_ids:
        row = existing.get(task_id)
        if row is None:
            db.add(
                TaskAcknowledgement(
                    id=f"tack_{new_token(10)}",
                    owner_user_id=owner_user_id,
                    task_key=task_id,
                    acknowledged_at=stamp,
                )
            )
        else:
            row.acknowledged_at = stamp
    db.flush()
    return unique_ids
