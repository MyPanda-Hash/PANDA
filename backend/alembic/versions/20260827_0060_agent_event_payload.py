"""Persist streamed Pi writing events in agent_event_receipts.

Revision ID: 20260827_0060
Revises: 20260826_0059
"""

from alembic import op
import sqlalchemy as sa


revision = "20260827_0060"
down_revision = "20260826_0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_event_receipts") as batch:
        batch.add_column(
            sa.Column(
                "payload_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_event_receipts") as batch:
        batch.drop_column("payload_json")
