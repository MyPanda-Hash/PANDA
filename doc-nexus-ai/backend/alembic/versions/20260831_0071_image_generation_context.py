"""Cache generated images for bounded chat context injection.

Revision ID: 20260831_0071
Revises: 20260831_0070
Create Date: 2026-08-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260831_0071"
down_revision = "20260831_0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "image_generation_tasks",
        sa.Column("image_data", sa.Text(), nullable=True),
    )
    op.add_column(
        "image_generation_tasks",
        sa.Column(
            "image_context_consumed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("image_generation_tasks", "image_context_consumed_at")
    op.drop_column("image_generation_tasks", "image_data")
