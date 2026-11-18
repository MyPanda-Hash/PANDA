"""Add durable chat deck render records."""

import sqlalchemy as sa

from alembic import op

revision = "20260922_0097"
down_revision = "20260921_0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_deck_renders",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("template_revision_id", sa.String(length=64), nullable=False),
        sa.Column("source_task_id", sa.String(length=160), nullable=False),
        sa.Column("source_file_path", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("active_session_key", sa.String(length=64), nullable=True),
        sa.Column("output_name", sa.String(length=255), nullable=True),
        sa.Column("output_mime", sa.String(length=255), nullable=True),
        sa.Column("output_size", sa.Integer(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("output_content", sa.LargeBinary(), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("blockers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("continuation_pages_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('running', 'ready', 'blocked', 'failed', 'cancelled', 'timed_out')",
            name="ck_chat_deck_renders_status",
        ),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["template_revision_id"],
            ["writing_doc_template_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "source_sha256",
            "template_revision_id",
            name="uq_chat_deck_renders_idempotency",
        ),
        sa.UniqueConstraint(
            "active_session_key", name="uq_chat_deck_renders_active_session"
        ),
    )
    op.create_index(
        "ix_chat_deck_renders_message_id", "chat_deck_renders", ["message_id"]
    )
    op.create_index(
        "ix_chat_deck_renders_owner_created",
        "chat_deck_renders",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_chat_deck_renders_owner_user_id",
        "chat_deck_renders",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_chat_deck_renders_session_created",
        "chat_deck_renders",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_chat_deck_renders_session_id", "chat_deck_renders", ["session_id"]
    )
    op.create_index(
        "ix_chat_deck_renders_template_revision_id",
        "chat_deck_renders",
        ["template_revision_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_deck_renders_template_revision_id", table_name="chat_deck_renders")
    op.drop_index("ix_chat_deck_renders_session_id", table_name="chat_deck_renders")
    op.drop_index("ix_chat_deck_renders_session_created", table_name="chat_deck_renders")
    op.drop_index("ix_chat_deck_renders_owner_user_id", table_name="chat_deck_renders")
    op.drop_index("ix_chat_deck_renders_owner_created", table_name="chat_deck_renders")
    op.drop_index("ix_chat_deck_renders_message_id", table_name="chat_deck_renders")
    op.drop_table("chat_deck_renders")
