"""订阅式盯单：某个单号、某个条件，一有变化就通知。

Revision ID: 20261121_0113
Revises: 20261120_0112
"""

from alembic import op
import sqlalchemy as sa

revision = "20261121_0113"
down_revision = "20261120_0112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doc_watches",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("doc_no", sa.String(64), nullable=False),
        sa.Column("panel_code", sa.String(64), nullable=False, server_default=""),
        # progress = 有环节推进；stalled = 出现卡点
        sa.Column("condition", sa.String(24), nullable=False, server_default="progress"),
        # 只盯某个环节（「到货就告诉我」→ target_keyword='到货'）
        sa.Column("target_keyword", sa.String(64), nullable=False, server_default=""),
        sa.Column("note", sa.String(255), nullable=False, server_default=""),
        # 建订阅时的基线快照，用来判断「变了没有」
        sa.Column("baseline_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_signal", sa.String(160), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "condition in ('progress', 'stalled')", name="ck_doc_watch_condition"
        ),
        sa.CheckConstraint(
            "status in ('active', 'done', 'cancelled')", name="ck_doc_watch_status"
        ),
    )
    op.create_index("ix_doc_watch_user_status", "doc_watches", ["user_id", "status"])
    op.create_index("ix_doc_watch_doc", "doc_watches", ["doc_no"])


def downgrade() -> None:
    op.drop_index("ix_doc_watch_doc", table_name="doc_watches")
    op.drop_index("ix_doc_watch_user_status", table_name="doc_watches")
    op.drop_table("doc_watches")
