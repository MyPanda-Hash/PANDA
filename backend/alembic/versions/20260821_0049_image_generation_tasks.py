"""Persist asynchronous Kling image generation tasks.

Revision ID: 20260821_0049
Revises: 20260821_0048
"""

from alembic import op
import sqlalchemy as sa


revision = "20260821_0049"
down_revision = "20260821_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "image_generation_tasks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("chat_session_id", sa.String(length=64), nullable=False),
        sa.Column("user_message_id", sa.String(length=64), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=64), nullable=False),
        sa.Column(
            "provider_task_id", sa.String(length=128), nullable=False, unique=True
        ),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=8), nullable=False),
        sa.Column("resolution", sa.String(length=4), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "resolution in ('1k', '2k')",
            name="ck_image_generation_tasks_resolution",
        ),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_image_generation_tasks_status",
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
        "ix_image_generation_tasks_owner_created",
        "image_generation_tasks",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_image_generation_tasks_session_created",
        "image_generation_tasks",
        ["chat_session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_image_generation_tasks_session_created",
        table_name="image_generation_tasks",
    )
    op.drop_index(
        "ix_image_generation_tasks_owner_created",
        table_name="image_generation_tasks",
    )
    op.drop_table("image_generation_tasks")
