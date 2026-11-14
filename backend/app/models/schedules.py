from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class ScheduleDraft(Base):
    __tablename__ = "schedule_drafts"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "request_id", name="uq_schedule_drafts_owner_request"
        ),
        CheckConstraint(
            "status in ('draft', 'waiting_skill', 'ready', 'confirmed', 'expired', 'cancelled')",
            name="ck_schedule_drafts_status",
        ),
        CheckConstraint(
            "operation in ('create', 'update', 'pause', 'resume', 'archive', 'run_now')",
            name="ck_schedule_drafts_operation",
        ),
        Index("ix_schedule_drafts_owner_created", "owner_user_id", "created_at"),
        Index("ix_schedule_drafts_source", "owner_user_id", "source_session_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(
        String(24), default="draft", server_default="draft"
    )
    config_json: Mapped[str] = mapped_column(Text)
    skill_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    preview_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preview_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    schedule_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True
    )
    operation: Mapped[str] = mapped_column(
        String(16), default="create", server_default="create"
    )
    target_schedule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        CheckConstraint("kind in ('reminder', 'skill')", name="ck_schedules_kind"),
        CheckConstraint(
            "status in ('enabled', 'paused', 'blocked', 'completed', 'archived')",
            name="ck_schedules_status",
        ),
        Index("ix_schedules_owner_updated", "owner_user_id", "updated_at"),
        Index("ix_schedules_due", "status", "next_run_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="enabled")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    config_json: Mapped[str] = mapped_column(Text)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    blocked_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"
    __table_args__ = (
        UniqueConstraint(
            "schedule_id",
            "trigger_kind",
            "occurrence_key",
            name="uq_schedule_runs_occurrence",
        ),
        CheckConstraint(
            "trigger_kind in ('scheduled', 'manual', 'snooze')",
            name="ck_schedule_runs_trigger_kind",
        ),
        CheckConstraint(
            "status in ('queued', 'running', 'completed', 'failed', 'cancelled', 'awaiting_user', 'outcome_unknown', 'skipped')",
            name="ck_schedule_runs_status",
        ),
        Index("ix_schedule_runs_owner_created", "owner_user_id", "created_at"),
        Index("ix_schedule_runs_schedule_created", "schedule_id", "created_at"),
        Index("ix_schedule_runs_queue", "status", "next_attempt_at", "lease_until"),
        Index(
            "ix_schedule_runs_pending_notifications",
            "updated_at",
            sqlite_where=text(
                "notification_revision < status_revision AND status IN "
                "('completed', 'failed', 'cancelled', 'awaiting_user', 'outcome_unknown', 'skipped')"
            ),
            postgresql_where=text(
                "notification_revision < status_revision AND status IN "
                "('completed', 'failed', 'cancelled', 'awaiting_user', 'outcome_unknown', 'skipped')"
            ),
        ),
        Index(
            "uq_schedule_runs_single_flight",
            "schedule_id",
            unique=True,
            sqlite_where=text(
                "status in ('queued', 'running', 'awaiting_user', 'outcome_unknown')"
            ),
            postgresql_where=text(
                "status in ('queued', 'running', 'awaiting_user', 'outcome_unknown')"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    trigger_kind: Mapped[str] = mapped_column(String(16))
    occurrence_key: Mapped[str] = mapped_column(String(160))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    config_json: Mapped[str] = mapped_column(Text)
    schedule_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    status_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    notification_revision: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_files_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default="[]"
    )
    usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    runtime_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, unique=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    claim_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    error_message_safe: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    handled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    parent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedule_runs.id", ondelete="SET NULL"), nullable=True
    )
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class ScheduleRunAttempt(Base):
    __tablename__ = "schedule_run_attempts"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "attempt_no", name="uq_schedule_run_attempts_number"
        ),
        Index("ix_schedule_run_attempts_run", "run_id", "attempt_no"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_runs.id", ondelete="CASCADE"), index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer)
    claim_epoch: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24))
    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScheduleNotificationCursor(Base):
    __tablename__ = "schedule_notification_cursors"

    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    last_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ScheduleNotification(Base):
    __tablename__ = "schedule_notifications"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "status_revision",
            "event_kind",
            name="uq_schedule_notifications_run_revision_kind",
        ),
        UniqueConstraint(
            "owner_user_id", "sequence", name="uq_schedule_notifications_owner_sequence"
        ),
        Index(
            "ix_schedule_notifications_owner_sequence",
            "owner_user_id",
            "sequence",
        ),
        Index("ix_schedule_notifications_expires", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(String(96), unique=True)
    sequence: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("schedule_runs.id", ondelete="CASCADE"), index=True
    )
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), index=True
    )
    status_revision: Mapped[int] = mapped_column(Integer)
    event_kind: Mapped[str] = mapped_column(String(32), default="status")
    status: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(160))
    resource_path: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    announce: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    sound: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    desktop: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class ScheduleOperation(Base):
    __tablename__ = "schedule_operations"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "resource_key",
            "request_id",
            name="uq_schedule_operations_request",
        ),
        Index("ix_schedule_operations_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    resource_key: Mapped[str] = mapped_column(String(192))
    request_id: Mapped[str] = mapped_column(String(128))
    result_type: Mapped[str] = mapped_column(String(24))
    result_id: Mapped[str] = mapped_column(String(64))
    result_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
