from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class ChatDeckRender(Base):
    """A template-rendered copy of one chat deck sidecar.

    This is deliberately separate from writing artifacts: chat renders belong to
    a message and must not appear in the writing-project artifact list.
    """

    __tablename__ = "chat_deck_renders"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'ready', 'blocked', 'failed', 'cancelled', 'timed_out')",
            name="ck_chat_deck_renders_status",
        ),
        UniqueConstraint(
            "message_id",
            "source_sha256",
            "template_revision_id",
            name="uq_chat_deck_renders_idempotency",
        ),
        UniqueConstraint("active_session_key", name="uq_chat_deck_renders_active_session"),
        Index("ix_chat_deck_renders_owner_created", "owner_user_id", "created_at"),
        Index("ix_chat_deck_renders_session_created", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )
    template_revision_id: Mapped[str] = mapped_column(
        ForeignKey("writing_doc_template_revisions.id", ondelete="RESTRICT"), index=True
    )
    source_task_id: Mapped[str] = mapped_column(String(160))
    source_file_path: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="running")
    active_session_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_mime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    blockers_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    page_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source_page_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    continuation_pages_json: Mapped[str] = mapped_column(
        Text, default="[]", server_default="[]"
    )
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
