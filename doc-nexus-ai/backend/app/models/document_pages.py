from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.auth import Base


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("document_version_id", "page_number", name="uq_document_pages_version_number"),
        Index("ix_document_pages_version", "document_version_id", "page_number"),
        CheckConstraint("page_number > 0", name="ck_document_pages_page_number"),
        CheckConstraint("width_points > 0 AND height_points > 0", name="ck_document_pages_dimensions"),
        CheckConstraint("rotation in (0, 90, 180, 270)", name="ck_document_pages_rotation"),
        CheckConstraint("quality_score >= 0 AND quality_score <= 1", name="ck_document_pages_quality"),
        CheckConstraint("average_confidence IS NULL OR (average_confidence >= 0 AND average_confidence <= 1)", name="ck_document_pages_confidence"),
        CheckConstraint("extraction_method in ('native', 'ocr', 'vision')", name="ck_document_pages_method"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    width_points: Mapped[float] = mapped_column()
    height_points: Mapped[float] = mapped_column()
    rotation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    extraction_method: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    quality_score: Mapped[float] = mapped_column(default=0.0, server_default="0")
    average_confidence: Mapped[float | None] = mapped_column(nullable=True)
    warning_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    blocks: Mapped[list[DocumentPageBlock]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="DocumentPageBlock.block_index"
    )


class DocumentPageBlock(Base):
    __tablename__ = "document_page_blocks"
    __table_args__ = (
        UniqueConstraint("page_id", "block_index", name="uq_document_page_blocks_page_index"),
        Index("ix_document_page_blocks_page", "page_id", "block_index"),
        CheckConstraint("block_index >= 0", name="ck_document_page_blocks_index"),
        CheckConstraint("source in ('native', 'ocr', 'vision')", name="ck_document_page_blocks_source"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_document_page_blocks_confidence"),
        CheckConstraint("char_start >= 0 AND char_end >= char_start", name="ck_document_page_blocks_span"),
        CheckConstraint(
            "source in ('native', 'vision') OR (bbox_x0 IS NOT NULL AND bbox_y0 IS NOT NULL AND bbox_x1 IS NOT NULL AND bbox_y1 IS NOT NULL)",
            name="ck_document_page_blocks_ocr_bbox",
        ),
        CheckConstraint(
            "(bbox_x0 IS NULL AND bbox_y0 IS NULL AND bbox_x1 IS NULL AND bbox_y1 IS NULL) OR "
            "(bbox_x0 >= 0 AND bbox_y0 >= 0 AND bbox_x1 <= 1 AND bbox_y1 <= 1 AND bbox_x1 >= bbox_x0 AND bbox_y1 >= bbox_y0)",
            name="ck_document_page_blocks_bbox",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    page_id: Mapped[str] = mapped_column(ForeignKey("document_pages.id", ondelete="CASCADE"), index=True)
    block_index: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    bbox_x0: Mapped[float | None] = mapped_column(nullable=True)
    bbox_y0: Mapped[float | None] = mapped_column(nullable=True)
    bbox_x1: Mapped[float | None] = mapped_column(nullable=True)
    bbox_y1: Mapped[float | None] = mapped_column(nullable=True)
    char_start: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    char_end: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    page: Mapped[DocumentPage] = relationship(back_populates="blocks")
