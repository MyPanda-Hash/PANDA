from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.auth import Base


class QaSession(Base):
    __tablename__ = "qa_sessions"
    __table_args__ = (Index("ix_qa_sessions_owner_created", "owner_user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    selected_space_ids: Mapped[str] = mapped_column(Text)
    scope_version: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    turns: Mapped[list[QaTurn]] = relationship(back_populates="session", cascade="all, delete-orphan")


class QaTurn(Base):
    __tablename__ = "qa_turns"
    __table_args__ = (
        CheckConstraint(
            "status in ('completed', 'knowledge_gap', 'budget_exceeded', 'source_permission_denied')",
            name="ck_qa_turns_terminal_status",
        ),
        Index("ix_qa_turns_session_created", "session_id", "created_at"),
        Index("ix_qa_turns_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("qa_sessions.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    selected_space_ids: Mapped[str] = mapped_column(Text)
    scope_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    retrieval_summary: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_summary: Mapped[str] = mapped_column(Text)
    usage: Mapped[str] = mapped_column(Text)
    events: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session: Mapped[QaSession] = relationship(back_populates="turns")
    citations: Mapped[list[Citation]] = relationship(back_populates="turn", cascade="all, delete-orphan")


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (
        CheckConstraint("support_state in ('supported', 'uncertain', 'unsupported')", name="ck_citations_support_state"),
        Index("ix_citations_document_version", "document_id", "document_version_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    turn_id: Mapped[str] = mapped_column(ForeignKey("qa_turns.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    document_version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), index=True)
    page_or_section: Mapped[str] = mapped_column(String(255))
    excerpt: Mapped[str] = mapped_column(Text)
    support_state: Mapped[str] = mapped_column(String(32))
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_block_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_page_blocks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    bbox_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    turn: Mapped[QaTurn] = relationship(back_populates="citations")
