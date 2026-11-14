"""Add chat session fork lineage columns.

Revision ID: 20260903_0077
Revises: 20260902_0075
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260903_0077"
down_revision = "20260903_0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(
            sa.Column("forked_from_session_id", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("forked_from_message_id", sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_column("forked_from_message_id")
        batch.drop_column("forked_from_session_id")
