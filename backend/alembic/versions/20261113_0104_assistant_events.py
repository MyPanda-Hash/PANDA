"""AI 助手主动监控 MES 产生的事件。

Revision ID: 20261113_0104
Revises: 20261112_0103
"""

from alembic import op
import sqlalchemy as sa

revision = "20261113_0104"
down_revision = "20261112_0103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dedupe_key", sa.String(200), nullable=False, unique=True),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="mes"),
        sa.Column("panel_code", sa.String(64), nullable=True),
        sa.Column("doc_no", sa.String(120), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("suggested_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("target_usernames_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("target_user_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("notifications", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "acked_by_user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status in ('open', 'handled', 'resolved')", name="ck_assistant_events_status"
        ),
        sa.CheckConstraint(
            "severity in ('info', 'warning', 'critical')",
            name="ck_assistant_events_severity",
        ),
    )
    op.create_index("ix_assistant_events_dedupe_key", "assistant_events", ["dedupe_key"])
    op.create_index("ix_assistant_events_kind", "assistant_events", ["kind"])
    op.create_index(
        "ix_assistant_events_status_created", "assistant_events", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("assistant_events")
