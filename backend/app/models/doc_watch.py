"""订阅式盯单。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class DocWatch(Base):
    __tablename__ = "doc_watches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    doc_no: Mapped[str] = mapped_column(String(64), index=True)
    panel_code: Mapped[str] = mapped_column(String(64), default="")
    condition: Mapped[str] = mapped_column(String(24), default="progress")
    target_keyword: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(255), default="")
    baseline_json: Mapped[str] = mapped_column(Text, default="")
    last_signal: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
