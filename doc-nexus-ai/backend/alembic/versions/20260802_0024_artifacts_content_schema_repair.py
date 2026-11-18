"""Repair artifacts snapshot columns missing from stamped SQLite DBs.

Revision ID: 20260802_0024
Revises: 20260802_0023
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260802_0024"
down_revision = "20260802_0023"
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
    columns = _column_names("artifacts")
    if not columns:
        return
    with op.batch_alter_table("artifacts") as batch:
        if "content" not in columns:
            batch.add_column(sa.Column("content", sa.LargeBinary(), nullable=True))
        if "checksum_sha256" not in columns:
            batch.add_column(sa.Column("checksum_sha256", sa.String(64), nullable=True))
        if "content_type" not in columns:
            batch.add_column(sa.Column("content_type", sa.String(255), nullable=True))
        if "content_size" not in columns:
            batch.add_column(sa.Column("content_size", sa.Integer(), nullable=True))
        if "source_version_ids" not in columns:
            batch.add_column(sa.Column("source_version_ids", sa.Text(), nullable=True))


def downgrade() -> None:
    # Repair revisions do not own historical columns.
    pass
