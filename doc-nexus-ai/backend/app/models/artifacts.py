from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        CheckConstraint("status in ('ready', 'expired', 'regenerating')", name="ck_artifacts_status"),
        Index("ix_artifacts_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("writing_projects.id", ondelete="CASCADE"), index=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("writing_operations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    artifact_type: Mapped[str] = mapped_column(String(64), default="writing_export")
    status: Mapped[str] = mapped_column(String(32), default="ready")
    storage_key: Mapped[str] = mapped_column(Text)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_version_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    share_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    share_scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    share_space_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    share_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
