"""Add sort_order to model_endpoints for chat model picker ordering.

Revision ID: 20260829_0062
Revises: 20260827_0061
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260829_0062"
down_revision = "20260827_0061"
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
        if "sort_order" not in columns:
            batch.add_column(
                sa.Column(
                    "sort_order",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )


def downgrade() -> None:
    columns = _column_names("model_endpoints")
    with op.batch_alter_table("model_endpoints") as batch:
        if "sort_order" in columns:
            batch.drop_column("sort_order")
