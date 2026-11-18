"""Add usage JSON to chat_messages for token and latency stats.

Revision ID: 20260731_0017
Revises: 20260731_0016
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa


revision = "20260731_0017"
down_revision = "20260731_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("usage", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "usage")
