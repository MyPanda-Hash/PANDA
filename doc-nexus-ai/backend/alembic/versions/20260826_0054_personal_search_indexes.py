"""Add owner-first indexes used by personal history and upload search.

Revision ID: 20260826_0054
Revises: 20260826_0053
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260826_0054"
down_revision = "20260826_0053"
branch_labels = None
depends_on = None


def _has_index(table_name: str, index_name: str) -> bool:
    if context.is_offline_mode():
        return False
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(item["name"] == index_name for item in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_index("chat_messages", "ix_chat_messages_owner_role_created"):
        op.create_index(
            "ix_chat_messages_owner_role_created",
            "chat_messages",
            ["owner_user_id", "role", "created_at"],
        )
    if not _has_index("documents", "ix_documents_creator_status_updated"):
        op.create_index(
            "ix_documents_creator_status_updated",
            "documents",
            ["created_by_user_id", "status", "updated_at"],
        )


def downgrade() -> None:
    if context.is_offline_mode():
        return
    if _has_index("documents", "ix_documents_creator_status_updated"):
        op.drop_index("ix_documents_creator_status_updated", table_name="documents")
    if _has_index("chat_messages", "ix_chat_messages_owner_role_created"):
        op.drop_index("ix_chat_messages_owner_role_created", table_name="chat_messages")
