"""主动监控：提醒次数、升级标记、首次发现时间。

Revision ID: 20261114_0106
Revises: 20261113_0105
"""

from alembic import op
import sqlalchemy as sa

revision = "20261114_0106"
down_revision = "20261113_0105"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_events",
        sa.Column("reminders", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "assistant_events",
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assistant_events",
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assistant_events",
        sa.Column(
            "original_severity", sa.String(16), nullable=False, server_default="info"
        ),
    )
    # 存量行补首次发现时间，避免升级判定为 0
    op.execute("UPDATE assistant_events SET first_seen_at = created_at WHERE first_seen_at IS NULL")


def downgrade() -> None:
    op.drop_column("assistant_events", "original_severity")
    op.drop_column("assistant_events", "first_seen_at")
    op.drop_column("assistant_events", "last_notified_at")
    op.drop_column("assistant_events", "reminders")
