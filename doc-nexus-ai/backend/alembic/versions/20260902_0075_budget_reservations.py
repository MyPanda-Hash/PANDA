"""Add durable budget reservations for provider calls.

Revision ID: 20260902_0075
Revises: 20260902_0074
Create Date: 2026-09-02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260902_0075"
down_revision = "20260902_0074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("operation_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("space_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("estimated_cost", sa.String(length=32), nullable=False),
        sa.Column("held_cost", sa.String(length=32), nullable=False),
        sa.Column("actual_cost", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="reserved"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hold_until", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_budget_reservations_operation_id",
        "budget_reservations",
        ["operation_id"],
        unique=False,
    )
    op.create_index(
        "ix_budget_reservations_owner_user_id",
        "budget_reservations",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_budget_reservations_status",
        "budget_reservations",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_budget_reservations_hold_until",
        "budget_reservations",
        ["hold_until"],
        unique=False,
    )
    op.create_index(
        "ix_budget_reservations_status_hold_until",
        "budget_reservations",
        ["status", "hold_until"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_budget_reservations_status_hold_until",
        table_name="budget_reservations",
    )
    op.drop_index("ix_budget_reservations_hold_until", table_name="budget_reservations")
    op.drop_index("ix_budget_reservations_status", table_name="budget_reservations")
    op.drop_index(
        "ix_budget_reservations_owner_user_id",
        table_name="budget_reservations",
    )
    op.drop_index(
        "ix_budget_reservations_operation_id",
        table_name="budget_reservations",
    )
    op.drop_table("budget_reservations")
