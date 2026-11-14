from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TaskSource = Literal["document", "writing", "image", "video", "schedule"]
TaskStatus = Literal[
    "queued",
    "running",
    "processing",
    "awaiting_user",
    "outcome_unknown",
    "completed",
    "failed",
    "cancelled",
    "skipped",
]
TaskView = Literal["attention", "active", "history"]

class TaskAction(BaseModel):
    path: str
    method: Literal["POST"]


class TaskCenterItem(BaseModel):
    id: str
    source: TaskSource
    source_id: str
    owner_user_id: str
    kind: str
    status: TaskStatus
    label: str
    resource_path: str | None = None
    retryable: bool = False
    retry_action: TaskAction | None = None
    cancel_action: TaskAction | None = None
    error_message_safe: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    unread: bool = False
    requires_action: bool = False
    # 仅定时任务：本次执行的原定触发时间，用于区分周期提醒的每一次触发。
    scheduled_for: datetime | None = None


class TaskListResponse(BaseModel):
    items: list[TaskCenterItem]
    next_offset: int | None = None
    active_count: int = 0
    unread_count: int = 0
    attention_count: int = 0


class TaskAcknowledgementRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=100)


class TaskAcknowledgementResponse(BaseModel):
    acknowledged_ids: list[str]
    unread_count: int
