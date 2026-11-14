"""Add optimistic concurrency revision for chat message snapshots.

Revision ID: 20260904_0080
Revises: 20260903_0079
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260904_0080"
down_revision = "20260903_0079"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(col["name"] == column for col in insp.get_columns(table))


def upgrade() -> None:
    # Guard against databases that already ran the OLD revision numbering, where
    # this id may have corresponded to a different migration, and against a later
    # re-stamp re-running this migration on an already-migrated database.
    if not _has_column("chat_sessions", "messages_revision"):
        with op.batch_alter_table("chat_sessions") as batch:
            batch.add_column(
                sa.Column(
                    "messages_revision",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )


def downgrade() -> None:
    if _has_column("chat_sessions", "messages_revision"):
        with op.batch_alter_table("chat_sessions") as batch:
            batch.drop_column("messages_revision")
