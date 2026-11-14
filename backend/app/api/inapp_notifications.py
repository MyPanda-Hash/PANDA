"""站内通知接口：给前端弹「微信式消息气泡」用。

没人用长连接推：悬浮窗/门户都是轮询，间隔几秒足够像微信；
少一个 WebSocket 就少一类断线重连的问题。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import current_user_from_bearer_or_cookie
from app.core.database import transaction
from app.models.auth import User
from app.services import inapp_notifications

router = APIRouter(prefix="/notifications", tags=["notifications"])


class _ReadRequest(BaseModel):
    ids: list[str] = []


@router.get("")
def list_notifications(
    user: User = Depends(current_user_from_bearer_or_cookie),
    unread_only: Annotated[bool, Query(alias="unread")] = True,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    with transaction() as db:
        rows = inapp_notifications.pending(db, user.id, limit=limit)
        return {
            "data": {
                "unread": inapp_notifications.unread_count(db, user.id),
                "items": [inapp_notifications.render(row) for row in rows],
            }
        }


@router.post("/read")
def mark_read(
    payload: _ReadRequest,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> dict:
    with transaction() as db:
        changed = inapp_notifications.mark_read(db, user.id, payload.ids or None)
        return {"data": {"marked": changed, "unread": inapp_notifications.unread_count(db, user.id)}}