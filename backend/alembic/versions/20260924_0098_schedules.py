"""Add durable schedules, runs, attempts, and notification events."""

import sqlalchemy as sa

from alembic import op

revision = "20260924_0098"
down_revision = "20260922_0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_reason", sa.String(length=128), nullable=True),
        sa.CheckConstraint("kind in ('reminder', 'skill')", name="ck_schedules_kind"),
        sa.CheckConstraint(
            "status in ('enabled', 'paused', 'blocked', 'completed', 'archived')",
            name="ck_schedules_status",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_schedules_owner_user_id", "schedules", ["owner_user_id"])
    op.create_index(
        "ix_schedules_owner_updated", "schedules", ["owner_user_id", "updated_at"]
    )
    op.create_index("ix_schedules_due", "schedules", ["status", "next_run_at"])

    op.create_table(
        "schedule_drafts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("source_session_id", sa.String(length=64), nullable=True),
        sa.Column("source_message_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default="draft", nullable=False
        ),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("skill_request_id", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("preview_token", sa.String(length=64), nullable=True),
        sa.Column("preview_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schedule_id", sa.String(length=64), nullable=True),
        sa.Column(
            "operation", sa.String(length=16), server_default="create", nullable=False
        ),
        sa.Column("target_schedule_id", sa.String(length=64), nullable=True),
        sa.Column("target_revision", sa.Integer(), nullable=True),
        sa.Column("blocked_reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('draft', 'waiting_skill', 'ready', 'confirmed', 'expired', 'cancelled')",
            name="ck_schedule_drafts_status",
        ),
        sa.CheckConstraint(
            "operation in ('create', 'update', 'pause', 'resume', 'archive', 'run_now')",
            name="ck_schedule_drafts_operation",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedules.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id", "request_id", name="uq_schedule_drafts_owner_request"
        ),
    )
    op.create_index(
        "ix_schedule_drafts_owner_user_id", "schedule_drafts", ["owner_user_id"]
    )
    op.create_index(
        "ix_schedule_drafts_owner_created",
        "schedule_drafts",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_schedule_drafts_source",
        "schedule_drafts",
        ["owner_user_id", "source_session_id"],
    )

    op.create_table(
        "schedule_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("schedule_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("trigger_kind", sa.String(length=16), nullable=False),
        sa.Column("occurrence_key", sa.String(length=160), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("schedule_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("status_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "notification_revision", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("result_files_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("usage_json", sa.Text(), nullable=True),
        sa.Column("progress_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("runtime_id", sa.String(length=160), nullable=True),
        sa.Column("attempt_no", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claim_epoch", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=96), nullable=True),
        sa.Column("error_message_safe", sa.String(length=500), nullable=True),
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parent_run_id", sa.String(length=64), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint(
            "trigger_kind in ('scheduled', 'manual', 'snooze')",
            name="ck_schedule_runs_trigger_kind",
        ),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'completed', 'failed', 'cancelled', 'awaiting_user', 'outcome_unknown', 'skipped')",
            name="ck_schedule_runs_status",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_run_id"], ["schedule_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("runtime_id"),
        sa.UniqueConstraint(
            "schedule_id",
            "trigger_kind",
            "occurrence_key",
            name="uq_schedule_runs_occurrence",
        ),
    )
    op.create_index("ix_schedule_runs_schedule_id", "schedule_runs", ["schedule_id"])
    op.create_index(
        "ix_schedule_runs_owner_user_id", "schedule_runs", ["owner_user_id"]
    )
    op.create_index(
        "ix_schedule_runs_owner_created",
        "schedule_runs",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_schedule_runs_schedule_created",
        "schedule_runs",
        ["schedule_id", "created_at"],
    )
    op.create_index(
        "ix_schedule_runs_queue",
        "schedule_runs",
        ["status", "next_attempt_at", "lease_until"],
    )
    pending_notifications = sa.text(
        "notification_revision < status_revision AND status IN "
        "('completed', 'failed', 'cancelled', 'awaiting_user', 'outcome_unknown', 'skipped')"
    )
    op.create_index(
        "ix_schedule_runs_pending_notifications",
        "schedule_runs",
        ["updated_at"],
        sqlite_where=pending_notifications,
        postgresql_where=pending_notifications,
    )
    op.create_index(
        "uq_schedule_runs_single_flight",
        "schedule_runs",
        ["schedule_id"],
        unique=True,
        sqlite_where=sa.text(
            "status in ('queued', 'running', 'awaiting_user', 'outcome_unknown')"
        ),
        postgresql_where=sa.text(
            "status in ('queued', 'running', 'awaiting_user', 'outcome_unknown')"
        ),
    )

    op.create_table(
        "schedule_run_attempts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("claim_epoch", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=96), nullable=True),
        sa.Column("usage_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["schedule_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "attempt_no", name="uq_schedule_run_attempts_number"
        ),
    )
    op.create_index(
        "ix_schedule_run_attempts_run_id", "schedule_run_attempts", ["run_id"]
    )
    op.create_index(
        "ix_schedule_run_attempts_run",
        "schedule_run_attempts",
        ["run_id", "attempt_no"],
    )

    op.create_table(
        "schedule_notification_cursors",
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("last_sequence", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_user_id"),
    )
    op.create_table(
        "schedule_notifications",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=96), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("schedule_id", sa.String(length=64), nullable=False),
        sa.Column("status_revision", sa.Integer(), nullable=False),
        sa.Column(
            "event_kind", sa.String(length=32), server_default="status", nullable=False
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("resource_path", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("announce", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("sound", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("desktop", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["schedule_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
        sa.UniqueConstraint(
            "run_id",
            "status_revision",
            "event_kind",
            name="uq_schedule_notifications_run_revision_kind",
        ),
        sa.UniqueConstraint(
            "owner_user_id", "sequence", name="uq_schedule_notifications_owner_sequence"
        ),
    )
    op.create_index(
        "ix_schedule_notifications_owner_user_id",
        "schedule_notifications",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_schedule_notifications_run_id", "schedule_notifications", ["run_id"]
    )
    op.create_index(
        "ix_schedule_notifications_schedule_id",
        "schedule_notifications",
        ["schedule_id"],
    )
    op.create_index(
        "ix_schedule_notifications_owner_sequence",
        "schedule_notifications",
        ["owner_user_id", "sequence"],
    )
    op.create_index(
        "ix_schedule_notifications_expires", "schedule_notifications", ["expires_at"]
    )

    op.create_table(
        "schedule_operations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("resource_key", sa.String(length=192), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("result_type", sa.String(length=24), nullable=False),
        sa.Column("result_id", sa.String(length=64), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "resource_key",
            "request_id",
            name="uq_schedule_operations_request",
        ),
    )
    op.create_index(
        "ix_schedule_operations_owner_user_id", "schedule_operations", ["owner_user_id"]
    )
    op.create_index(
        "ix_schedule_operations_owner_created",
        "schedule_operations",
        ["owner_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schedule_operations_owner_created", table_name="schedule_operations"
    )
    op.drop_index(
        "ix_schedule_operations_owner_user_id", table_name="schedule_operations"
    )
    op.drop_table("schedule_operations")
    op.drop_index(
        "ix_schedule_notifications_expires", table_name="schedule_notifications"
    )
    op.drop_index(
        "ix_schedule_notifications_owner_sequence", table_name="schedule_notifications"
    )
    op.drop_index(
        "ix_schedule_notifications_schedule_id", table_name="schedule_notifications"
    )
    op.drop_index(
        "ix_schedule_notifications_run_id", table_name="schedule_notifications"
    )
    op.drop_index(
        "ix_schedule_notifications_owner_user_id", table_name="schedule_notifications"
    )
    op.drop_table("schedule_notifications")
    op.drop_table("schedule_notification_cursors")
    op.drop_index("ix_schedule_run_attempts_run", table_name="schedule_run_attempts")
    op.drop_index("ix_schedule_run_attempts_run_id", table_name="schedule_run_attempts")
    op.drop_table("schedule_run_attempts")
    op.drop_index("uq_schedule_runs_single_flight", table_name="schedule_runs")
    op.drop_index("ix_schedule_runs_pending_notifications", table_name="schedule_runs")
    op.drop_index("ix_schedule_runs_queue", table_name="schedule_runs")
    op.drop_index("ix_schedule_runs_schedule_created", table_name="schedule_runs")
    op.drop_index("ix_schedule_runs_owner_created", table_name="schedule_runs")
    op.drop_index("ix_schedule_runs_owner_user_id", table_name="schedule_runs")
    op.drop_index("ix_schedule_runs_schedule_id", table_name="schedule_runs")
    op.drop_table("schedule_runs")
    op.drop_index("ix_schedule_drafts_source", table_name="schedule_drafts")
    op.drop_index("ix_schedule_drafts_owner_created", table_name="schedule_drafts")
    op.drop_index("ix_schedule_drafts_owner_user_id", table_name="schedule_drafts")
    op.drop_table("schedule_drafts")
    op.drop_index("ix_schedules_due", table_name="schedules")
    op.drop_index("ix_schedules_owner_updated", table_name="schedules")
    op.drop_index("ix_schedules_owner_user_id", table_name="schedules")
    op.drop_table("schedules")
