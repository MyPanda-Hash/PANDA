"""管理员「以某账号视角查看」。

为什么需要：简报/待办的数字是按「当前登录人」取的，光改前端标签只能换文案、
换不了数据——三个视角看到的其实是同一份（2026-11-17 用户反馈"切换后权限与可见混乱"）。

这里的边界：
- 只有管理员能用；普通账号传了 view_as 直接 403，不静默忽略；
- 被查看的账号必须是系统里真实存在的用户；
- 只影响「取数身份」，不改变调用者的权限，也不写任何数据。
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.auth import User


class ViewAsDenied(RuntimeError):
    """不是管理员，或不存在的被查看账号。"""


def resolve(db, user: User, view_as: str = "") -> User:
    """返回「这次请求该用谁的身份取数」。

    空 view_as 或调用者不是管理员 → 原样返回调用者（保持原有行为）。
    """
    target = (view_as or "").strip()
    if not target:
        return user

    if not _is_admin(user):
        raise ViewAsDenied("只有管理员可以切换查看账号")

    if target in (getattr(user, "username", ""), getattr(user, "id", "")):
        return user

    row = db.scalars(
        select(User).where((User.username == target) | (User.id == target)).limit(1)
    ).first()
    if row is None:
        raise ViewAsDenied("账号不存在：" + target)
    return row


def _is_admin(user: User) -> bool:
    # 与 deps.admin_user 同一套判定：以 MES 实时权限为准
    flag = getattr(user, "mes_is_admin", None)
    if flag is not None:
        return bool(flag)
    return str(getattr(user, "role", "") or "") == "admin"


def describe(user: User) -> dict:
    """给前端展示的账号摘要。"""
    return {
        "id": getattr(user, "id", ""),
        "username": getattr(user, "username", ""),
        "displayName": str(
            getattr(user, "display_name", "") or getattr(user, "username", "") or ""
        ),
        "role": str(getattr(user, "role", "") or ""),
        "isAdmin": _is_admin(user),
    }