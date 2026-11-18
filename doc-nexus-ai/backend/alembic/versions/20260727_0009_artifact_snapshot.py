"""Persist immutable export artifact snapshots.

Revision ID: 20260727_0009
Revises: 20260727_0008
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0009"
down_revision = "20260727_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("content", sa.LargeBinary(), nullable=True))
    op.add_column("artifacts", sa.Column("checksum_sha256", sa.String(length=64), nullable=True))
    op.add_column("artifacts", sa.Column("content_type", sa.String(length=255), nullable=True))
    op.add_column("artifacts", sa.Column("content_size", sa.Integer(), nullable=True))
    op.add_column("artifacts", sa.Column("source_version_ids", sa.Text(), nullable=True))
    # Earlier metadata-only artifacts cannot be downloaded safely; expire them
    # instead of representing a missing blob as an available export.
    op.execute("UPDATE artifacts SET status = 'expired' WHERE status = 'ready'")


def downgrade() -> None:
    op.drop_column("artifacts", "source_version_ids")
    op.drop_column("artifacts", "content_size")
    op.drop_column("artifacts", "content_type")
    op.drop_column("artifacts", "checksum_sha256")
    op.drop_column("artifacts", "content")
