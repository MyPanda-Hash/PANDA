"""主动监控：认领备注 + 归档时间。

Revision ID: 20261115_0107
Revises: 20261114_0106
"""

from alembic import op
import sqlalchemy as sa

revision = "20261115_0107"
down_revision = "20261114_0106"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_events",
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "assistant_events",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assistant_events", "archived_at")
    op.drop_column("assistant_events", "note")
