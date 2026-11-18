"""员工职能卡片与项目经验。

Revision ID: 20261117_0109
Revises: 20261116_0108
"""

from alembic import op
import sqlalchemy as sa

revision = "20261117_0109"
down_revision = "20261116_0108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_profiles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chat_sessions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chat_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("group_messages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("todo_assigned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("todo_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events_acked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actions_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_response_hours", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("strengths", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("risks", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("focus_areas", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("capability_tags", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("activity_digest", sa.Text(), nullable=False, server_default=""),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_by", sa.String(16), nullable=False, server_default="rules"),
        sa.UniqueConstraint("user_id", "period_start", name="uq_employee_profiles_user_period"),
    )
    op.create_index("ix_employee_profiles_user_id", "employee_profiles", ["user_id"])
    op.create_index("ix_employee_profiles_period", "employee_profiles", ["period_start"])

    op.create_table(
        "project_experiences",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("group_id", sa.String(64),
                  sa.ForeignKey("project_groups.id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolution", sa.Text(), nullable=False, server_default=""),
        sa.Column("lesson", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(16), nullable=False, server_default="group"),
        sa.Column("source_ref", sa.String(120), nullable=True),
        sa.Column("contributors", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source in ('group', 'todo', 'manual')",
                           name="ck_project_experiences_source"),
    )
    op.create_index("ix_project_experiences_group", "project_experiences", ["group_id"])


def downgrade() -> None:
    op.drop_table("project_experiences")
    op.drop_table("employee_profiles")
