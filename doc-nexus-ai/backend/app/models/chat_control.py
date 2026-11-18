from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class ChatControlOperation(Base):
    """Privacy-safe trace for a steer or follow-up command.

    It deliberately stores route and usage metadata only.  The control text,
    image payloads, and visual-context body remain in neither this table nor
    the usage views built from it.
    """

    __tablename__ = "chat_control_operations"
    __table_args__ = (
        Index(
            "ix_chat_control_operations_owner_created",
            "owner_user_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    usage: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
