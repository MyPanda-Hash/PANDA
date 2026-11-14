"""项目群聊：群、成员、消息、附件、AI 分析记录、待办。

Revision ID: 20261111_0102
Revises: 20261003_0101
"""

from alembic import op
import sqlalchemy as sa


revision = "20261111_0102"
down_revision = "20261003_0101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_groups",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "owner_user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("mes_project_code", sa.String(64), nullable=True),
        sa.Column(
            "knowledge_space_id",
            sa.String(64),
            sa.ForeignKey("knowledge_spaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('active', 'archived')", name="ck_project_groups_status"
        ),
    )
    op.create_index("ix_project_groups_owner_user_id", "project_groups", ["owner_user_id"])
    op.create_index(
        "ix_project_groups_owner_status", "project_groups", ["owner_user_id", "status"]
    )
    op.create_index(
        "ix_project_groups_mes_project_code", "project_groups", ["mes_project_code"]
    )

    op.create_table(
        "project_group_members",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "group_id",
            sa.String(64),
            sa.ForeignKey("project_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("invited_by_user_id", sa.String(64), nullable=True),
        sa.Column("last_read_message_id", sa.String(64), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),
        sa.CheckConstraint(
            "role in ('owner', 'manager', 'member')", name="ck_group_members_role"
        ),
    )
    op.create_index("ix_project_group_members_group_id", "project_group_members", ["group_id"])
    op.create_index("ix_group_members_user", "project_group_members", ["user_id"])

    op.create_table(
        "project_group_messages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "group_id",
            sa.String(64),
            sa.ForeignKey("project_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sender_kind", sa.String(16), nullable=False, server_default="user"),
        sa.Column("kind", sa.String(16), nullable=False, server_default="text"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("parts_json", sa.Text(), nullable=True),
        sa.Column("attachments_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("reply_to_message_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind in ('text', 'image', 'file', 'voice', 'ai', 'system')",
            name="ck_group_messages_kind",
        ),
        sa.CheckConstraint(
            "sender_kind in ('user', 'ai', 'system')",
            name="ck_group_messages_sender_kind",
        ),
    )
    op.create_index("ix_project_group_messages_group_id", "project_group_messages", ["group_id"])
    op.create_index(
        "ix_group_messages_group_created", "project_group_messages", ["group_id", "created_at"]
    )
    op.create_index(
        "ix_project_group_messages_sender_user_id", "project_group_messages", ["sender_user_id"]
    )

    op.create_table(
        "project_group_attachments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "group_id",
            sa.String(64),
            sa.ForeignKey("project_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            sa.String(64),
            sa.ForeignKey("project_group_messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "uploader_user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False, server_default="file"),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column(
            "extract_status", sa.String(16), nullable=False, server_default="pending"
        ),
        sa.Column("extract_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind in ('image', 'file', 'voice')", name="ck_group_attachments_kind"
        ),
        sa.CheckConstraint(
            "extract_status in ('pending', 'done', 'failed', 'skipped')",
            name="ck_group_attachments_extract_status",
        ),
    )
    op.create_index(
        "ix_project_group_attachments_group_id", "project_group_attachments", ["group_id"]
    )
    op.create_index(
        "ix_project_group_attachments_message_id", "project_group_attachments", ["message_id"]
    )
    op.create_index(
        "ix_group_attachments_group_created", "project_group_attachments", ["group_id", "created_at"]
    )
    op.create_index("ix_project_group_attachments_sha256", "project_group_attachments", ["sha256"])

    op.create_table(
        "project_group_ai_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "group_id",
            sa.String(64),
            sa.ForeignKey("project_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("model", sa.String(160), nullable=True),
        sa.Column("input_refs_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("output_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("todos_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "trigger in ('message', 'attachment', 'manual')", name="ck_group_ai_runs_trigger"
        ),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'done', 'failed')",
            name="ck_group_ai_runs_status",
        ),
    )
    op.create_index("ix_project_group_ai_runs_group_id", "project_group_ai_runs", ["group_id"])
    op.create_index(
        "ix_group_ai_runs_group_created", "project_group_ai_runs", ["group_id", "created_at"]
    )

    op.create_table(
        "group_todos",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "group_id",
            sa.String(64),
            sa.ForeignKey("project_groups.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "assignee_user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("origin", sa.String(16), nullable=False, server_default="ai"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_message_id", sa.String(64), nullable=True),
        sa.Column("source_attachment_id", sa.String(64), nullable=True),
        sa.Column(
            "ai_run_id",
            sa.String(64),
            sa.ForeignKey("project_group_ai_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('pending', 'accepted', 'doing', 'done', 'dismissed')",
            name="ck_group_todos_status",
        ),
        sa.CheckConstraint("origin in ('ai', 'human')", name="ck_group_todos_origin"),
        sa.CheckConstraint(
            "priority in ('low', 'normal', 'high', 'urgent')",
            name="ck_group_todos_priority",
        ),
    )
    op.create_index("ix_group_todos_group_id", "group_todos", ["group_id"])
    op.create_index("ix_group_todos_assignee_user_id", "group_todos", ["assignee_user_id"])
    op.create_index(
        "ix_group_todos_assignee_status", "group_todos", ["assignee_user_id", "status"]
    )
    op.create_index("ix_group_todos_group_status", "group_todos", ["group_id", "status"])


def downgrade() -> None:
    op.drop_table("group_todos")
    op.drop_table("project_group_ai_runs")
    op.drop_table("project_group_attachments")
    op.drop_table("project_group_messages")
    op.drop_table("project_group_members")
    op.drop_table("project_groups")
