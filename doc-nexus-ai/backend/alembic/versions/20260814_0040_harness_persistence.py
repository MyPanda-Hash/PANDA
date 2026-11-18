"""Create Harness persistence tables.

Revision ID: 20260814_0040
Revises: 20260813_0039
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260814_0040"
down_revision = "20260813_0039"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    if context.is_offline_mode():
        return False
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # --- harness_tasks ---
    if not _has_table("harness_tasks"):
        op.create_table(
            "harness_tasks",
            sa.Column("id", sa.String(128), nullable=False),
            sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
            sa.Column("user_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("project_id", sa.String(128), nullable=False, server_default="default"),
            sa.Column("objective", sa.Text(), nullable=False, server_default=""),
            sa.Column("permissions_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("max_steps", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="500000"),
            sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="1800"),
            sa.Column("max_cost", sa.String(32), nullable=True),
            sa.Column("verification_build_cmd", sa.Text(), nullable=True),
            sa.Column("verification_test_cmd", sa.Text(), nullable=True),
            sa.Column("verification_lint_cmd", sa.Text(), nullable=True),
            sa.Column("verification_max_retries", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("workspace_path", sa.Text(), nullable=False, server_default=""),
            sa.Column("repository", sa.Text(), nullable=True),
            sa.Column("branch", sa.String(256), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="created"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_harness_tasks_tenant_id", "harness_tasks", ["tenant_id"])
        op.create_index("ix_harness_tasks_user_id", "harness_tasks", ["user_id"])
        op.create_index("ix_harness_tasks_status", "harness_tasks", ["status"])

    # --- harness_audit_events ---
    if not _has_table("harness_audit_events"):
        op.create_table(
            "harness_audit_events",
            sa.Column("id", sa.String(64), nullable=False),
            sa.Column("task_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("actor_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_harness_audit_events_task_id", "harness_audit_events", ["task_id"])
        op.create_index("ix_harness_audit_events_event_type", "harness_audit_events", ["event_type"])
        op.create_index("ix_harness_audit_events_actor_id", "harness_audit_events", ["actor_id"])
        op.create_index("ix_harness_audit_events_created_at", "harness_audit_events", ["created_at"])

    # --- harness_approvals ---
    if not _has_table("harness_approvals"):
        op.create_table(
            "harness_approvals",
            sa.Column("id", sa.String(64), nullable=False),
            sa.Column("task_id", sa.String(128), nullable=False),
            sa.Column("tool_name", sa.String(64), nullable=False),
            sa.Column("arguments_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("rule_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("requester_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("approver_id", sa.String(64), nullable=True),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("ttl_seconds", sa.Float(), nullable=False, server_default="600.0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_harness_approvals_task_id", "harness_approvals", ["task_id"])
        op.create_index("ix_harness_approvals_status", "harness_approvals", ["status"])

    # --- harness_policy_rules ---
    if not _has_table("harness_policy_rules"):
        op.create_table(
            "harness_policy_rules",
            sa.Column("id", sa.String(64), nullable=False),
            sa.Column("tool_pattern", sa.String(128), nullable=False),
            sa.Column("arg_pattern", sa.Text(), nullable=False, server_default=""),
            sa.Column("decision", sa.String(16), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    op.drop_table("harness_policy_rules")
    op.drop_table("harness_approvals")
    op.drop_table("harness_audit_events")
    op.drop_table("harness_tasks")
