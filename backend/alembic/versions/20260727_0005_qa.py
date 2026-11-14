"""Create owner-scoped immutable Q&A sessions, turns, and citations.

Revision ID: 20260727_0005
Revises: 20260727_0004
"""

from alembic import op
import sqlalchemy as sa


revision = "20260727_0005"
down_revision = "20260727_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("selected_space_ids", sa.Text(), nullable=False),
        sa.Column("scope_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qa_sessions_owner_user_id", "qa_sessions", ["owner_user_id"])
    op.create_index("ix_qa_sessions_owner_created", "qa_sessions", ["owner_user_id", "created_at"])
    op.create_table(
        "qa_turns",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("selected_space_ids", sa.Text(), nullable=False),
        sa.Column("scope_version", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("retrieval_summary", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("verification_summary", sa.Text(), nullable=False),
        sa.Column("usage", sa.Text(), nullable=False),
        sa.Column("events", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('completed', 'knowledge_gap', 'budget_exceeded', 'source_permission_denied')", name="ck_qa_turns_terminal_status"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["qa_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qa_turns_session_id", "qa_turns", ["session_id"])
    op.create_index("ix_qa_turns_owner_user_id", "qa_turns", ["owner_user_id"])
    op.create_index("ix_qa_turns_status", "qa_turns", ["status"])
    op.create_index("ix_qa_turns_session_created", "qa_turns", ["session_id", "created_at"])
    op.create_index("ix_qa_turns_owner_created", "qa_turns", ["owner_user_id", "created_at"])
    op.create_table(
        "citations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("document_version_id", sa.String(length=64), nullable=False),
        sa.Column("page_or_section", sa.String(length=255), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("support_state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("support_state in ('supported', 'uncertain', 'unsupported')", name="ck_citations_support_state"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["qa_turns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_citations_turn_id", "citations", ["turn_id"])
    op.create_index("ix_citations_document_id", "citations", ["document_id"])
    op.create_index("ix_citations_document_version_id", "citations", ["document_version_id"])
    op.create_index("ix_citations_document_version", "citations", ["document_id", "document_version_id"])


def downgrade() -> None:
    op.drop_index("ix_citations_document_version", table_name="citations")
    op.drop_index("ix_citations_document_version_id", table_name="citations")
    op.drop_index("ix_citations_document_id", table_name="citations")
    op.drop_index("ix_citations_turn_id", table_name="citations")
    op.drop_table("citations")
    op.drop_index("ix_qa_turns_owner_created", table_name="qa_turns")
    op.drop_index("ix_qa_turns_session_created", table_name="qa_turns")
    op.drop_index("ix_qa_turns_status", table_name="qa_turns")
    op.drop_index("ix_qa_turns_owner_user_id", table_name="qa_turns")
    op.drop_index("ix_qa_turns_session_id", table_name="qa_turns")
    op.drop_table("qa_turns")
    op.drop_index("ix_qa_sessions_owner_created", table_name="qa_sessions")
    op.drop_index("ix_qa_sessions_owner_user_id", table_name="qa_sessions")
    op.drop_table("qa_sessions")
