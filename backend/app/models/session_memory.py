"""AI 会话记忆：把员工「用 AI 做了什么」蒸成一页可读的记录。

与 employee_profiles（数字统计 + 职能结论）互补：
- 画像回答「这个人这个周期干得怎么样」
- 会话记忆回答「这个人具体用 AI 处理了哪些事、得到什么结论」

证据全部可回溯：每条记忆带 session_ids，管理员能从记忆跳回原始会话。
仅管理员可见，理由同 employee_profile。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class SessionMemory(Base):
    """一条会话蒸馏结果：某人 + 某个周期 + 一批会话 -> 一段结论。

    UNIQUE(user_id, period_start)：同一周期重跑就覆盖，不会越积越多，
    与「记忆应当稳定」的预期一致。
    """

    __tablename__ = "session_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "period_start", name="uq_session_memories_user_period"),
        Index("ix_session_memories_user_created", "user_id", "created_at"),
        Index("ix_session_memories_topic_created", "topic", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    headline: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(96), default="")
    highlights: Mapped[str] = mapped_column(Text, default="[]")
    lessons: Mapped[str] = mapped_column(Text, default="[]")
    tools: Mapped[str] = mapped_column(Text, default="[]")

    session_ids: Mapped[str] = mapped_column(Text, default="[]")
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    session_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    generated_by: Mapped[str] = mapped_column(String(16), default="rules")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)