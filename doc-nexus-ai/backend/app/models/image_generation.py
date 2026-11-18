from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.chat import ChatDispatchDecision

from app.models.auth import Base


class ImageGenerationTask(Base):
    __tablename__ = "image_generation_tasks"
    __table_args__ = (
        CheckConstraint(
            "resolution in ('1k', '2k')",
            name="ck_image_generation_tasks_resolution",
        ),
        CheckConstraint(
            "status in "
            "('pending', 'running', 'submitting', 'outcome_unknown', "
            "'succeeded', 'failed', 'canceled')",
            name="ck_image_generation_tasks_status",
        ),
        CheckConstraint(
            "operation in ('generate', 'edit')",
            name="ck_image_generation_tasks_operation",
        ),
        Index(
            "ix_image_generation_tasks_owner_created",
            "owner_user_id",
            "created_at",
        ),
        Index(
            "ix_image_generation_tasks_session_created",
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
    operation: Mapped[str] = mapped_column(
        String(16), nullable=False, default="generate", server_default="generate"
    )
    model: Mapped[str] = mapped_column(String(160))
    aspect_ratio: Mapped[str] = mapped_column(String(8))
    resolution: Mapped[str] = mapped_column(String(4))
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_cache_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    image_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_context_consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dispatch: Mapped[ChatDispatchDecision | None] = relationship(
        back_populates="image_tasks"
    )
