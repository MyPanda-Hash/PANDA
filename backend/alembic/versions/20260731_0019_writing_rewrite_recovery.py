"""Add durable input for rewrite and recovery workflows.

Revision ID: 20260731_0012
Revises: 20260730_0011
"""

from alembic import op
import sqlalchemy as sa


revision = "20260731_0019"
down_revision = "20260731_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("writing_operations") as batch:
        batch.add_column(
            sa.Column(
                "input_payload",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("writing_operations") as batch:
        batch.drop_column("input_payload")
