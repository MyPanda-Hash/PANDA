"""站内通知表：派活、催办、升级的微信式消息气泡。

Revision ID: 20261120_0112
Revises: 20261119_0111
"""

from alembic import op
import sqlalchemy as sa

revision = "20261120_0112"
down_revision = "20261119_0111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inapp_notifications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("ref_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("ref_id", sa.String(64), nullable=True),
        sa.Column("group_id", sa.String(64), nullable=True),
        sa.Column("actor_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("dedupe_key", sa.String(160), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind in ('todo_assigned', 'todo_nudged', 'todo_escalated', 'notice', 'system')",
            name="ck_inapp_kind",
        ),
    )
    op.create_index("ix_inapp_notifications_user_id", "inapp_notifications", ["user_id"])
    op.create_index("ix_inapp_notifications_dedupe_key", "inapp_notifications", ["dedupe_key"])
    op.create_index("ix_inapp_user_created", "inapp_notifications", ["user_id", "created_at"])
    op.create_index("ix_inapp_user_read", "inapp_notifications", ["user_id", "read_at"])


def downgrade() -> None:
    op.drop_index("ix_inapp_user_read", table_name="inapp_notifications")
    op.drop_index("ix_inapp_user_created", table_name="inapp_notifications")
    op.drop_index("ix_inapp_notifications_dedupe_key", table_name="inapp_notifications")
    op.drop_index("ix_inapp_notifications_user_id", table_name="inapp_notifications")
    op.drop_table("inapp_notifications")