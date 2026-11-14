"""user_channel_bindings

Revision ID: 68cf522fd227
Revises: 20260909_0088
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '68cf522fd227'
down_revision: Union[str, Sequence[str], None] = '20260909_0088'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "user_channel_bindings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("channel_kind", sa.String(16), nullable=False),
        sa.Column("verified_channel_key", sa.String(64), nullable=False),
        sa.Column("external_user_id", sa.String(255), nullable=False),
        sa.Column("owner_user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "provider", "channel_kind", "verified_channel_key", "external_user_id",
            name="uq_user_channel_bindings_external",
        ),
    )
    op.create_index("ix_user_channel_bindings_owner", "user_channel_bindings", ["owner_user_id", "status"])
    op.create_table(
        "user_channel_binding_challenges",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_kind", sa.String(16), nullable=False),
        sa.Column("verified_channel_key", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_user_channel_binding_challenges_owner",
        "user_channel_binding_challenges", ["owner_user_id", "created_at"],
    )
    op.create_table(
        "user_channel_binding_rate_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )

def downgrade() -> None:
    op.drop_table("user_channel_binding_rate_limits")
    op.drop_table("user_channel_binding_challenges")
    op.drop_table("user_channel_bindings")
