"""Use adapter-neutral references for video first-frame files.

Revision ID: 20260821_0048
Revises: 20260821_0047
"""

from alembic import op
import sqlalchemy as sa


revision = "20260821_0048"
down_revision = "20260821_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_generation_tasks",
        sa.Column("source_file_ref", sa.String(length=512), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE video_generation_tasks "
            "SET source_file_ref = 'bailian://' || source_file_id "
            "WHERE source_file_id IS NOT NULL"
        )
    )
    op.drop_column("video_generation_tasks", "source_file_id")


def downgrade() -> None:
    op.add_column(
        "video_generation_tasks",
        sa.Column("source_file_id", sa.String(length=128), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE video_generation_tasks "
            "SET source_file_id = replace(source_file_ref, 'bailian://', '') "
            "WHERE source_file_ref IS NOT NULL"
        )
    )
    op.drop_column("video_generation_tasks", "source_file_ref")
