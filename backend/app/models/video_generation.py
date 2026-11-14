from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.chat import ChatDispatchDecision

from app.models.auth import Base


class VideoGenerationTask(Base):
    __tablename__ = "video_generation_tasks"
    __table_args__ = (
        CheckConstraint(
            "mode in ('std', 'pro', '4k')", name="ck_video_generation_tasks_mode"
        ),
        CheckConstraint(
            "status in "
            "('pending', 'running', 'submitting', 'outcome_unknown', "
            "'succeeded', 'failed', 'canceled')",
            name="ck_video_generation_tasks_status",
        ),
        Index(
            "ix_video_generation_tasks_owner_created",
            "owner_user_id",
            "created_at",
        ),
        Index(
            "ix_video_generation_tasks_session_created",
            "chat_session_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    chat_session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    user_message_id: Mapped[str] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE")
    )
    assistant_message_id: Mapped[str] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE")
    )
    dispatch_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_dispatch_decisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider_task_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True
    )
    source_file_ref: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    model: Mapped[str] = mapped_column(String(160))
    mode: Mapped[str] = mapped_column(String(8))
    duration: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dispatch: Mapped[ChatDispatchDecision | None] = relationship(
        back_populates="video_tasks"
    )
