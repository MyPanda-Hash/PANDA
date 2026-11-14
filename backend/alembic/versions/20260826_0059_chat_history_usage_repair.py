"""Repair chat history usage columns missing from stamped SQLite/Postgres DBs.

Revision ID: 20260826_0059
Revises: 20260826_0058
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260826_0059"
down_revision = "20260826_0058"
branch_labels = None
depends_on = None


_COLUMNS = (
    ("message_id", sa.String(length=64), True, None),
    ("operation_kind", sa.String(length=32), False, "search"),
    ("provider", sa.String(length=128), True, None),
    ("model", sa.String(length=160), True, None),
    ("price_version_id", sa.String(length=64), True, None),
    ("currency", sa.String(length=8), True, None),
    ("input_tokens", sa.Integer(), False, "0"),
    ("input_uncached_tokens", sa.Integer(), False, "0"),
    ("cache_read_tokens", sa.Integer(), False, "0"),
    ("cache_write_tokens", sa.Integer(), False, "0"),
    ("output_tokens", sa.Integer(), False, "0"),
    ("total_tokens", sa.Integer(), False, "0"),
    ("cost", sa.String(length=64), False, "0"),
    ("retry_number", sa.Integer(), False, "0"),
    ("status", sa.String(length=32), False, "succeeded"),
)


def upgrade() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    if "chat_history_usage_events" not in sa.inspect(bind).get_table_names():
        return
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("chat_history_usage_events")
    }
    for name, type_, nullable, default in _COLUMNS:
        if name in existing:
            continue
        op.add_column(
            "chat_history_usage_events",
            sa.Column(name, type_, nullable=nullable, server_default=default),
        )
    indexes = {
        item["name"]
        for item in sa.inspect(bind).get_indexes("chat_history_usage_events")
    }
    if "ix_chat_history_usage_events_message_id" not in indexes:
        op.create_index(
            "ix_chat_history_usage_events_message_id",
            "chat_history_usage_events",
            ["message_id"],
        )


def downgrade() -> None:
    return
