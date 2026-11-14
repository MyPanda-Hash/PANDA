"""Add persistent chat session archive state.

Revision ID: 20260902_0074
Revises: 20260901_0073
Create Date: 2026-09-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260902_0074"
down_revision = "20260901_0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(
            sa.Column(
                "archived",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_index(
            "ix_chat_sessions_owner_archived_updated",
            ["owner_user_id", "archived", "updated_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_index("ix_chat_sessions_owner_archived_updated")
        batch.drop_column("archived_at")
        batch.drop_column("archived")
