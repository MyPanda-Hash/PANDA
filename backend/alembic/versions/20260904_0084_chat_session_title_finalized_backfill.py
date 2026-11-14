"""Treat pre-existing chat session titles as finalized.

Revision ID: 20260904_0084
Revises: 20260904_0083
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260904_0084"
down_revision = "20260904_0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing titles are considered finalized. Only sessions created after this
    # migration receive the one-shot LLM summary, so historical user titles stay intact.
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE chat_sessions SET title_finalized = :value"),
        {"value": True},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE chat_sessions SET title_finalized = :value"),
        {"value": False},
    )
