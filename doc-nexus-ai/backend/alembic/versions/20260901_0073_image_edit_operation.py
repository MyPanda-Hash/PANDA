"""Track image generation and editing operations.

Revision ID: 20260901_0073
Revises: 20260831_0072
Create Date: 2026-09-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260901_0073"
down_revision = "20260831_0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("image_generation_tasks") as batch:
        batch.add_column(
            sa.Column(
                "operation",
                sa.String(length=16),
                nullable=False,
                server_default="generate",
            )
        )
        batch.create_check_constraint(
            "ck_image_generation_tasks_operation",
            "operation in ('generate', 'edit')",
        )


def downgrade() -> None:
    with op.batch_alter_table("image_generation_tasks") as batch:
        batch.drop_constraint(
            "ck_image_generation_tasks_operation",
            type_="check",
        )
        batch.drop_column("operation")
