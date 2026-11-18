"""Expand writing_operations status CHECK to include dispatch states.

Revision ID: 20260802_0022
Revises: 20260802_0021
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260802_0022"
down_revision = "20260802_0021"
branch_labels = None
depends_on = None


_NEW_STATUS_CHECK = (
    "status in ('queued','dispatching','running','completed','failed',"
    "'cancelling','cancelled','stopped')"
)


def _table_sql(table_name: str) -> str | None:
    if context.is_offline_mode():
        return None
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        row = bind.execute(
            sa.text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name=:name"
            ),
            {"name": table_name},
        ).fetchone()
        return row[0] if row else None
    return None


def upgrade() -> None:
    table_sql = _table_sql("writing_operations")
    if table_sql is not None and "dispatching" in table_sql:
        return

    with op.batch_alter_table("writing_operations") as batch:
        batch.drop_constraint("ck_writing_operations_status", type_="check")
        batch.create_check_constraint(
            "ck_writing_operations_status",
            _NEW_STATUS_CHECK,
        )


def downgrade() -> None:
    with op.batch_alter_table("writing_operations") as batch:
        batch.drop_constraint("ck_writing_operations_status", type_="check")
        batch.create_check_constraint(
            "ck_writing_operations_status",
            "status in ('queued','running','completed','failed','stopped')",
        )
