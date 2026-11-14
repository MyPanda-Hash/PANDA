"""Invalidate access tokens after password changes.

Revision ID: 20260831_0072
Revises: 20260831_0071
Create Date: 2026-08-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260831_0072"
down_revision = "20260831_0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "auth_version")
