from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base



class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    external_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class NotificationConnection(Base):
    __tablename__ = "notification_connections"
    __table_args__ = (
        CheckConstraint("kind in ('smtp', 'dingtalk_group', 'wecom_group')", name="ck_notification_connections_kind"),
        CheckConstraint("status in ('active', 'disabled')", name="ck_notification_connections_status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(24))
    config_json: Mapped[str] = mapped_column(Text)
    credential_ciphertext: Mapped[str] = mapped_column(Text)
    credential_key_version: Mapped[str] = mapped_column(String(64))
    credential_fingerprint: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationTarget(Base):
    __tablename__ = "notification_targets"
    __table_args__ = (
        CheckConstraint("kind in ('email', 'dingtalk_group', 'wecom_group')", name="ck_notification_targets_kind"),
        CheckConstraint("status in ('pending', 'active', 'disabled')", name="ck_notification_targets_status"),
        Index("ix_notification_targets_connection", "connection_id"),
        Index("ix_notification_targets_owner", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("notification_connections.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(24))
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    label: Mapped[str] = mapped_column(String(120))
    address_ciphertext: Mapped[str] = mapped_column(Text)
    address_mask: Mapped[str] = mapped_column(String(320))
    address_key_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationTargetGrant(Base):
    __tablename__ = "notification_target_grants"

    target_id: Mapped[str] = mapped_column(ForeignKey("notification_targets.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationVerificationChallenge(Base):
    __tablename__ = "notification_verification_challenges"
    __table_args__ = (Index("ix_notification_challenges_target", "target_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("notification_targets.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    delivery_status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "target_id", name="uq_notification_deliveries_event_target"),
        CheckConstraint("status in ('pending', 'sending', 'accepted', 'retry_wait', 'unknown', 'failed', 'suppressed')", name="ck_notification_deliveries_status"),
        Index("ix_notification_deliveries_queue", "status", "next_attempt_at", "lease_until"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("schedule_notifications.id", ondelete="CASCADE"))
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    target_id: Mapped[str] = mapped_column(ForeignKey("notification_targets.id", ondelete="CASCADE"))
    target_revision: Mapped[int] = mapped_column(Integer)
    target_label_snapshot: Mapped[str] = mapped_column(String(120))
    event_status_revision: Mapped[int] = mapped_column(Integer)
    content_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationDeliveryAttempt(Base):
    __tablename__ = "notification_delivery_attempts"
    __table_args__ = (
        UniqueConstraint("delivery_id", "attempt_no", name="uq_notification_delivery_attempts_number"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    delivery_id: Mapped[str] = mapped_column(ForeignKey("notification_deliveries.id", ondelete="CASCADE"), index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationOperation(Base):
    __tablename__ = "notification_operations"
    __table_args__ = (
        UniqueConstraint("actor_user_id", "action", "request_id", name="uq_notification_operations_actor_action_request"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(128))
    payload_digest: Mapped[str] = mapped_column(String(64))
    result_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
