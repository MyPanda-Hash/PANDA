"""Persist chat feedback and task acknowledgements.

Revision ID: 20260830_0064
Revises: 20260830_0063
"""

from alembic import context, op
import sqlalchemy as sa


revision = "20260830_0064"
down_revision = "20260830_0063"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    if context.is_offline_mode():
        return False
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("chat_message_feedback"):
        op.create_table(
            "chat_message_feedback",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "message_id",
                sa.String(length=64),
                sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "owner_user_id",
                sa.String(length=64),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("rating", sa.String(length=8), nullable=False),
            sa.Column("reason", sa.String(length=32), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "rating in ('up', 'down')",
                name="ck_chat_message_feedback_rating",
            ),
            sa.CheckConstraint(
                "reason is null or reason in ('inaccurate', 'unsupported', 'irrelevant', 'unsafe', 'other')",
                name="ck_chat_message_feedback_reason",
            ),
            sa.CheckConstraint(
                "rating = 'down' or reason is null",
                name="ck_chat_message_feedback_up_reason",
            ),
            sa.UniqueConstraint("message_id", name="uq_chat_message_feedback_message"),
        )
        op.create_index(
            "ix_chat_message_feedback_message_id",
            "chat_message_feedback",
            ["message_id"],
        )
        op.create_index(
            "ix_chat_message_feedback_owner_user_id",
            "chat_message_feedback",
            ["owner_user_id"],
        )
        op.create_index(
            "ix_chat_message_feedback_owner_updated",
            "chat_message_feedback",
            ["owner_user_id", "updated_at"],
        )

    if not _has_table("task_acknowledgements"):
        op.create_table(
            "task_acknowledgements",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "owner_user_id",
                sa.String(length=64),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("task_key", sa.String(length=192), nullable=False),
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "owner_user_id",
                "task_key",
                name="uq_task_acknowledgements_owner_task",
            ),
        )
        op.create_index(
            "ix_task_acknowledgements_owner_user_id",
            "task_acknowledgements",
            ["owner_user_id"],
        )
        op.create_index(
            "ix_task_acknowledgements_owner_acknowledged",
            "task_acknowledgements",
            ["owner_user_id", "acknowledged_at"],
        )


def downgrade() -> None:
    if _has_table("task_acknowledgements"):
        op.drop_index(
            "ix_task_acknowledgements_owner_acknowledged",
            table_name="task_acknowledgements",
        )
        op.drop_index(
            "ix_task_acknowledgements_owner_user_id",
            table_name="task_acknowledgements",
        )
        op.drop_table("task_acknowledgements")
    if _has_table("chat_message_feedback"):
        op.drop_index(
            "ix_chat_message_feedback_owner_updated",
            table_name="chat_message_feedback",
        )
        op.drop_index(
            "ix_chat_message_feedback_owner_user_id",
            table_name="chat_message_feedback",
        )
        op.drop_index(
            "ix_chat_message_feedback_message_id",
            table_name="chat_message_feedback",
        )
        op.drop_table("chat_message_feedback")
