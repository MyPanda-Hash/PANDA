"""Repair writing_operations columns missing from stamped SQLite/Postgres DBs.

Revision ID: 20260802_0021
Revises: 20260802_0020
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260802_0021"
down_revision = "20260802_0020"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    if context.is_offline_mode():
        return set()
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_indexes(table_name)}


def upgrade() -> None:
    columns = _column_names("writing_operations")
    if not columns:
        return

    with op.batch_alter_table("writing_operations") as batch:
        if "idempotency_key" not in columns:
            batch.add_column(sa.Column("idempotency_key", sa.String(256), nullable=True))
        if "attempt" not in columns:
            batch.add_column(
                sa.Column("attempt", sa.Integer(), nullable=False, server_default="0")
            )
        if "input_revision" not in columns:
            batch.add_column(sa.Column("input_revision", sa.Integer(), nullable=True))
        if "error_code" not in columns:
            batch.add_column(sa.Column("error_code", sa.String(64), nullable=True))
        if "error_message_safe" not in columns:
            batch.add_column(
                sa.Column("error_message_safe", sa.String(500), nullable=True)
            )
        if "last_event_cursor" not in columns:
            batch.add_column(
                sa.Column(
                    "last_event_cursor",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )
        if "started_at" not in columns:
            batch.add_column(
                sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
            )
        if "input_payload" not in columns:
            batch.add_column(
                sa.Column(
                    "input_payload",
                    sa.Text(),
                    nullable=False,
                    server_default="{}",
                )
            )

    indexes = _index_names("writing_operations")
    # UniqueConstraint may appear as an index name depending on dialect.
    if "uq_writing_operation_idempotency" not in indexes:
        with op.batch_alter_table("writing_operations") as batch:
            batch.create_unique_constraint(
                "uq_writing_operation_idempotency",
                ["project_id", "idempotency_key"],
            )


def downgrade() -> None:
    # Repair revisions do not own historical columns.
    pass
