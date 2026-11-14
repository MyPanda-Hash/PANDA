"""Add api_version column to model_endpoints for Azure OpenAI.

Revision ID: 20260812_0035
Revises: 20260812_0034
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260812_0035"
down_revision = "20260812_0034"
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
        if "api_version" not in columns:
            batch.add_column(sa.Column("api_version", sa.String(64), nullable=False, server_default=""))


def downgrade() -> None:
    columns = _column_names("model_endpoints")
    with op.batch_alter_table("model_endpoints") as batch:
        if "api_version" in columns:
            batch.drop_column("api_version")
