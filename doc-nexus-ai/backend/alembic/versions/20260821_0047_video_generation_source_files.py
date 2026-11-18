"""Persist Bailian source file IDs for video first-frame retries.

Revision ID: 20260821_0047
Revises: 20260821_0046
"""

from alembic import op
import sqlalchemy as sa


revision = "20260821_0047"
down_revision = "20260821_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_generation_tasks",
        sa.Column("source_file_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("video_generation_tasks", "source_file_id")
