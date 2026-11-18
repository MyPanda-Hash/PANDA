from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import current_user
from app.core.database import transaction
from app.models.auth import User
from app.repositories import tasks as task_repository
from app.schemas.tasks import (
    TaskAcknowledgementRequest,
    TaskAcknowledgementResponse,
    TaskCenterItem,
    TaskListResponse,
    TaskView,
)


router = APIRouter(tags=["tasks"])


def _item_response(item: task_repository.TaskProjection) -> TaskCenterItem:
    return TaskCenterItem(
        id=item.id,
        source=item.source,
        source_id=item.source_id,
        owner_user_id=item.owner_user_id,
        kind=item.kind,
        status=item.status,
        label=item.label,
        resource_path=item.resource_path,
        retryable=item.retryable,
        retry_action=item.retry_action,
        cancel_action=item.cancel_action,
        error_message_safe=item.error_message_safe,
        created_at=item.created_at,
        updated_at=item.updated_at,
        completed_at=item.completed_at,
        unread=item.unread,
        requires_action=item.requires_action,
        scheduled_for=item.scheduled_for,
    )


@router.get("/tasks", response_model=TaskListResponse)
def list_tasks(
    view: TaskView = Query(default="attention"),
    kind: str | None = Query(default=None, min_length=1, max_length=64),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(current_user),
) -> TaskListResponse:
    if kind and kind not in task_repository.PUBLIC_TASK_KINDS:
        raise HTTPException(status_code=422, detail="不支持的任务类型")
    with transaction() as db:
        (
            items,
            next_offset,
            active_count,
            unread_count,
            attention_count,
        ) = task_repository.list_tasks(
            db,
            owner_user_id=user.id,
            view=view,
            kind=kind,
            offset=offset,
            limit=limit,
        )
        return TaskListResponse(
            items=[_item_response(item) for item in items],
            next_offset=next_offset,
            active_count=active_count,
            unread_count=unread_count,
            attention_count=attention_count,
        )


@router.post(
    "/tasks/acknowledgements",
    response_model=TaskAcknowledgementResponse,
)
def acknowledge_tasks(
    payload: TaskAcknowledgementRequest,
    user: User = Depends(current_user),
) -> TaskAcknowledgementResponse:
    with transaction() as db:
        try:
            acknowledged = task_repository.acknowledge_tasks(
                db, owner_user_id=user.id, task_ids=payload.task_ids
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        _, _, _, unread_count, _ = task_repository.list_tasks(
            db,
            owner_user_id=user.id,
            view="attention",
            offset=0,
            limit=100,
        )
        return TaskAcknowledgementResponse(
            acknowledged_ids=acknowledged,
            unread_count=unread_count,
        )
