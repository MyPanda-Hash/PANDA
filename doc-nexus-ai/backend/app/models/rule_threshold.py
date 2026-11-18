"""阈值改动留痕：谁在什么时候把哪条门槛从多少改成了多少。

为什么要这张表：阈值直接决定「会不会报警」。把不合格率门槛从 5% 调到 15%，
可以是真的工艺稳定了，也可以是有人嫌报警太吵——事后必须能查。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class RuleThresholdChange(Base):
    __tablename__ = "rule_threshold_changes"
    __table_args__ = (
        Index("ix_rule_threshold_changes_key_created", "key", "created_at"),
        # 值统一存整数或百分比的「百万分之一」，避免浮点比较
        CheckConstraint("value_kind in ('int', 'rate')", name="ck_rule_threshold_value_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 阈值 key，例如 stall_days
    key: Mapped[str] = mapped_column(String(64), index=True)
    #: int=计数类；rate=比例类（存万分比，如 0.05 → 500）
    value_kind: Mapped[str] = mapped_column(String(8), default="int")
    #: 改之前的值（整数或万分比）
    old_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 改之后的值
    new_value: Mapped[int] = mapped_column(Integer)
    #: 改之前/之后的来源：default / env / override / reverted
    source_from: Mapped[str] = mapped_column(String(16), default="override")
    source_to: Mapped[str] = mapped_column(String(16), default="override")
    #: 谁改的（username；环境变量来源记 env）
    actor: Mapped[str] = mapped_column(String(64), default="")
    #: 为什么改
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)