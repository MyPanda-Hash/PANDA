"""Add shared chat session snapshots.

Revision ID: 20260806_0029
Revises: 20260804_0028
"""

import sqlalchemy as sa
from alembic import op

revision = "20260806_0029"
down_revision = "20260804_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_session_publications",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source_session_id", sa.String(length=64), nullable=False),
        sa.Column("source_owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("shared_by_user_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "share_scope",
            sa.String(length=32),
            nullable=False,
            server_default="all_internal",
        ),
        sa.Column("allow_cite", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "allow_remix", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_session_id"], ["chat_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_owner_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shared_by_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_publications_status_published",
        "chat_session_publications",
        ["status", "published_at"],
    )
    op.create_index(
        "ix_chat_publications_source_session",
        "chat_session_publications",
        ["source_session_id", "version"],
    )
    op.create_index(
        "ix_chat_session_publications_source_session_id",
        "chat_session_publications",
        ["source_session_id"],
    )
    op.create_index(
        "ix_chat_session_publications_source_owner_user_id",
        "chat_session_publications",
        ["source_owner_user_id"],
    )
    op.create_index(
        "ix_chat_session_publications_shared_by_user_id",
        "chat_session_publications",
        ["shared_by_user_id"],
    )
    op.create_index(
        "ix_chat_session_publications_status",
        "chat_session_publications",
        ["status"],
    )

    with op.batch_alter_table("chat_messages") as batch:
        batch.add_column(
            sa.Column(
                "session_references_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_messages") as batch:
        batch.drop_column("session_references_json")
    op.drop_index(
        "ix_chat_session_publications_status", table_name="chat_session_publications"
    )
    op.drop_index(
        "ix_chat_session_publications_shared_by_user_id",
        table_name="chat_session_publications",
    )
    op.drop_index(
        "ix_chat_session_publications_source_owner_user_id",
        table_name="chat_session_publications",
    )
    op.drop_index(
        "ix_chat_session_publications_source_session_id",
        table_name="chat_session_publications",
    )
    op.drop_index(
        "ix_chat_publications_source_session", table_name="chat_session_publications"
    )
    op.drop_index(
        "ix_chat_publications_status_published", table_name="chat_session_publications"
    )
    op.drop_table("chat_session_publications")
