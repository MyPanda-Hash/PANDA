"""对话归档：把「谁在什么时候问了什么、聊了多久」留档给管理员。

为什么单独一张表：
- chat-demo 的会话正文存在浏览器 IndexedDB 里（各人各自独立，这是既定选择）；
- 但企业需要审计：谁用过、用了多久、问了哪类问题；
- 所以这里只存**归档摘要 + 完整消息快照**，不参与对话存取，
  也不改变 chat-demo 自身的存储方式。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class ChatArchiveSession(Base):
    """一条对话的归档记录（按「用户 + 前端会话 id」唯一）。"""

    __tablename__ = "chat_archive_sessions"
    __table_args__ = (
        Index("ix_chat_archive_owner_started", "owner_user_id", "started_at"),
        Index("ix_chat_archive_started", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 冗余用户名：管理员列表要按账号看，不能每次都 join
    owner_username: Mapped[str] = mapped_column(String(128), default="")
    owner_display: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_active_at: Mapped[datetime] = mapped_column(DateTime)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    # 最后一条用户提问的摘要，列表里一眼能看出聊了什么
    preview: Mapped[str] = mapped_column(String(500), default="")
    # 完整消息快照（JSON 文本）；管理员点开才读，列表不取
    messages_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )