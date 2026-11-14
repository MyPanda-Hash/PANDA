"""Add document_ids_json to chat messages for document-scoped citation.

Revision ID: 20260804_0028
Revises: 20260803_0027
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260804_0028"
down_revision = "20260803_0027"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _column_names("chat_messages")
    with op.batch_alter_table("chat_messages") as batch:
        if "document_ids_json" not in columns:
            batch.add_column(
                sa.Column("document_ids_json", sa.Text(), nullable=False, server_default="[]")
            )


def downgrade() -> None:
    columns = _column_names("chat_messages")
    with op.batch_alter_table("chat_messages") as batch:
        if "document_ids_json" in columns:
            batch.drop_column("document_ids_json")
