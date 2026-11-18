"""Add reliable Writing dispatch outbox.

Revision ID: 20260730_0011
Revises: 20260729_0010
"""

from alembic import op
import sqlalchemy as sa


revision = "20260731_0018"
down_revision = ("20260729_0010", "20260731_0017")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "writing_dispatch_outbox",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("operation_id", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_safe", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"], ["writing_operations.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_writing_dispatch_outbox_operation",
        "writing_dispatch_outbox",
        ["operation_id"],
    )
    op.create_index(
        "ix_writing_dispatch_outbox_ready",
        "writing_dispatch_outbox",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_writing_dispatch_outbox_ready",
        table_name="writing_dispatch_outbox",
    )
    op.drop_index(
        "ix_writing_dispatch_outbox_operation",
        table_name="writing_dispatch_outbox",
    )
    op.drop_table("writing_dispatch_outbox")
