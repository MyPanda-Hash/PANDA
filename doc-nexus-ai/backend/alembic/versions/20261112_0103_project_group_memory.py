"""项目群聊的长期记忆表。

Revision ID: 20261112_0103
Revises: 20261111_0102
"""

from alembic import op
import sqlalchemy as sa


revision = "20261112_0103"
down_revision = "20261111_0102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_group_memories",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "group_id",
            sa.String(64),
            sa.ForeignKey("project_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("covered_message_id", sa.String(64), nullable=True),
        sa.Column(
            "covered_message_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", name="uq_group_memories_group"),
    )
    op.create_index(
        "ix_project_group_memories_group_id", "project_group_memories", ["group_id"]
    )


def downgrade() -> None:
    op.drop_table("project_group_memories")
