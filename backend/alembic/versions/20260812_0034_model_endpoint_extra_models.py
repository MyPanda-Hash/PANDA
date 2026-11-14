"""Add extra_models column to model_endpoints.

Revision ID: 20260812_0034
Revises: 20260811_0033
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260812_0034"
down_revision = "20260811_0033"
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
    columns = _column_names("model_endpoints")
    with op.batch_alter_table("model_endpoints") as batch:
        if "extra_models" not in columns:
            batch.add_column(sa.Column("extra_models", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    columns = _column_names("model_endpoints")
    with op.batch_alter_table("model_endpoints") as batch:
        if "extra_models" in columns:
            batch.drop_column("extra_models")
