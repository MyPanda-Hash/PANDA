"""Bind temporary chat attachments to a chat session.

Revision ID: 20260813_0039
Revises: 20260813_0038
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260813_0039"
down_revision = "20260813_0038"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    if context.is_offline_mode():
        return False
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return any(item["name"] == column_name for item in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    if context.is_offline_mode():
        return False
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return any(item["name"] == index_name for item in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_column("documents", "chat_session_id"):
        with op.batch_alter_table("documents") as batch:
            batch.add_column(sa.Column("chat_session_id", sa.String(length=64), nullable=True))
            batch.create_foreign_key(
                "fk_documents_chat_session_id",
                "chat_sessions",
                ["chat_session_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if not _has_index("documents", "ix_documents_chat_session_id"):
        op.create_index(
            "ix_documents_chat_session_id",
            "documents",
            ["chat_session_id"],
        )
    if not _has_index("documents", "ix_documents_temporary_chat_session"):
        op.create_index(
            "ix_documents_temporary_chat_session",
            "documents",
            ["is_temporary", "chat_session_id"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("documents")}
    if "ix_documents_temporary_chat_session" in indexes:
        op.drop_index("ix_documents_temporary_chat_session", table_name="documents")
    if "ix_documents_chat_session_id" in indexes:
        op.drop_index("ix_documents_chat_session_id", table_name="documents")
    if _has_column("documents", "chat_session_id"):
        with op.batch_alter_table("documents") as batch:
            batch.drop_constraint("fk_documents_chat_session_id", type_="foreignkey")
            batch.drop_column("chat_session_id")
