"""Add variant columns to chat_messages for regenerate / version switching.

variant_group_id groups all variants of a single logical message turn.
variant_index numbers the variants within a group (0 = original).
variant_active marks the currently selected variant.
"""

import sqlalchemy as sa

from alembic import op

revision = "20260916_0095"
down_revision = "20260915_0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("variant_group_id", sa.String(64), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column(
            "variant_index",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "chat_messages",
        sa.Column(
            "variant_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_index(
        "ix_chat_messages_variant_group",
        "chat_messages",
        ["variant_group_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_variant_group", table_name="chat_messages")
    op.drop_column("chat_messages", "variant_active")
    op.drop_column("chat_messages", "variant_index")
    op.drop_column("chat_messages", "variant_group_id")
