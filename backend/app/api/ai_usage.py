"""AI 用量与额度的管理接口（管理员）。

- 看：每账号每月的调用次数、token、金额；
- 配：每账号每月额度上限（不配＝不限）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import admin_user
from app.core.database import transaction
from app.models.ai_usage import AiQuota, AiUsageLog
from app.models.auth import User
from app.services import ai_usage

router = APIRouter(prefix="/admin/ai-usage", tags=["ai-usage"])


@router.get("")
def usage_report(
    month: str | None = Query(default=None, description="YYYY-MM，默认本月"),
    _: User = Depends(admin_user),
) -> dict:
    """管理员：某月各账号的 AI 用量（按花费降序）。"""
    period = month or ai_usage.current_month()
    with transaction() as db:
        rows = ai_usage.monthly_report(db, period)
        # 也把没花钱但有额度的账号带出来，否则配了额度却看不到人
        known = {r["user_id"] for r in rows}
        for quota in db.execute(select(AiQuota)).scalars().all():
            if quota.user_id in known:
                continue
            rows.append({
                "user_id": quota.user_id,
                "username": quota.username or "",
                "real_name": "",
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_cny": "0",
                "limit_cny": None if quota.monthly_limit_cny is None else str(quota.monthly_limit_cny),
                "over_limit": False,
            })
    return {"data": {"month": period, "rows": rows}}


@router.get("/monthly")
def usage_by_month(
    months: int = Query(default=6, ge=1, le=24),
    _: User = Depends(admin_user),
) -> dict:
    """管理员：最近 N 个月的总花费趋势（看成本走势用）。"""
    from sqlalchemy import func

    with transaction() as db:
        rows = db.execute(
            select(
                AiUsageLog.biz_month,
                func.count(AiUsageLog.id),
                func.coalesce(func.sum(AiUsageLog.cost_cny), 0),
            )
            .group_by(AiUsageLog.biz_month)
            .order_by(AiUsageLog.biz_month.desc())
            .limit(months)
        ).all()
    return {
        "data": [
            {"month": m, "calls": int(c or 0), "cost_cny": str(Decimal(str(cost or 0)).quantize(Decimal("0.0001")))}
            for m, c, cost in rows
        ]
    }


@router.get("/prices")
def list_prices(_: User = Depends(admin_user)) -> dict:
    """管理员：当前生效单价（界面可改）。"""
    from app.models.governance import ModelPrice

    with transaction() as db:
        rows = db.execute(
            select(ModelPrice).where(ModelPrice.superseded_at.is_(None)).order_by(ModelPrice.model)
        ).scalars().all()
    if not rows:
        return {"data": {"items": list(ai_usage.DEFAULT_PRICES), "source": "default"}}
    return {
        "data": {
            "items": [
                {
                    "provider": r.provider,
                    "model": r.model,
                    "input_per_million": str(r.input_per_million),
                    "output_per_million": str(r.output_per_million),
                    "cache_read_per_million": str(r.cache_read_per_million or 0),
                }
                for r in rows
            ],
            "source": "db",
        }
    }


@router.patch("/quota")
def save_quota(body: dict[str, Any], _: User = Depends(admin_user)) -> dict:
    """管理员：设置某账号的月额度。limit_cny 传 null 表示不限，0 表示禁止使用。"""
    user_id = str(body.get("user_id") or "").strip()
    if not user_id:
        return {"code": 400, "message": "user_id 不能为空", "data": None}

    raw_limit = body.get("limit_cny")
    limit: Decimal | None
    if raw_limit is None or str(raw_limit).strip() == "":
        limit = None
    else:
        try:
            limit = Decimal(str(raw_limit)).quantize(Decimal("0.01"))
        except Exception:  # noqa: BLE001
            return {"code": 400, "message": "额度必须是数字", "data": None}
        if limit < 0:
            return {"code": 400, "message": "额度不能为负数", "data": None}

    with transaction() as db:
        row = db.execute(select(AiQuota).where(AiQuota.user_id == user_id)).scalar_one_or_none()
        if row is None:
            row = AiQuota(
                id="quo_" + uuid4().hex,
                user_id=user_id,
                username=str(body.get("username") or ""),
            )
            db.add(row)
        row.monthly_limit_cny = limit
        row.username = str(body.get("username") or row.username or "")
        row.note = str(body.get("note") or "")
        row.is_active = True
        db.flush()

    return {"data": {"user_id": user_id, "limit_cny": None if limit is None else str(limit)}}