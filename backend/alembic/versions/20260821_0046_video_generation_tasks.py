"""Persist asynchronous Kling video generation tasks.

Revision ID: 20260821_0046
Revises: 20260820_0045
"""

from alembic import op
import sqlalchemy as sa


revision = "20260821_0046"
down_revision = "20260820_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_generation_tasks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("user_message_id", sa.String(length=64), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=64), nullable=False),
        sa.Column(
            "provider_task_id", sa.String(length=128), nullable=False, unique=True
        ),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "mode in ('std', 'pro', '4k')", name="ck_video_generation_tasks_mode"
        ),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_video_generation_tasks_status",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["chat_session_id"], ["chat_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_message_id"], ["chat_messages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"], ["chat_messages.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_video_generation_tasks_owner_created",
        "video_generation_tasks",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_video_generation_tasks_session_created",
        "video_generation_tasks",
        ["chat_session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_video_generation_tasks_session_created",
        table_name="video_generation_tasks",
    )
    op.drop_index(
        "ix_video_generation_tasks_owner_created",
        table_name="video_generation_tasks",
    )
    op.drop_table("video_generation_tasks")
