"""AI 用量记账与额度校验。

口径（2026-11-17 与用户确认）：
- 只统计**对话**（chat），群聊/语音/报表暂不计入；
- 额度＝每账号每月人民币上限，超额**拒绝并提示**，管理员可调高或临时放开；
- 单价来自 model_prices，界面可改；历史流水存单价快照，调价不改旧账。
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ai_usage import AiQuota, AiUsageLog
from app.services import pricing

logger = logging.getLogger(__name__)

# DeepSeek 公开价（人民币 / 百万 token）。界面可改，这里只是首次使用的默认值。
# 来源：DeepSeek 官方定价页；调价后请在「AI 用量」页改，不要改这里。
DEFAULT_PRICES: tuple[dict[str, Any], ...] = (
    {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "input_per_million": "2",
        "output_per_million": "8",
        "cache_read_per_million": "0.5",
    },
    {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "input_per_million": "2",
        "output_per_million": "8",
        "cache_read_per_million": "0.5",
    },
)


def current_month() -> str:
    """账期用本地时间：用户看到的「本月」应该是自己时区的本月。"""
    return datetime.now().strftime("%Y-%m")


def _price_of(db: Session, provider: str, model: str) -> tuple[Decimal, Decimal, Decimal]:
    """取生效单价；查不到就用定价表里的第一条同类，再不行按 0 算（不阻断使用）。"""
    row = pricing.find_active_price(db, provider=provider, model=model) if hasattr(pricing, "find_active_price") else None
    if row is not None:
        return (
            pricing._as_decimal(getattr(row, "input_per_million", 0)) or Decimal("0"),
            pricing._as_decimal(getattr(row, "output_per_million", 0)) or Decimal("0"),
            pricing._as_decimal(getattr(row, "cache_read_per_million", 0)) or Decimal("0"),
        )
    for item in DEFAULT_PRICES:
        if item["model"] == model:
            return (Decimal(item["input_per_million"]), Decimal(item["output_per_million"]), Decimal(item["cache_read_per_million"]))
    return Decimal("0"), Decimal("0"), Decimal("0")


def record_usage(
    db: Session,
    *,
    user_id: str,
    username: str = "",
    real_name: str = "",
    factory_code: str = "",
    model: str = "",
    provider: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    rounds: int = 0,
    duration_ms: int = 0,
    session_id: str = "",
    source: str = "chat",
) -> AiUsageLog:
    """记一条用量流水。金额按当前生效单价算，并存下单价快照。"""
    input_price, output_price, cache_price = _price_of(db, provider, model)
    cost = pricing.calculate_cost(
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        cache_read_tokens=int(cache_read_tokens or 0),
        input_per_million=input_price,
        output_per_million=output_price,
        cache_read_per_million=cache_price,
    )

    entry = AiUsageLog(
        id="usg_" + uuid4().hex,
        user_id=user_id,
        username=username,
        real_name=real_name,
        factory_code=factory_code,
        model=model,
        provider=provider,
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        cache_read_tokens=int(cache_read_tokens or 0),
        cost_cny=cost,
        input_price_snapshot=input_price,
        output_price_snapshot=output_price,
        biz_month=current_month(),
        source=source,
        session_id=session_id,
        rounds=int(rounds or 0),
        duration_ms=int(duration_ms or 0),
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    return entry


def month_spend(db: Session, user_id: str, month: str | None = None) -> Decimal:
    """某账号某月已花金额。"""
    period = month or current_month()
    total = db.execute(
        select(func.coalesce(func.sum(AiUsageLog.cost_cny), 0)).where(
            AiUsageLog.user_id == user_id,
            AiUsageLog.biz_month == period,
        )
    ).scalar()
    return Decimal(str(total or 0))


def quota_of(db: Session, user_id: str) -> AiQuota | None:
    return db.execute(
        select(AiQuota).where(AiQuota.user_id == user_id, AiQuota.is_active.is_(True))
    ).scalar_one_or_none()


def check_quota(db: Session, user_id: str) -> tuple[bool, str, dict[str, Any]]:
    """对话前校验额度。返回 (是否放行, 拒绝时给用户的提示, 明细)。"""
    quota = quota_of(db, user_id)
    spent = month_spend(db, user_id)
    detail = {
        "month": current_month(),
        "spent_cny": str(spent.quantize(Decimal("0.0001"))),
        "limit_cny": None if quota is None or quota.monthly_limit_cny is None else str(quota.monthly_limit_cny),
    }
    if quota is None or quota.monthly_limit_cny is None:
        return True, "", detail

    limit = Decimal(str(quota.monthly_limit_cny))
    if spent < limit:
        return True, "", detail

    if limit <= 0:
        # 0 是「禁止使用」，和「额度刚好花完」要分开说，否则用户以为是自己花超了
        message = "该账号暂未开通 AI 使用权限，请联系管理员分配额度。"
    else:
        message = (
            "本月 AI 额度已用完（已用 ¥"
            + str(spent.quantize(Decimal("0.01")))
            + "，上限 ¥"
            + str(limit.quantize(Decimal("0.01")))
            + "）。请联系管理员调高额度，或下个月再用。"
        )
    return False, message, detail


def monthly_report(db: Session, month: str | None = None) -> list[dict[str, Any]]:
    """按账号聚合某月用量，附带额度和是否超额。管理员页面直接用这个。"""
    period = month or current_month()
    rows = db.execute(
        select(
            AiUsageLog.user_id,
            func.max(AiUsageLog.username),
            func.max(AiUsageLog.real_name),
            func.count(AiUsageLog.id),
            func.coalesce(func.sum(AiUsageLog.input_tokens), 0),
            func.coalesce(func.sum(AiUsageLog.output_tokens), 0),
            func.coalesce(func.sum(AiUsageLog.cost_cny), 0),
        )
        .where(AiUsageLog.biz_month == period)
        .group_by(AiUsageLog.user_id)
        .order_by(func.sum(AiUsageLog.cost_cny).desc())
    ).all()

    quotas = {q.user_id: q for q in db.execute(select(AiQuota)).scalars().all()}
    report: list[dict[str, Any]] = []
    for user_id, username, real_name, calls, tin, tout, cost in rows:
        quota = quotas.get(user_id)
        limit = None if quota is None or quota.monthly_limit_cny is None else Decimal(str(quota.monthly_limit_cny))
        spent = Decimal(str(cost or 0))
        report.append({
            "user_id": user_id,
            "username": username or "",
            "real_name": real_name or "",
            "calls": int(calls or 0),
            "input_tokens": int(tin or 0),
            "output_tokens": int(tout or 0),
            "cost_cny": str(spent.quantize(Decimal("0.0001"))),
            "limit_cny": None if limit is None else str(limit.quantize(Decimal("0.01"))),
            "over_limit": bool(limit is not None and spent >= limit),
        })
    return report