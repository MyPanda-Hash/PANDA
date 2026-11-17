"""资料库：模板与文件集中存放。

为什么单独一张表：现有 document_uploads 是知识库的分片上传流程（checksum、
临时 bucket、租约），用来存「一个 PPTX 模板」太重；这里只要
「谁在什么时候传了一个什么文件、归到哪一类」。
文件本体落在 data/library/，表里只存相对路径，方便整目录备份。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base

# 分类是固定的几个，避免自由文本导致列表没法按类筛
CATEGORIES = {
    "template": "报表模板",
    "policy": "制度文件",
    "process": "工艺文件",
    "other": "其他",
}


class LibraryFile(Base):
    __tablename__ = "library_files"
    __table_args__ = (
        Index("ix_library_category_created", "category", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(32), default="other", index=True)
    mime: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    # 相对 data/library/ 的文件名；不存绝对路径，换机器不用改库
    stored_name: Mapped[str] = mapped_column(String(255))
    note: Mapped[str] = mapped_column(Text, default="")
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    owner_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)