from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, column, or_
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.auth import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "ix_documents_temporary_expires_at",
            "is_temporary",
            "expires_at",
            "temporary_cleanup_at",
        ),
        Index(
            "ix_documents_temporary_chat_session",
            "is_temporary",
            "chat_session_id",
        ),
        Index(
            "ix_documents_creator_status_updated",
            "created_by_user_id",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    space_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_spaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    category_id: Mapped[str | None] = mapped_column(ForeignKey("document_categories.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    is_temporary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", index=True)
    chat_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    temporary_cleanup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    versions: Mapped[list[DocumentVersion]] = relationship(back_populates="document", cascade="all, delete-orphan")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_versions_document_number"),
        UniqueConstraint("retry_of_version_id", name="uq_document_versions_retry_of"),
        CheckConstraint(or_(column("queryable", Boolean).is_(False), column("processing_stage", String) == "ready"), name="ck_document_versions_queryable_ready"),
        CheckConstraint(or_(column("is_current_published", Boolean).is_(False), column("processing_stage", String) == "ready"), name="ck_document_versions_published_ready"),
        CheckConstraint("extraction_method is null or extraction_method in ('native', 'ocr', 'vision', 'mixed')", name="ck_document_versions_extraction_method"),
        CheckConstraint(
            "page_count >= 0 and ocr_page_count >= 0 and vision_page_count >= 0",
            name="ck_document_versions_page_counts",
        ),
        Index(
            "uq_document_versions_current_published",
            "document_id",
            unique=True,
            sqlite_where=column("is_current_published", Boolean).is_(True),
            postgresql_where=column("is_current_published", Boolean).is_(True),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    processing_stage: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    queryable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_of_version_id: Mapped[str | None] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_relative_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    ext_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ocr_page_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    vision_page_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    vision_usage_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    extraction_warning_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_current_published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    document: Mapped[Document] = relationship(back_populates="versions")


class DocumentJob(Base):
    __tablename__ = "document_jobs"
    __table_args__ = (UniqueConstraint("version_id", name="uq_document_jobs_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), index=True)
    space_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_spaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64), default="document_processing")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
