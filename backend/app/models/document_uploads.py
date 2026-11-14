from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.auth import Base
from app.models.documents import Document, DocumentJob, DocumentVersion


class DocumentUploadSession(Base):
    """Durable state for one owner-scoped knowledge-document upload.

    The multipart object remains private in the configured temporary bucket until
    the dispatcher verifies its streamed checksum and promotes it server-side.
    ``version_id`` is the durable recovery fence used by document processing
    startup recovery to leave upload-owned work for this dispatcher.
    """

    __tablename__ = "document_upload_sessions"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "client_upload_id",
            name="uq_document_upload_sessions_owner_client",
        ),
        UniqueConstraint("version_id", name="uq_document_upload_sessions_version"),
        Index(
            "ix_document_upload_sessions_status_lease",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ix_document_upload_sessions_owner_created",
            "owner_user_id",
            "created_at",
        ),
        Index(
            "ix_document_upload_sessions_expires_status",
            "expires_at",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    space_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_spaces.id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    client_upload_id: Mapped[str] = mapped_column(String(128))
    filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_relative_path: Mapped[str] = mapped_column(Text)
    part_size: Mapped[int] = mapped_column(Integer)
    part_count: Mapped[int] = mapped_column(Integer)

    staging_bucket: Mapped[str | None] = mapped_column(String(128), nullable=True)
    staging_object_key: Mapped[str] = mapped_column(Text, default="", server_default="")
    multipart_upload_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    canonical_bucket: Mapped[str | None] = mapped_column(String(128), nullable=True)
    canonical_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), default="uploading", server_default="uploading", index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    version: Mapped[DocumentVersion | None] = relationship(
        foreign_keys=[version_id], lazy="joined"
    )
    document: Mapped[Document | None] = relationship(
        foreign_keys=[document_id], lazy="joined"
    )
    job: Mapped[DocumentJob | None] = relationship(foreign_keys=[job_id], lazy="joined")
