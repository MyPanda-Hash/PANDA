"""Add Doc Nexus Writing Agent Platform bridge.

Revision ID: 20260729_0010
Revises: 20260727_0009
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_0010"
down_revision = "20260727_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("writing_operations") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(256), nullable=True))
        batch.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("input_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(64), nullable=True))
        batch.add_column(sa.Column("error_message_safe", sa.String(500), nullable=True))
        batch.add_column(sa.Column("last_event_cursor", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint(
            "uq_writing_operation_idempotency",
            ["project_id", "idempotency_key"],
        )
        batch.drop_constraint("ck_writing_operations_status", type_="check")
        batch.create_check_constraint(
            "ck_writing_operations_status",
            "status in ('queued','dispatching','running','completed','failed',"
            "'cancelling','cancelled','stopped')",
        )

    op.create_table(
        "agent_run_bindings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("writing_operation_id", sa.String(64), nullable=False, unique=True),
        sa.Column("platform_run_id", sa.String(96), nullable=False, unique=True),
        sa.Column("team_id", sa.String(128), nullable=False),
        sa.Column("team_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_event_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manifest_sha256", sa.String(128), nullable=True),
        sa.Column("usage_summary", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["writing_operation_id"], ["writing_operations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_agent_run_bindings_operation", "agent_run_bindings", ["writing_operation_id"])
    op.create_index("ix_agent_run_bindings_platform", "agent_run_bindings", ["platform_run_id"])

    op.create_table(
        "agent_event_receipts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("platform_run_id", sa.String(96), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("platform_run_id", "event_id", name="uq_agent_event_receipt"),
    )
    op.create_index("ix_agent_event_receipts_platform", "agent_event_receipts", ["platform_run_id"])

    op.create_table(
        "writing_tool_receipts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("platform_run_id", sa.String(96), nullable=False),
        sa.Column("tool_call_id", sa.String(128), nullable=False),
        sa.Column("action_key", sa.String(256), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result_ref", sa.String(128), nullable=True),
        sa.Column("result_sha256", sa.String(64), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("platform_run_id", "action_key", name="uq_writing_tool_action"),
    )
    op.create_index("ix_writing_tool_receipts_platform", "writing_tool_receipts", ["platform_run_id"])

    op.create_table(
        "writing_candidates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("writing_operation_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("chapter_id", sa.String(64), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("source_platform_run_id", sa.String(96), nullable=False),
        sa.Column("source_tool_call_id", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["writing_operation_id"], ["writing_operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["writing_projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("writing_operation_id", "kind", "chapter_id", "candidate_revision", name="uq_writing_candidate_revision"),
    )
    op.create_index("ix_writing_candidates_operation", "writing_candidates", ["writing_operation_id"])
    op.create_index("ix_writing_candidates_project", "writing_candidates", ["project_id"])


def downgrade() -> None:
    op.drop_table("writing_candidates")
    op.drop_table("writing_tool_receipts")
    op.drop_table("agent_event_receipts")
    op.drop_table("agent_run_bindings")
    with op.batch_alter_table("writing_operations") as batch:
        batch.drop_constraint("ck_writing_operations_status", type_="check")
        batch.create_check_constraint(
            "ck_writing_operations_status",
            "status in ('queued','running','completed','failed','stopped')",
        )
        batch.drop_constraint(
            "uq_writing_operation_idempotency", type_="unique"
        )
        for name in (
            "started_at", "last_event_cursor", "error_message_safe", "error_code",
            "input_revision", "attempt", "idempotency_key",
        ):
            batch.drop_column(name)
