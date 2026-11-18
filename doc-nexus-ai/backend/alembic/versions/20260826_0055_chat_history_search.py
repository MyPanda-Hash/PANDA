"""Persist personal chat search indexing jobs, embeddings, and usage counters.

Revision ID: 20260826_0055
Revises: 20260826_0054
"""

from alembic import op
import sqlalchemy as sa


revision = "20260826_0055"
down_revision = "20260826_0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "chat_history_embeddings" not in tables:
        op.create_table(
            "chat_history_embeddings",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("message_id", sa.String(length=64), nullable=False),
            sa.Column("owner_user_id", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=128), nullable=True),
            sa.Column("model", sa.String(length=160), nullable=True),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("embedding_json", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("message_id", name="uq_chat_history_embeddings_message"),
        )
        op.create_index(
            "ix_chat_history_embeddings_message_id",
            "chat_history_embeddings",
            ["message_id"],
        )
        op.create_index(
            "ix_chat_history_embeddings_owner_user_id",
            "chat_history_embeddings",
            ["owner_user_id"],
        )
        op.create_index(
            "ix_chat_history_embeddings_status",
            "chat_history_embeddings",
            ["status"],
        )
        op.create_index(
            "ix_chat_history_embeddings_owner_status",
            "chat_history_embeddings",
            ["owner_user_id", "status"],
        )

    if "chat_history_index_jobs" not in tables:
        op.create_table(
            "chat_history_index_jobs",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("message_id", sa.String(length=64), nullable=False),
            sa.Column("owner_user_id", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("lease_owner", sa.String(length=128), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("message_id", name="uq_chat_history_index_jobs_message"),
        )
        op.create_index(
            "ix_chat_history_index_jobs_message_id",
            "chat_history_index_jobs",
            ["message_id"],
        )
        op.create_index(
            "ix_chat_history_index_jobs_owner_user_id",
            "chat_history_index_jobs",
            ["owner_user_id"],
        )
        op.create_index(
            "ix_chat_history_index_jobs_status",
            "chat_history_index_jobs",
            ["status"],
        )
        op.create_index(
            "ix_chat_history_index_jobs_status_next",
            "chat_history_index_jobs",
            ["status", "next_attempt_at"],
        )
        op.create_index(
            "ix_chat_history_index_jobs_owner_created",
            "chat_history_index_jobs",
            ["owner_user_id", "created_at"],
        )

    if "chat_history_usage_events" not in tables:
        op.create_table(
            "chat_history_usage_events",
            sa.Column("id", sa.String(length=128), primary_key=True),
            sa.Column("owner_user_id", sa.String(length=64), nullable=False),
            sa.Column("message_id", sa.String(length=64), nullable=True),
            sa.Column("operation_kind", sa.String(length=32), nullable=False, server_default="search"),
            sa.Column("query_type", sa.String(length=32), nullable=False),
            sa.Column("query_hash", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("provider", sa.String(length=128), nullable=True),
            sa.Column("model", sa.String(length=160), nullable=True),
            sa.Column("price_version_id", sa.String(length=64), nullable=True),
            sa.Column("currency", sa.String(length=8), nullable=True),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("input_uncached_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost", sa.String(length=64), nullable=False, server_default="0"),
            sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("vector_used", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("retry_number", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="succeeded"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        )
        op.create_index(
            "ix_chat_history_usage_events_owner_user_id",
            "chat_history_usage_events",
            ["owner_user_id"],
        )
        op.create_index(
            "ix_chat_history_usage_events_owner_created",
            "chat_history_usage_events",
            ["owner_user_id", "created_at"],
        )
        op.create_index(
            "ix_chat_history_usage_events_message_id",
            "chat_history_usage_events",
            ["message_id"],
        )


def downgrade() -> None:
    # Search data is user-owned operational history; never destroy it implicitly.
    return
