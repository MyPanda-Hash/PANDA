"""每日简报推送：到点把「今日简报」推进 AI 数字员工自己里面。

为什么先不接手机：企业微信/短信要外部凭据与审批，属于「通道」问题；
先把内容按天生成、按人落成站内通知，通道随时可以加一层转发，
而内容与幂等这两件事是通道无关的，先做掉。

幂等靠 (user_id, ref_type='briefing', ref_id=日期) 判重：
进程重启、一天内多轮扫描都不会重复推同一天的简报。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import User
from app.models.inapp_notification import InappNotification
from app.services import brief_board

logger = logging.getLogger(__name__)

REF_TYPE = "briefing"


def _role_of(user: User) -> str:
    """推送口径：管理员看管理者简报，其余人看执行层口径。

    角色目前是前端预览概念，后端只有 admin 这一确定事实；
    这里按它分流，**不**假装知道更多。
    """
    return "manager" if (getattr(user, "role", "") or "") == "admin" else "supervisor"


def _body_of(board: dict) -> str:
    """通知正文：结论一句 + 前两条要拍板的事。

    正文要能在通知列表里直接读完，不要求点进去才知道发生了什么。
    """
    lines = [str(board.get("headline") or "").strip()]
    for item in (board.get("decisions") or [])[:2]:
        title = str(item.get("title") or "").strip()
        if title:
            lines.append(f"· {title}")
    if board.get("hidden"):
        lines.append(f"另有 {board['hidden']} 条执行层待办")
    return "\n".join([line for line in lines if line])


def already_pushed(db: Session, *, user_id: str, day: str) -> bool:
    row = db.scalar(
        select(InappNotification.id).where(
            InappNotification.user_id == user_id,
            InappNotification.ref_type == REF_TYPE,
            InappNotification.ref_id == day,
        )
    )
    return row is not None


def push_for_user(db: Session, *, user: User, day: str) -> InappNotification | None:
    """给一个人推一条今日简报；已推过返回 None。"""
    if already_pushed(db, user_id=user.id, day=day):
        return None
    role = _role_of(user)
    board = brief_board.for_user(db, user_id=user.id, role=role, period="")
    body = _body_of(board)
    if not body:
        return None
    notice = InappNotification(
        id=f"ntf_{uuid.uuid4().hex[:20]}",
        user_id=user.id,
        kind="notice",
        title=f"今日简报 · {day[5:]}",
        body=body,
        ref_type=REF_TYPE,
        ref_id=day,
        created_at=datetime.now().astimezone(),
    )
    db.add(notice)
    return notice


def push_all(db: Session, *, day: str | None = None) -> dict[str, int]:
    """给所有启用账号推今日简报，返回推送/跳过计数。

    单人失败不影响其他人 —— 一个人没绑定 MES 权限，不该让全厂收不到简报。
    """
    target = day or date.today().isoformat()
    users = list(db.scalars(select(User)))
    pushed = 0
    skipped = 0
    failed = 0
    for user in users:
        if not getattr(user, "enabled", 1):
            continue
        try:
            if push_for_user(db, user=user, day=target) is None:
                skipped += 1
            else:
                pushed += 1
        except Exception:  # noqa: BLE001 - 单个人失败要记下来但不能中断
            failed += 1
            logger.exception("brief push failed", extra={"user_id": getattr(user, "id", "")})
    return {"pushed": pushed, "skipped": skipped, "failed": failed, "day": target}
