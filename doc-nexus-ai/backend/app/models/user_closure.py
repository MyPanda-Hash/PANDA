from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
	CheckConstraint,
	DateTime,
	ForeignKey,
	Index,
	String,
	UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class ChatMessageFeedback(Base):
    """遗留历史：点赞/点踩能力已下线，这里是保留的表映射。

    聊天界面不再记录回答评分，也没有任何代码读写这张表，但用户此前产生的行
    原样保留，因此映射继续存在：既不触发删表迁移，也让历史数据仍可查阅。
    """

    __tablename__ = "chat_message_feedback"
    __table_args__ = (
        CheckConstraint(
            "rating in ('up', 'down')",
            name="ck_chat_message_feedback_rating",
        ),
        CheckConstraint(
            "reason is null or reason in ('inaccurate', 'unsupported', 'irrelevant', 'unsafe', 'other')",
            name="ck_chat_message_feedback_reason",
        ),
        CheckConstraint(
            "rating = 'down' or reason is null",
            name="ck_chat_message_feedback_up_reason",
        ),
        Index("ix_chat_message_feedback_owner_updated", "owner_user_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    rating: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskAcknowledgement(Base):
    __tablename__ = "task_acknowledgements"
    __table_args__ = (
		UniqueConstraint(
			"owner_user_id", "task_key", name="uq_task_acknowledgements_owner_task"
		),
        Index("ix_task_acknowledgements_owner_acknowledged", "owner_user_id", "acknowledged_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    task_key: Mapped[str] = mapped_column(String(192))
    acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
