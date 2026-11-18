"""Persist Pi Agent session id on writing projects.

Revision ID: 20260802_0023
Revises: 20260802_0022
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260802_0023"
down_revision = "20260802_0022"
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
    if "pi_agent_session_id" not in _column_names("writing_projects"):
        with op.batch_alter_table("writing_projects") as batch:
            batch.add_column(
                sa.Column("pi_agent_session_id", sa.String(128), nullable=True)
            )


def downgrade() -> None:
    if "pi_agent_session_id" in _column_names("writing_projects"):
        with op.batch_alter_table("writing_projects") as batch:
            batch.drop_column("pi_agent_session_id")
