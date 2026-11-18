"""Add preferred_model to chat_sessions.

Revision ID: 20260731_0015
Revises: 20260731_0014
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa


revision = "20260731_0015"
down_revision = "20260731_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("preferred_model", sa.String(length=160), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_sessions", "preferred_model")
