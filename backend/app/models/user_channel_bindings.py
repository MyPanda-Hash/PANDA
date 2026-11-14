"""Verified external identities and short-lived, single-use binding challenges."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class UserChannelBinding(Base):
    __tablename__ = "user_channel_bindings"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "channel_kind",
            "verified_channel_key",
            "external_user_id",
            name="uq_user_channel_bindings_external",
        ),
        Index("ix_user_channel_bindings_owner", "owner_user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    verified_channel_key: Mapped[str] = mapped_column(String(64), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserChannelBindingChallenge(Base):
    __tablename__ = "user_channel_binding_challenges"
    __table_args__ = (
        Index(
            "ix_user_channel_binding_challenges_owner", "owner_user_id", "created_at"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    channel_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    verified_channel_key: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class UserChannelBindingRateLimit(Base):
    """A bounded attempt window per verified channel/sender, never per raw code."""

    __tablename__ = "user_channel_binding_rate_limits"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
