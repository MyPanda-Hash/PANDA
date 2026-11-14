"""Add opt-in external schedule notification delivery."""

import sqlalchemy as sa
from alembic import op

revision = "20260925_0099"
down_revision = "20260924_0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("external_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "notification_connections",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("credential_ciphertext", sa.Text(), nullable=False),
        sa.Column("credential_key_version", sa.String(64), nullable=False),
        sa.Column("credential_fingerprint", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind in ('smtp', 'dingtalk_group', 'wecom_group')", name="ck_notification_connections_kind"),
        sa.CheckConstraint("status in ('active', 'disabled')", name="ck_notification_connections_status"),
    )
    op.create_table(
        "notification_targets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("connection_id", sa.String(64), sa.ForeignKey("notification_connections.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("owner_user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("address_ciphertext", sa.Text(), nullable=False),
        sa.Column("address_mask", sa.String(320), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("address_key_version", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind in ('email', 'dingtalk_group', 'wecom_group')", name="ck_notification_targets_kind"),
        sa.CheckConstraint("status in ('pending', 'active', 'disabled')", name="ck_notification_targets_status"),
    )
    op.create_index("ix_notification_targets_connection", "notification_targets", ["connection_id"])
    op.create_index("ix_notification_targets_owner", "notification_targets", ["owner_user_id"])
    op.create_table(
        "notification_target_grants",
        sa.Column("target_id", sa.String(64), sa.ForeignKey("notification_targets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "notification_verification_challenges",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("target_id", sa.String(64), sa.ForeignKey("notification_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notification_challenges_target", "notification_verification_challenges", ["target_id"])
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(64), sa.ForeignKey("schedule_notifications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", sa.String(64), sa.ForeignKey("notification_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("target_label_snapshot", sa.String(120), nullable=False),
        sa.Column("event_status_revision", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(96), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "target_id", name="uq_notification_deliveries_event_target"),
        sa.CheckConstraint("status in ('pending', 'sending', 'accepted', 'retry_wait', 'unknown', 'failed', 'suppressed')", name="ck_notification_deliveries_status"),
    )
    op.create_index("ix_notification_deliveries_queue", "notification_deliveries", ["status", "next_attempt_at", "lease_until"])
    op.create_table(
        "notification_delivery_attempts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("delivery_id", sa.String(64), sa.ForeignKey("notification_deliveries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(96), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("delivery_id", "attempt_no", name="uq_notification_delivery_attempts_number"),
    )
    op.create_index("ix_notification_delivery_attempts_delivery_id", "notification_delivery_attempts", ["delivery_id"])
    op.create_table(
        "notification_operations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor_user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("result_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_user_id", "action", "request_id", name="uq_notification_operations_actor_action_request"),
    )


def downgrade() -> None:
    op.drop_table("notification_operations")
    op.drop_index("ix_notification_delivery_attempts_delivery_id", table_name="notification_delivery_attempts")
    op.drop_table("notification_delivery_attempts")
    op.drop_index("ix_notification_deliveries_queue", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_index("ix_notification_challenges_target", table_name="notification_verification_challenges")
    op.drop_table("notification_verification_challenges")
    op.drop_table("notification_target_grants")
    op.drop_index("ix_notification_targets_owner", table_name="notification_targets")
    op.drop_index("ix_notification_targets_connection", table_name="notification_targets")
    op.drop_table("notification_targets")
    op.drop_table("notification_connections")
    op.drop_table("notification_preferences")
