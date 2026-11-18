"""Store interleaved thinking/content parts on chat messages.

Revision ID: 20260802_0020
Revises: 20260731_0013r
Create Date: 2026-08-02
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260802_0020"
down_revision = "20260731_0013r"
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
    if "parts_json" not in _column_names("chat_messages"):
        op.add_column("chat_messages", sa.Column("parts_json", sa.Text(), nullable=True))


def downgrade() -> None:
    if "parts_json" in _column_names("chat_messages"):
        op.drop_column("chat_messages", "parts_json")
