"""Add durable media dispatch decisions and submission states.

Revision ID: 20260903_0076
Revises: 20260902_0075
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260903_0076"
down_revision = "20260902_0075"
branch_labels = None
depends_on = None


_DISPATCH_INTENTS = (
    "'chat', 'image_generate', 'image_edit', 'video_generate', 'clarify'"
)
_DISPATCH_STATUSES = (
    "'resolved', 'awaiting_confirmation', 'dispatching', 'dispatched', "
    "'failed', 'outcome_unknown', 'superseded'"
)
_TASK_STATUSES = (
    "'pending', 'running', 'submitting', 'outcome_unknown', "
    "'succeeded', 'failed', 'canceled'"
)


def upgrade() -> None:
    op.create_table(
        "chat_dispatch_decisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("client_message_id", sa.String(length=128), nullable=False),
        sa.Column("requested_skill", sa.String(length=64), nullable=True),
        sa.Column("predicted_intent", sa.String(length=32), nullable=False),
        sa.Column(
            "effective_route",
            sa.String(length=32),
            nullable=False,
            server_default="chat",
        ),
        sa.Column("final_intent", sa.String(length=32), nullable=True),
        sa.Column("route_source", sa.String(length=32), nullable=False),
        sa.Column("confidence_band", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("classifier_model", sa.String(length=160), nullable=True),
        sa.Column("classifier_version", sa.String(length=64), nullable=True),
        sa.Column("decision_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("classifier_usage_json", sa.Text(), nullable=True),
        sa.Column("prompt_digest", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "attachment_digest",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="resolved",
        ),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["chat_session_id"], ["chat_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "client_message_id",
            name="uq_chat_dispatch_decisions_owner_client_message",
        ),
        sa.CheckConstraint(
            f"predicted_intent in ({_DISPATCH_INTENTS})",
            name="ck_chat_dispatch_decisions_predicted_intent",
        ),
        sa.CheckConstraint(
            f"effective_route in ({_DISPATCH_INTENTS})",
            name="ck_chat_dispatch_decisions_effective_route",
        ),
        sa.CheckConstraint(
            f"status in ({_DISPATCH_STATUSES})",
            name="ck_chat_dispatch_decisions_status",
        ),
    )
    op.create_index(
        "ix_chat_dispatch_decisions_owner_user_id",
        "chat_dispatch_decisions",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_dispatch_decisions_chat_session_id",
        "chat_dispatch_decisions",
        ["chat_session_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_dispatch_decisions_owner_created",
        "chat_dispatch_decisions",
        ["owner_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_chat_dispatch_decisions_session_created",
        "chat_dispatch_decisions",
        ["chat_session_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_chat_dispatch_decisions_status_updated",
        "chat_dispatch_decisions",
        ["status", "updated_at"],
        unique=False,
    )

    with op.batch_alter_table("chat_messages") as batch:
        batch.add_column(
            sa.Column(
                "dispatch_id",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            "fk_chat_messages_dispatch_id",
            "chat_dispatch_decisions",
            ["dispatch_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_chat_messages_dispatch_id",
        "chat_messages",
        ["dispatch_id"],
        unique=False,
    )

    with op.batch_alter_table("image_generation_tasks") as batch:
        batch.add_column(
            sa.Column(
                "dispatch_id",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            "fk_image_generation_tasks_dispatch_id",
            "chat_dispatch_decisions",
            ["dispatch_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.alter_column(
            "provider_task_id",
            existing_type=sa.String(length=128),
            nullable=True,
        )
        batch.alter_column(
            "status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
        )
        batch.drop_constraint(
            "ck_image_generation_tasks_status",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_image_generation_tasks_status",
            f"status in ({_TASK_STATUSES})",
        )
    op.create_index(
        "ix_image_generation_tasks_dispatch_id",
        "image_generation_tasks",
        ["dispatch_id"],
        unique=False,
    )

    with op.batch_alter_table("video_generation_tasks") as batch:
        batch.add_column(
            sa.Column(
                "dispatch_id",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            "fk_video_generation_tasks_dispatch_id",
            "chat_dispatch_decisions",
            ["dispatch_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.alter_column(
            "provider_task_id",
            existing_type=sa.String(length=128),
            nullable=True,
        )
        batch.alter_column(
            "status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
        )
        batch.drop_constraint(
            "ck_video_generation_tasks_status",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_video_generation_tasks_status",
            f"status in ({_TASK_STATUSES})",
        )
    op.create_index(
        "ix_video_generation_tasks_dispatch_id",
        "video_generation_tasks",
        ["dispatch_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in ("image_generation_tasks", "video_generation_tasks"):
        unresolved = bind.execute(
            sa.text(
                f"SELECT COUNT(*) FROM {table_name} "
                "WHERE provider_task_id IS NULL "
                "OR status IN ('submitting', 'outcome_unknown')"
            )
        ).scalar_one()
        if unresolved:
            raise RuntimeError(
                f"cannot downgrade media dispatch while {table_name} "
                "contains unresolved submissions"
            )
    op.drop_index("ix_video_generation_tasks_dispatch_id", table_name="video_generation_tasks")
    with op.batch_alter_table("video_generation_tasks") as batch:
        batch.drop_constraint("fk_video_generation_tasks_dispatch_id", type_="foreignkey")
        batch.drop_constraint("ck_video_generation_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_video_generation_tasks_status",
            "status in ('pending', 'running', 'succeeded', 'failed', 'canceled')",
        )
        batch.alter_column(
            "status",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
        )
        batch.alter_column(
            "provider_task_id",
            existing_type=sa.String(length=128),
            nullable=False,
        )
        batch.drop_column("dispatch_id")

    op.drop_index("ix_image_generation_tasks_dispatch_id", table_name="image_generation_tasks")
    with op.batch_alter_table("image_generation_tasks") as batch:
        batch.drop_constraint("fk_image_generation_tasks_dispatch_id", type_="foreignkey")
        batch.drop_constraint("ck_image_generation_tasks_status", type_="check")
        batch.create_check_constraint(
            "ck_image_generation_tasks_status",
            "status in ('pending', 'running', 'succeeded', 'failed', 'canceled')",
        )
        batch.alter_column(
            "status",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
        )
        batch.alter_column(
            "provider_task_id",
            existing_type=sa.String(length=128),
            nullable=False,
        )
        batch.drop_column("dispatch_id")

    op.drop_index("ix_chat_messages_dispatch_id", table_name="chat_messages")
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_constraint("fk_chat_messages_dispatch_id", type_="foreignkey")
        batch.drop_column("dispatch_id")

    op.drop_index(
        "ix_chat_dispatch_decisions_status_updated",
        table_name="chat_dispatch_decisions",
    )
    op.drop_index(
        "ix_chat_dispatch_decisions_session_created",
        table_name="chat_dispatch_decisions",
    )
    op.drop_index(
        "ix_chat_dispatch_decisions_owner_created",
        table_name="chat_dispatch_decisions",
    )
    op.drop_index(
        "ix_chat_dispatch_decisions_chat_session_id",
        table_name="chat_dispatch_decisions",
    )
    op.drop_index(
        "ix_chat_dispatch_decisions_owner_user_id",
        table_name="chat_dispatch_decisions",
    )
    op.drop_table("chat_dispatch_decisions")
