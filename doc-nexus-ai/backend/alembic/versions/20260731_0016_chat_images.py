"""Add images_json to chat_messages.

Revision ID: 20260731_0016
Revises: 20260731_0015
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa


revision = "20260731_0016"
down_revision = "20260731_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("images_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "images_json")
