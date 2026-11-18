"""Allow nullable email on users for invite-without-email flows.

Revision ID: 20260826_0053
Revises: 20260824_0052
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260826_0053"
down_revision = "20260824_0052"
branch_labels = None
depends_on = None


def _column_nullable(table_name: str, column_name: str) -> bool | None:
    if context.is_offline_mode():
        return None
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return None
    for item in inspector.get_columns(table_name):
        if item["name"] == column_name:
            return bool(item.get("nullable"))
    return None


def upgrade() -> None:
    if _column_nullable("users", "email") is False:
        with op.batch_alter_table("users") as batch:
            batch.alter_column(
                "email", existing_type=sa.String(length=320), nullable=True
            )


def downgrade() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    missing = bind.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE email IS NULL")
    ).scalar()
    if missing:
        return
    if _column_nullable("users", "email") is True:
        with op.batch_alter_table("users") as batch:
            batch.alter_column(
                "email", existing_type=sa.String(length=320), nullable=False
            )
