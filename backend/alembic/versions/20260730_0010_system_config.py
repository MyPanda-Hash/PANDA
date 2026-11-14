"""Create system_config table.

Revision ID: 20260730_0010
Revises: 20260727_0009
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "20260730_0010"
down_revision = "20260727_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_config",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_config")
