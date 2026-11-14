"""Store the original relative path for uploaded document versions.

Revision ID: 20260811_0033
Revises: 20260811_0032
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260811_0033"
down_revision = "20260811_0032"
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
    columns = _column_names("document_versions")
    with op.batch_alter_table("document_versions") as batch:
        if "source_relative_path" not in columns:
            batch.add_column(sa.Column("source_relative_path", sa.Text(), nullable=True))


def downgrade() -> None:
    columns = _column_names("document_versions")
    with op.batch_alter_table("document_versions") as batch:
        if "source_relative_path" in columns:
            batch.drop_column("source_relative_path")
