"""待办催办：记录催过几次、什么时候催的、什么时候升级过。

「状态回流」要能回答「这条待办卡住了吗、催过了吗」，所以状态本身沿用
``group_todos.status``（人改的），这里只补 AI 侧的跟进痕迹。

Revision ID: 20261119_0111
Revises: 20261118_0110
"""

from alembic import op
import sqlalchemy as sa

revision = "20261119_0111"
down_revision = "20261118_0110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "group_todos", sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("group_todos", sa.Column("nudged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "group_todos",
        sa.Column("nudge_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "group_todos", sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_group_todos_status_created", "group_todos", ["status", "created_at"])
    # 存量行：已经动过的（非 pending）把 updated_at 当作最后一次人工动作
    op.execute(
        "UPDATE group_todos SET last_action_at = updated_at WHERE status <> 'pending'"
    )


def downgrade() -> None:
    op.drop_index("ix_group_todos_status_created", table_name="group_todos")
    op.drop_column("group_todos", "escalated_at")
    op.drop_column("group_todos", "last_action_at")
    op.drop_column("group_todos", "nudge_count")
    op.drop_column("group_todos", "nudged_at")