"""Persist free-chat sessions, messages, and categories.

Revision ID: 20260731_0014
Revises: 20260730_0013
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa


revision = "20260731_0014"
down_revision = "20260730_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_categories",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_categories_owner_user_id", "chat_categories", ["owner_user_id"])
    op.create_index("ix_chat_categories_owner_sort", "chat_categories", ["owner_user_id", "sort_order"])

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("agent_session_id", sa.String(length=128), nullable=True),
        sa.Column("category_id", sa.String(length=64), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("writing_project_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["chat_categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_sessions_owner_user_id", "chat_sessions", ["owner_user_id"])
    op.create_index("ix_chat_sessions_category_id", "chat_sessions", ["category_id"])
    op.create_index("ix_chat_sessions_owner_updated", "chat_sessions", ["owner_user_id", "updated_at"])
    op.create_index("ix_chat_sessions_owner_pinned", "chat_sessions", ["owner_user_id", "pinned", "pinned_at"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("thinking", sa.Text(), nullable=True),
        sa.Column("space_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("references_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("retrieval_summary", sa.Text(), nullable=True),
        sa.Column("writing_project_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
    op.create_index("ix_chat_messages_owner_user_id", "chat_messages", ["owner_user_id"])
    op.create_index("ix_chat_messages_session_created", "chat_messages", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_session_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_owner_user_id", table_name="chat_messages")
    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_owner_pinned", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_owner_updated", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_category_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_owner_user_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")
    op.drop_index("ix_chat_categories_owner_sort", table_name="chat_categories")
    op.drop_index("ix_chat_categories_owner_user_id", table_name="chat_categories")
    op.drop_table("chat_categories")
