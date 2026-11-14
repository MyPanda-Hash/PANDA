from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

StrictModel = ConfigDict(extra="forbid")


class ScheduleRule(BaseModel):
    model_config = StrictModel

    kind: Literal["once", "daily", "weekly", "monthly"]
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    date: str | None = None
    weekdays: list[int] | None = None
    month_day: int | Literal["last"] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ScheduleRule:
        if self.kind == "once":
            if (
                self.date is None
                or self.weekdays is not None
                or self.month_day is not None
            ):
                raise ValueError("一次性规则必须且只能提供 date")
            try:
                date.fromisoformat(self.date)
            except ValueError as exc:
                raise ValueError("date 必须是有效的 YYYY-MM-DD 日期") from exc
        elif self.kind == "weekly":
            if not self.weekdays or self.date is not None or self.month_day is not None:
                raise ValueError("每周规则必须且只能提供 weekdays")
            if len(set(self.weekdays)) != len(self.weekdays) or any(
                day < 1 or day > 7 for day in self.weekdays
            ):
                raise ValueError("weekdays 必须是无重复的 1 到 7")
            self.weekdays = sorted(self.weekdays)
        elif self.kind == "monthly":
            if (
                self.month_day is None
                or self.date is not None
                or self.weekdays is not None
            ):
                raise ValueError("每月规则必须且只能提供 month_day")
            if isinstance(self.month_day, int) and not 1 <= self.month_day <= 31:
                raise ValueError("month_day 必须是 1 到 31 或 last")
        elif (
            self.date is not None
            or self.weekdays is not None
            or self.month_day is not None
        ):
            raise ValueError("每日规则不能提供 date、weekdays 或 month_day")
        return self


class ExternalNotificationRule(BaseModel):
    model_config = StrictModel

    target_id: str = Field(min_length=1, max_length=64)
    target_revision: int = Field(ge=1)
    events: list[Literal["completed", "failed", "awaiting_user", "outcome_unknown"]] = Field(
        min_length=1, max_length=4
    )
    content_mode: Literal["status_only"] = "status_only"

    @field_validator("events")
    @classmethod
    def unique_events(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("外部通知事件不能重复")
        return value


class ScheduleNotificationConfig(BaseModel):
    model_config = StrictModel

    sound: bool = False
    desktop: bool = False
    on_success: bool = False
    on_failure: bool = True
    external: list[ExternalNotificationRule] = Field(default_factory=list, max_length=5)

    @field_validator("external")
    @classmethod
    def unique_targets(
        cls, value: list[ExternalNotificationRule]
    ) -> list[ExternalNotificationRule]:
        if len({item.target_id for item in value}) != len(value):
            raise ValueError("外部通知目标不能重复")
        return value



class ScheduleConfig(BaseModel):
    model_config = StrictModel

    name: str = Field(min_length=1, max_length=120)
    kind: Literal["reminder", "skill"]
    instruction: str = Field(min_length=1, max_length=12_000)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=128)
    rule: ScheduleRule
    skill_id: str | None = Field(default=None, min_length=1, max_length=128)
    skill_version: str | None = Field(default=None, min_length=1, max_length=128)
    skill_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model: str | None = Field(default=None, min_length=1, max_length=160)
    space_ids: list[str] = Field(default_factory=list, max_length=100)
    document_ids: list[str] = Field(default_factory=list, max_length=200)
    late_window_minutes: int | None = Field(default=None, ge=0, le=1_440)
    budget_tokens: int = Field(default=500_000, ge=1, le=500_000)
    timeout_seconds: int = Field(default=600, ge=30, le=3_600)
    max_attempts: int = Field(default=3, ge=1, le=3)
    notification: ScheduleNotificationConfig = Field(
        default_factory=ScheduleNotificationConfig
    )

    @field_validator("name", "instruction")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value

    @field_validator("space_ids", "document_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item or len(item) > 128 for item in cleaned):
            raise ValueError("资源 ID 无效")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("资源 ID 不能重复")
        return cleaned

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone 必须是有效的 IANA 时区") from exc
        return value

    @model_validator(mode="after")
    def validate_kind(self) -> ScheduleConfig:
        binding = (self.skill_id, self.skill_version, self.skill_digest)
        if self.kind == "reminder" and any(item is not None for item in binding):
            raise ValueError("提醒不能绑定 Skill")
        if self.kind == "reminder" and self.model is not None:
            raise ValueError("提醒不能指定模型")
        if self.late_window_minutes is None:
            self.late_window_minutes = 15 if self.kind == "reminder" else 60
        return self


class DraftCreateRequest(BaseModel):
    model_config = StrictModel

    config: ScheduleConfig
    request_id: str = Field(min_length=1, max_length=128)
    source_session_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_message_id: str | None = Field(default=None, min_length=1, max_length=64)
    operation: Literal["create", "update", "pause", "resume", "archive", "run_now"] = (
        "create"
    )
    target_schedule_id: str | None = Field(default=None, min_length=1, max_length=64)
    target_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_source_and_target(self) -> DraftCreateRequest:
        if (self.source_session_id is None) != (self.source_message_id is None):
            raise ValueError("source session 和 message 必须同时提供")
        if self.operation == "create":
            if self.target_schedule_id is not None or self.target_revision is not None:
                raise ValueError("创建草案不能指定目标计划")
        elif self.target_schedule_id is None or self.target_revision is None:
            raise ValueError("管理草案必须指定目标计划及版本")
        return self


class DraftPatchRequest(BaseModel):
    model_config = StrictModel

    expected_revision: int = Field(ge=1)
    config: ScheduleConfig
    request_id: str = Field(min_length=1, max_length=128)


class DraftPreviewRequest(BaseModel):
    model_config = StrictModel

    expected_revision: int = Field(ge=1)


class DraftConfirmRequest(BaseModel):
    model_config = StrictModel

    expected_revision: int = Field(ge=1)
    preview_token: str | None = Field(default=None, min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=128)
    enabled: bool = True


class SchedulePatchRequest(BaseModel):
    model_config = StrictModel

    expected_revision: int = Field(ge=1)
    config: ScheduleConfig
    request_id: str = Field(min_length=1, max_length=128)


class ScheduleActionRequest(BaseModel):
    model_config = StrictModel

    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)


class RunActionRequest(BaseModel):
    model_config = StrictModel

    request_id: str = Field(min_length=1, max_length=128)


class SnoozeRequest(RunActionRequest):
    until: datetime

    @field_validator("until")
    @classmethod
    def _until_within_bound(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("稍后提醒时间必须包含时区")
        if value > datetime.now(UTC) + timedelta(days=31):
            raise ValueError("稍后提醒时间最多只能设置 31 天内")
        return value


class RunFileDTO(BaseModel):
    model_config = StrictModel

    name: str
    href: str
    size: int | None = None
    mime_type: str | None = None


class RunProgressDTO(BaseModel):
    model_config = StrictModel

    message: str
    at: str | None = None


class RunAttemptDTO(BaseModel):
    model_config = StrictModel

    attempt_no: int
    status: str
    started_at: str
    completed_at: str | None = None
    error_code: str | None = None


class RunDTO(BaseModel):
    model_config = StrictModel

    id: str
    schedule_id: str
    kind: str
    status: str
    status_revision: int
    scheduled_for: str
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str
    body: str | None
    files: list[RunFileDTO]
    usage: dict[str, Any] | None
    progress: list[RunProgressDTO]
    error_code: str | None
    error_message_safe: str | None
    retryable: bool
    handled_at: str | None
    snoozed_until: str | None
    resource_path: str
    attempts: list[RunAttemptDTO]
    config: ScheduleConfig


class ScheduleDTO(BaseModel):
    model_config = StrictModel

    id: str
    name: str
    kind: str
    status: str
    revision: int
    config: ScheduleConfig
    next_run_at: str | None
    pending_snooze_until: str | None
    blocked_reason: str | None
    created_at: str
    updated_at: str
    latest_run: RunDTO | None
    resource_path: str


class DraftDTO(BaseModel):
    model_config = StrictModel

    id: str
    revision: int
    status: str
    config: ScheduleConfig
    source_session_id: str | None
    source_message_id: str | None
    skill_request_id: str | None
    schedule_id: str | None
    expires_at: str
    blocked_reason: str | None
    operation: str
    target_schedule_id: str | None
    target_revision: int | None


class DraftListDTO(BaseModel):
    model_config = StrictModel

    items: list[DraftDTO]


class DraftPreviewDTO(BaseModel):
    model_config = StrictModel

    draft: DraftDTO
    next_runs: list[str]
    preview_token: str | None
    blockers: list[str]


class ScheduleListDTO(BaseModel):
    model_config = StrictModel

    items: list[ScheduleDTO]
    next_offset: int | None


class RunListDTO(BaseModel):
    model_config = StrictModel

    items: list[RunDTO]
    next_offset: int | None


class NotificationDTO(BaseModel):
    model_config = StrictModel

    event_id: str
    run_id: str
    schedule_id: str
    status: str
    title: str
    resource_path: str
    created_at: str
    expires_at: str
    announce: bool
    sound: bool
    desktop: bool
    # 与任务中心同一语义：提醒已触发但尚未处理，需要用户确认。
    requires_action: bool


class DeliveryDTO(BaseModel):
    model_config = StrictModel

    id: str
    target_label: str
    channel: str
    status: str
    created_at: str
    updated_at: str
    last_error_code: str | None


class DeliveryListDTO(BaseModel):
    model_config = StrictModel

    items: list[DeliveryDTO]


class NotificationListDTO(BaseModel):
    model_config = StrictModel

    items: list[NotificationDTO]
    cursor: str
    has_more: bool


class CapabilityDTO(BaseModel):
    model_config = StrictModel

    id: str
    name: str
    version: str
    digest: str
    description: str


class CapabilityListDTO(BaseModel):
    model_config = StrictModel

    items: list[CapabilityDTO]
