"""Persist Harness verification command profile IDs.

Revision ID: 20260815_0043
Revises: 20260815_0042
"""

from alembic import context, op
import sqlalchemy as sa

revision = "20260815_0043"
down_revision = "20260815_0042"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("verification_build_command_id", sa.String(128), True),
    ("verification_test_command_id", sa.String(128), True),
    ("verification_lint_command_id", sa.String(128), True),
    ("verification_custom_command_ids", sa.Text(), False),
)


def _has_column(table: str, column: str) -> bool:
    if context.is_offline_mode():
        return False
    inspector = sa.inspect(op.get_bind())
    return any(item["name"] == column for item in inspector.get_columns(table))


def upgrade() -> None:
    if context.is_offline_mode():
        for name, column_type, nullable in _COLUMNS:
            op.add_column(
                "harness_tasks",
                sa.Column(name, column_type, nullable=nullable, server_default="[]" if name.endswith("ids") else None),
            )
        return
    for name, column_type, nullable in _COLUMNS:
        if not _has_column("harness_tasks", name):
            op.add_column(
                "harness_tasks",
                sa.Column(
                    name,
                    column_type,
                    nullable=nullable,
                    server_default="[]" if name.endswith("ids") else None,
                ),
            )


def downgrade() -> None:
    for name, _, _ in reversed(_COLUMNS):
        if not context.is_offline_mode() and _has_column("harness_tasks", name):
            op.drop_column("harness_tasks", name)
