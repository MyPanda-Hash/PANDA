"""Create identity, session, invitation and audit tables.

Revision ID: 20260727_0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "20260727_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("users", sa.Column("id", sa.String(64), primary_key=True), sa.Column("username", sa.String(128), nullable=False), sa.Column("email", sa.String(320), nullable=False), sa.Column("display_name", sa.String(120), nullable=False), sa.Column("password_hash", sa.Text(), nullable=False), sa.Column("role", sa.String(32), nullable=False, server_default="user"), sa.Column("status", sa.String(32), nullable=False, server_default="active"), sa.Column("first_login", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("locked_until", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table("invitations", sa.Column("id", sa.String(64), primary_key=True), sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("token_hash", sa.String(64), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("accepted_at", sa.DateTime(timezone=True)), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_invitations_user_id", "invitations", ["user_id"])
    op.create_index("ix_invitations_token_hash", "invitations", ["token_hash"], unique=True)
    op.create_table("sessions", sa.Column("id", sa.String(64), primary_key=True), sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("refresh_hash", sa.String(64), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_refresh_hash", "sessions", ["refresh_hash"], unique=True)
    op.create_table("audit_events", sa.Column("id", sa.String(64), primary_key=True), sa.Column("actor_user_id", sa.String(64)), sa.Column("event_type", sa.String(100), nullable=False), sa.Column("target_id", sa.String(64)), sa.Column("result", sa.String(32), nullable=False), sa.Column("request_id", sa.String(128)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor_user_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_sessions_refresh_hash", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("ix_invitations_token_hash", table_name="invitations")
    op.drop_index("ix_invitations_user_id", table_name="invitations")
    op.drop_table("invitations")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
