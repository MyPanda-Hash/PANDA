"""Add durable generated-image cache reference to image tasks.

Revision ID: 20260903_0078
Revises: 20260903_0077
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260903_0078"
down_revision = "20260903_0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("image_generation_tasks") as batch:
        batch.add_column(
            sa.Column("image_cache_uri", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("image_generation_tasks") as batch:
        batch.drop_column("image_cache_uri")
