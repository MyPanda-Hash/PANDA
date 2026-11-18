"""待办关联来源事件，事件消解时可自动关闭。

Revision ID: 20261113_0105
Revises: 20261113_0104
"""

from alembic import op
import sqlalchemy as sa

revision = "20261113_0105"
down_revision = "20261113_0104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "group_todos",
        sa.Column(
            "assistant_event_ids_json", sa.Text(), nullable=False, server_default="[]"
        ),
    )


def downgrade() -> None:
    op.drop_column("group_todos", "assistant_event_ids_json")
