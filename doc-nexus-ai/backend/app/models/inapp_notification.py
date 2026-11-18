"""站内通知：派下来的待办、催办、通知，落到收件人自己的提醒里。

为什么不复用 ``notification_deliveries``：那套是往 SMTP / 钉钉 / 企微外发的投递队列，
有凭据、指纹、重试状态机。这里只要「我该看到一条气泡」这么轻的东西，
单独一张表反而更清楚，也不会污染外发投递的统计。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base

INAPP_KINDS = ("todo_assigned", "todo_nudged", "todo_escalated", "notice", "system")


class InappNotification(Base):
    __tablename__ = "inapp_notifications"
    __table_args__ = (
        CheckConstraint(
            "kind in ('todo_assigned', 'todo_nudged', 'todo_escalated', 'notice', 'system')",
            name="ck_inapp_kind",
        ),
        Index("ix_inapp_user_created", "user_id", "created_at"),
        Index("ix_inapp_user_read", "user_id", "read_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    # 点「去处理」跳到哪：todo / notice / system
    ref_type: Mapped[str] = mapped_column(String(32), default="")
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 「来自 管理员」这种，微信式气泡要显示发送人
    actor_name: Mapped[str] = mapped_column(String(120), default="")
    # 同一个人同一件事只提醒一次，用这个键去重
    dedupe_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )