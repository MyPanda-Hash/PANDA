"""Make chat-history indexing claims recoverable after worker restarts."""

from alembic import context, op
import sqlalchemy as sa


revision = "20260826_0058"
down_revision = "20260826_0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    existing = {
        column["name"]
        for column in sa.inspect(bind).get_columns("chat_history_index_jobs")
    }
    if "lease_owner" not in existing:
        op.add_column(
            "chat_history_index_jobs",
            sa.Column("lease_owner", sa.String(length=128), nullable=True),
        )
    if "lease_expires_at" not in existing:
        op.add_column(
            "chat_history_index_jobs",
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    indexes = {
        item["name"] for item in sa.inspect(bind).get_indexes("chat_history_index_jobs")
    }
    if "ix_chat_history_index_jobs_lease" not in indexes:
        op.create_index(
            "ix_chat_history_index_jobs_lease",
            "chat_history_index_jobs",
            ["status", "lease_expires_at"],
        )


def downgrade() -> None:
    return
