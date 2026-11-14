"""主动汇报的读取入口：语音悬浮窗、MES 通知都能从这里拿。

只读，按当前登录账号的 MES 权限取数；不做写操作。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import current_user_from_bearer_or_cookie
from app.core.database import transaction
from app.models.auth import User
from app.models.inapp_notification import InappNotification
from app.services import production_briefing

router = APIRouter(prefix="/assistant/briefing", tags=["assistant-briefing"])


@router.get("")
def latest_briefing(user: User = Depends(current_user_from_bearer_or_cookie)) -> dict:
    """当前账号的生产概况（按他自己的 MES 权限）。"""
    briefing = production_briefing.for_user(user.id)
    return {
        "data": {
            "text": briefing.speak(),
            "ok": briefing.ok,
            "note": briefing.note,
            "lines": briefing.lines,
            "items": briefing.items,
            "skipped": briefing.skipped,
            "generatedAt": date.today().isoformat(),
        }
    }


@router.get("/notifications")
def briefing_notifications(
    limit: int = 5,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> dict:
    """最近的主动汇报（早间推送写进通知表的那几条），用于「昨天/今早跟我说了什么」。"""
    with transaction() as db:
        rows = list(
            db.scalars(
                select(InappNotification)
                .where(InappNotification.user_id == user.id)
                .where(InappNotification.kind == "notice")
                .order_by(InappNotification.created_at.desc())
                .limit(max(1, min(limit, 20)))
            )
        )
        items = [
            {
                "id": row.id,
                "title": row.title,
                "body": row.body,
                "createdAt": row.created_at.isoformat() if row.created_at else "",
                "read": bool(row.read_at),
            }
            for row in rows
        ]
    return {"data": {"items": items}}
