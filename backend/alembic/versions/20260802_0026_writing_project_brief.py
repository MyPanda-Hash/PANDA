"""Add writing project brief and Pi session stage columns.

Revision ID: 20260802_0026
Revises: 20260802_0025
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260802_0026"
down_revision = "20260802_0025"
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
        if "brief_json" not in columns:
            batch.add_column(
                sa.Column("brief_json", sa.Text(), nullable=False, server_default="{}")
            )
        if "brief_revision" not in columns:
            batch.add_column(
                sa.Column("brief_revision", sa.Integer(), nullable=False, server_default="1")
            )
        if "pi_session_stage" not in columns:
            batch.add_column(sa.Column("pi_session_stage", sa.String(32), nullable=True))


def downgrade() -> None:
    columns = _column_names("writing_projects")
    with op.batch_alter_table("writing_projects") as batch:
        if "pi_session_stage" in columns:
            batch.drop_column("pi_session_stage")
        if "brief_revision" in columns:
            batch.drop_column("brief_revision")
        if "brief_json" in columns:
            batch.drop_column("brief_json")
