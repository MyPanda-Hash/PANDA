"""Add a generated API key hash to users.

Revision ID: 20260824_0050
Revises: 20260821_0049
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0050"
down_revision = "20260821_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("api_key_hash", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_users_api_key_hash",
        "users",
        ["api_key_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_users_api_key_hash", table_name="users")
    op.drop_column("users", "api_key_hash")
