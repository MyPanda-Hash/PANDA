"""项目经验：证据字段（topic / status / reusable / 适用范围 / 来源数）。

借鉴 allexyuan/skill-cairn 的证据约束晋升模型。

Revision ID: 20261118_0110
Revises: 20261117_0109
"""

from alembic import op
import sqlalchemy as sa

revision = "20261118_0110"
down_revision = "20261117_0109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_experiences",
        sa.Column("topic", sa.String(120), nullable=False, server_default=""),
    )
    op.add_column(
        "project_experiences",
        sa.Column("status", sa.String(16), nullable=False, server_default="unverified"),
    )
    op.add_column(
        "project_experiences",
        sa.Column("reusable", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "project_experiences",
        sa.Column("not_applicable", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "project_experiences",
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_project_experiences_topic", "project_experiences", ["topic"])
    # 存量行补 topic（用标题压缩，与 slugify 同思路）
    op.execute(
        "UPDATE project_experiences SET topic = lower(replace(title, ' ', '-')) "
        "WHERE topic = ''"
    )


def downgrade() -> None:
    op.drop_index("ix_project_experiences_topic", table_name="project_experiences")
    op.drop_column("project_experiences", "source_count")
    op.drop_column("project_experiences", "not_applicable")
    op.drop_column("project_experiences", "reusable")
    op.drop_column("project_experiences", "status")
    op.drop_column("project_experiences", "topic")
