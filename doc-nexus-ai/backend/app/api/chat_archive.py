"""对话归档 API：上报（本人）+ 查询（管理员）。

边界：
- 上报只写自己的记录，ID 用「用户 + 前端会话 id」拼，避免跨用户覆盖；
- 列表与详情只有管理员能看（沿用 admin_user 依赖）；
- 归档是**旁路**，不参与对话本身，chat-demo 仍用浏览器本地存储。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.api.deps import admin_user, current_user_from_bearer_or_cookie
from app.core.database import transaction
from app.models.auth import User
from app.models.chat_archive import ChatArchiveSession

router = APIRouter(prefix="/chat-archive", tags=["chat-archive"])


def _iso(value) -> str:
    # 库里存的是 naive UTC；必须显式标 Z，前端才会按 UTC 转本地时区。
    return value.isoformat() + 'Z' if value else ''


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_ts(value: Any) -> datetime:
    """前端的 ISO 字符串或毫秒时间戳都接受；解析不了就用当前时间。"""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return _now()
    text = str(value or "").strip()
    if not text:
        return _now()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return _now()


@router.post("/sync")
def sync_archive(
    body: dict[str, Any],
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> dict:
    """上报一条会话归档（存在就更新）。前端在会话变化时调用。"""
    local_id = str(body.get("sessionId") or "").strip()
    if not local_id:
        raise HTTPException(status_code=422, detail="缺少 sessionId")

    # 归档 id 带用户前缀：同一台机器换人登录不会互相覆盖
    archive_id = f"{user.id}:{local_id}"[:120]
    messages = body.get("messages") or []
    if not isinstance(messages, list):
        messages = []

    preview = ""
    for item in reversed(messages):
        if isinstance(item, dict) and str(item.get("role")) == "user":
            preview = str(item.get("content") or "").strip()[:500]
            break

    with transaction() as db:
        row = db.get(ChatArchiveSession, archive_id)
        if row is None:
            row = ChatArchiveSession(
                id=archive_id,
                owner_user_id=user.id,
                started_at=_parse_ts(body.get("startedAt")),
            )
            db.add(row)
        row.owner_username = str(getattr(user, "username", "") or "")
        row.owner_display = str(getattr(user, "display_name", "") or getattr(user, "username", "") or "")
        row.title = str(body.get("title") or "新对话")[:255]
        row.last_active_at = _parse_ts(body.get("updatedAt"))
        row.message_count = int(body.get("messageCount") or len(messages))
        row.preview = preview
        # 完整快照留档；量大时这里是主要体积，列表不读它
        row.messages_json = json.dumps(messages, ensure_ascii=False)[:200000]
    return {"data": {"id": archive_id}}


@router.get("")
def list_archive(
    _: User = Depends(admin_user),
    owner: Annotated[str, Query(max_length=128)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """管理员：按账号看归档列表（不含消息正文）。"""
    with transaction() as db:
        stmt = select(ChatArchiveSession).order_by(ChatArchiveSession.last_active_at.desc())
        if owner.strip():
            stmt = stmt.where(ChatArchiveSession.owner_username == owner.strip())
        rows = db.scalars(stmt.limit(limit)).all()
        return {
            "data": [
                {
                    "id": row.id,
                    "ownerUsername": row.owner_username,
                    "ownerDisplay": row.owner_display,
                    "title": row.title,
                    "startedAt": _iso(row.started_at),
                    "lastActiveAt": _iso(row.last_active_at),
                    "messageCount": row.message_count,
                    "preview": row.preview,
                }
                for row in rows
            ]
        }


@router.get("/{archive_id}")
def archive_detail(
    archive_id: str,
    _: User = Depends(admin_user),
) -> dict:
    """管理员：看一条归档的完整消息。"""
    with transaction() as db:
        row = db.get(ChatArchiveSession, archive_id)
        if row is None:
            raise HTTPException(status_code=404, detail="归档不存在")
        try:
            messages = json.loads(row.messages_json or "[]")
        except ValueError:
            messages = []
        return {
            "data": {
                "id": row.id,
                "ownerUsername": row.owner_username,
                "ownerDisplay": row.owner_display,
                "title": row.title,
                "startedAt": _iso(row.started_at),
                "lastActiveAt": _iso(row.last_active_at),
                "messageCount": row.message_count,
                "messages": messages,
            }
        }