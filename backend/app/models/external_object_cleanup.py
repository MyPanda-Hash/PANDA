from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class ExternalObjectCleanupOutbox(Base):
    """Durable tombstones for external objects whose owning row was removed."""

    __tablename__ = "external_object_cleanup_outbox"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'processing', 'succeeded')",
            name="ck_external_object_cleanup_status",
        ),
        UniqueConstraint("uri", name="uq_external_object_cleanup_uri"),
        UniqueConstraint("claim_token", name="uq_external_object_cleanup_claim"),
        Index(
            "ix_external_object_cleanup_ready",
            "status",
            "next_attempt_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    resource_kind: Mapped[str] = mapped_column(
        String(64), nullable=False, default="minio_object", server_default="minio_object"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending", index=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
