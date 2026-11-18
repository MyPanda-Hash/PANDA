"""员工职能卡片：从真实行为数据蒸馏出的个人画像。

数据来源全部是可核对的事实：
- ``audit_events``  谁在什么时候做了什么操作
- ``chat_messages`` 个人会话里的提问与产出
- ``project_group_messages`` 群聊发言
- ``group_todos`` 分配到的人与其完成情况
- ``assistant_events`` AI 派单的认领/处理

**仅管理员可见**：这些都是后台行为数据，普通用户看不到别人的卡片，
也看不到自己卡片的原始明细（避免「监控我」的观感，同时保留审计能力）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
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


class EmployeeProfile(Base):
    """一个周期（周）的员工画像快照。"""

    __tablename__ = "employee_profiles"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "period_start", name="uq_employee_profiles_user_period"
        ),
        Index("ix_employee_profiles_period", "period_start"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # ---- 行为统计（全部可从原始数据重算） ----
    chat_sessions: Mapped[int] = mapped_column(Integer, default=0)
    chat_messages: Mapped[int] = mapped_column(Integer, default=0)
    group_messages: Mapped[int] = mapped_column(Integer, default=0)
    todo_assigned: Mapped[int] = mapped_column(Integer, default=0)
    todo_completed: Mapped[int] = mapped_column(Integer, default=0)
    events_acked: Mapped[int] = mapped_column(Integer, default=0)
    actions_total: Mapped[int] = mapped_column(Integer, default=0)
    active_days: Mapped[int] = mapped_column(Integer, default=0)
    # 平均响应：从被派单到认领的中位小时数
    avg_response_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ---- 蒸馏结果（模型生成，可缺省） ----
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    strengths: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    risks: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    focus_areas: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    capability_tags: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    # 具体做过什么的摘要（给管理员看行为轨迹）
    activity_digest: Mapped[str] = mapped_column(Text, default="", server_default="")

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generated_by: Mapped[str] = mapped_column(String(16), default="rules")


class ProjectExperience(Base):
    """项目经验资料：从群聊与待办里沉淀的可复用经验。

    借鉴 allexyuan/skill-cairn（积微）的「证据约束知识晋升」：
    每条经验都要有确定的来源、适用范围、验证方式与状态，
    同一 topic 的经验积累够证据后才允许晋升成 SOP。
    """

    __tablename__ = "project_experiences"
    __table_args__ = (
        CheckConstraint(
            "source in ('group', 'todo', 'manual')",
            name="ck_project_experiences_source",
        ),
        Index("ix_project_experiences_group", "group_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    # 稳定的小写 slug，用于把同类经验归到一起做晋升判断（对应 cairn 的 topic）
    topic: Mapped[str] = mapped_column(String(120), default="", server_default="", index=True)
    # 证据状态的受控取值（对应 cairn 的 unverified/verified/stale）
    status: Mapped[str] = mapped_column(
        String(16), default="unverified", server_default="unverified"
    )
    reusable: Mapped[bool] = mapped_column(default=False, server_default="0")
    # 适用范围边界：什么情况下不该照搬
    not_applicable: Mapped[str] = mapped_column(Text, default="", server_default="")
    # 独立来源条数（来自多少个不同的人/群），晋升门禁要用
    source_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # 问题 / 处理 / 结论，便于检索
    problem: Mapped[str] = mapped_column(Text, default="", server_default="")
    resolution: Mapped[str] = mapped_column(Text, default="", server_default="")
    lesson: Mapped[str] = mapped_column(Text, default="", server_default="")
    tags: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    source: Mapped[str] = mapped_column(String(16), default="group")
    source_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contributors: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
