"""主动监控：巡查运行记录。

Revision ID: 20261116_0108
Revises: 20261115_0107
"""

from alembic import op
import sqlalchemy as sa

revision = "20261116_0108"
down_revision = "20261115_0107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_watch_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trigger", sa.String(16), nullable=False, server_default="timer"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("panels_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events_evaluated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events_resolved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reminders_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_watch_runs_started", "assistant_watch_runs", ["started_at"])


def downgrade() -> None:
    op.drop_table("assistant_watch_runs")
