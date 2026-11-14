"""AI 助手主动监控 MES 得到的事件。

一条事件 = 某个「待处理事实」（例如：销售订单已审核但没建生产工单）。
按 ``dedupe_key`` 去重，条件消失后标成 resolved，避免每次轮询都刷一遍。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base

EVENT_STATUSES = ("open", "handled", "resolved")
EVENT_SEVERITIES = ("info", "warning", "critical")


class AssistantEvent(Base):
    __tablename__ = "assistant_events"
    __table_args__ = (
        Index("ix_assistant_events_status_created", "status", "created_at"),
        Index("ix_assistant_events_kind", "kind"),
        CheckConstraint(
            "status in ('open', 'handled', 'resolved')", name="ck_assistant_events_status"
        ),
        CheckConstraint(
            "severity in ('info', 'warning', 'critical')",
            name="ck_assistant_events_severity",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 去重键：同一个待处理事实只留一条，例如 "so_unstarted:SO-20261112-005"
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(48))
    source: Mapped[str] = mapped_column(String(32), default="mes")
    panel_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    doc_no: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text, default="")
    suggested_action: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="info")
    status: Mapped[str] = mapped_column(String(16), default="open")
    target_usernames_json: Mapped[str] = mapped_column(Text, default="[]")
    target_user_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    # 触达情况：谁被通知过、通知了几次
    notifications: Mapped[int] = mapped_column(Integer, default=0)
    # 反复提醒：每隔 REMIND_EVERY_HOURS 小时重发一次，最多 MAX_REMINDERS 次
    reminders: Mapped[int] = mapped_column(Integer, default=0)
    last_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 首次发现时的严重度，用于「拖久了自动升级」
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    original_severity: Mapped[str] = mapped_column(
        String(16), default="info", server_default="info"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acked_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # 认领/处理时留的一句话，便于交接和回溯
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: 关掉之后又回来的次数。反复复发说明根因没动，只处理表面会一直回来。
    recurrence: Mapped[int] = mapped_column(Integer, default=0)
    last_reopened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WatchScanRun(Base):
    """每轮巡查的运行记录：耗时、拉取面板数、事件数、失败原因。

    便于回答「AI 多久看一次 MES」「上一轮跑成没跑成」「为什么没发现东西」。
    """

    __tablename__ = "assistant_watch_runs"
    __table_args__ = (
        Index("ix_watch_runs_started", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16), default="timer")  # timer/manual
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/ok/failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    panels_fetched: Mapped[int] = mapped_column(Integer, default=0)
    events_evaluated: Mapped[int] = mapped_column(Integer, default=0)
    events_created: Mapped[int] = mapped_column(Integer, default=0)
    events_resolved: Mapped[int] = mapped_column(Integer, default=0)
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
