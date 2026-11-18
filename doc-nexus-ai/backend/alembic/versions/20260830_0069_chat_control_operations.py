"""Persist privacy-safe chat control operation traces.

Revision ID: 20260830_0069
Revises: 20260830_0068
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260830_0069"
down_revision = "20260830_0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_control_operations",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("usage", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_control_operations_owner_created",
        "chat_control_operations",
        ["owner_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_chat_control_operations_owner_user_id",
        "chat_control_operations",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_control_operations_session_id",
        "chat_control_operations",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_control_operations_session_id", table_name="chat_control_operations")
    op.drop_index("ix_chat_control_operations_owner_user_id", table_name="chat_control_operations")
    op.drop_index("ix_chat_control_operations_owner_created", table_name="chat_control_operations")
    op.drop_table("chat_control_operations")
