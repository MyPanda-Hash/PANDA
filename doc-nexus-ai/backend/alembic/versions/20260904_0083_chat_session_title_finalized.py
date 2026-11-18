"""Add title_finalized flag for LLM-summarized chat session titles.

Revision ID: 20260904_0083
Revises: 20260904_0082
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260904_0083"
down_revision = "20260904_0082"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(col["name"] == column for col in insp.get_columns(table))


def upgrade() -> None:
    # On databases stamped under the OLD revision numbering the title_finalized
    # column may already exist (an old 0080 added it). Skip the add so upgrade
    # head does not hard-fail with a duplicate-column error.
    if not _has_column("chat_sessions", "title_finalized"):
        with op.batch_alter_table("chat_sessions") as batch:
            batch.add_column(
                sa.Column(
                    "title_finalized",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade() -> None:
    if _has_column("chat_sessions", "title_finalized"):
        with op.batch_alter_table("chat_sessions") as batch:
            batch.drop_column("title_finalized")
