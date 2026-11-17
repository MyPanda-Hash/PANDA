"""AI 用量与额度：按账号、按月统计模型消耗，并限制每月的花费上限。

为什么单独建表而不是复用 budget_*：
那套是给视觉/记忆/日程用的「operation 级预留-结算」机制（budget_reservations
按 operation_id 记账、有 hold_until 租约），天生带并发保护，但对「每人每月花了多少」
这种事后聚合既重又绕——这里要的是能按月分组求和、能一把查出一张表看全员的流水。

计价复用 pricing.calculate_cost() 和既有 model_prices 表，不另造一套规则。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class AiUsageLog(Base):
    """一次对话问答的用量流水（多轮工具调用已合并成一行）。"""

    __tablename__ = "ai_usage_log"
    __table_args__ = (
        # 月度聚合是主要查询：WHERE biz_month = ? GROUP BY user_id
        Index("ix_ai_usage_month_user", "biz_month", "user_id"),
        Index("ix_ai_usage_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    # 冗余账号名：用户改名或删除后，历史流水仍然可读
    username: Mapped[str] = mapped_column(String(120), default="")
    real_name: Mapped[str] = mapped_column(String(120), default="")
    # 账套（两个账套要分开看成本）
    factory_code: Mapped[str] = mapped_column(String(64), default="")

    model: Mapped[str] = mapped_column(String(120), default="")
    provider: Mapped[str] = mapped_column(String(64), default="")

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # 金额用 Numeric 存，别用 float：钱累加起来会飘
    cost_cny: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    # 单价快照：以后调价了，历史账单不会被改写
    input_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    output_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))

    biz_month: Mapped[str] = mapped_column(String(7), default="")  # YYYY-MM
    source: Mapped[str] = mapped_column(String(32), default="chat")
    session_id: Mapped[str] = mapped_column(String(120), default="")

    rounds: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AiQuota(Base):
    """账号的月度额度上限。没有行的账号＝不限额。"""

    __tablename__ = "ai_quota"
    __table_args__ = (UniqueConstraint("user_id", name="uq_ai_quota_user"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    username: Mapped[str] = mapped_column(String(120), default="")

    # 每月上限（人民币）。NULL = 不限；0 = 禁止使用
    monthly_limit_cny: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)