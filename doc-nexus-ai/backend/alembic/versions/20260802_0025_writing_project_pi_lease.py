"""Add project-level Pi writing lease columns.

Revision ID: 20260802_0025
Revises: 20260802_0024
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260802_0025"
down_revision = "20260802_0024"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _column_names("writing_projects")
    with op.batch_alter_table("writing_projects") as batch:
        if "pi_lease_owner" not in columns:
            batch.add_column(sa.Column("pi_lease_owner", sa.String(128), nullable=True))
        if "pi_lease_operation_id" not in columns:
            batch.add_column(
                sa.Column("pi_lease_operation_id", sa.String(64), nullable=True)
            )
        if "pi_lease_expires_at" not in columns:
            batch.add_column(
                sa.Column("pi_lease_expires_at", sa.DateTime(timezone=True), nullable=True)
            )


def downgrade() -> None:
    columns = _column_names("writing_projects")
    with op.batch_alter_table("writing_projects") as batch:
        if "pi_lease_expires_at" in columns:
            batch.drop_column("pi_lease_expires_at")
        if "pi_lease_operation_id" in columns:
            batch.drop_column("pi_lease_operation_id")
        if "pi_lease_owner" in columns:
            batch.drop_column("pi_lease_owner")
