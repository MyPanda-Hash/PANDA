"""Add document_versions storage and processing columns.

Revision ID: 20260730_0011
Revises: 20260730_0010
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_0011"
down_revision = "20260730_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("file_path", sa.Text(), nullable=True))
    op.add_column("document_versions", sa.Column("ext_task_id", sa.String(length=128), nullable=True))
    op.add_column(
        "document_versions",
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "chunk_count")
    op.drop_column("document_versions", "ext_task_id")
    op.drop_column("document_versions", "file_path")
