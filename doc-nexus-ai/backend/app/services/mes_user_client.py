"""按「当前这个人」访问 MES：权限相适配的关键一层。

群助手与语音助手原来都用 MES_WATCH 那个账号查数据——那是管理员视角，
普通仓管问一句就能查到财务数据。这里改成用 `mes_user_bindings` 里绑定的
MES 账号登录，MES 自己按它的角色裁决；AI 侧再按登录返回的
`visiblePanels` / `approvePanels` 提前挡一次，避免把「你没权限」说成「没数据」。

绑定缺失时不偷偷降级成管理员视角，而是明确让对方去绑定。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sqlalchemy import select

from app.core.database import transaction
from app.models.mes_binding import MesUserBinding
from app.services.mes_client import MesClient, MesWatchError

logger = logging.getLogger(__name__)

PROFILE_TTL_SECONDS = 300.0


class MesPermissionError(RuntimeError):
    """没有绑定，或该账号看不到这个面板。"""


@dataclass
class Access:
    panels: set[str]
    approve: set[str]
    is_admin: bool
    role_code: str
    real_name: str
    loaded_at: float


_CACHE: dict[str, Access] = {}


def binding_of(user_id: str) -> MesUserBinding | None:
    with transaction() as db:
        row = db.scalars(
            select(MesUserBinding).where(MesUserBinding.user_id == user_id)
        ).first()
        if row is None:
            return None
        db.expunge(row)
        return row


def client_for_user(user_id: str) -> tuple[MesClient, MesUserBinding]:
    """这个人自己的 MES 客户端（用他的账号登录，查到什么就是他能看的）。"""
    binding = binding_of(user_id)
    if binding is None or not binding.mes_username or not binding.mes_password:
        raise MesPermissionError(
            "当前账号还没有绑定 MES：请先在 MES 里点「AI 数字员工」入口完成一次绑定，"
            "我才能按你的权限查数据"
        )
    return (
        MesClient(username=binding.mes_username, password=binding.mes_password),
        binding,
    )


def access_of(user_id: str, client: MesClient | None = None) -> Access:
    """该账号在 MES 里的可见/可审批面板（缓存 5 分钟）。"""
    cached = _CACHE.get(user_id)
    if cached and time.monotonic() - cached.loaded_at < PROFILE_TTL_SECONDS:
        return cached
    if client is None:
        client, _ = client_for_user(user_id)
    try:
        profile = client.profile()
    except MesWatchError as exc:
        raise MesPermissionError(f"读取 MES 权限失败：{exc}") from exc
    access = Access(
        panels={str(code) for code in (profile.get("visiblePanels") or [])},
        approve={str(code) for code in (profile.get("approvePanels") or [])},
        is_admin=bool(profile.get("isAdmin")),
        role_code=str(profile.get("roleCode") or ""),
        real_name=str(profile.get("realName") or ""),
        loaded_at=time.monotonic(),
    )
    _CACHE[user_id] = access
    return access


def invalidate(user_id: str) -> None:
    """改过绑定或权限后强制重读。"""
    _CACHE.pop(user_id, None)


def can_query(panel: str, user_id: str) -> tuple[bool, str]:
    """能不能查这个面板。"""
    try:
        access = access_of(user_id)
    except MesPermissionError as exc:
        return False, str(exc)
    if access.is_admin or panel in access.panels:
        return True, ""
    return False, f"你的 MES 账号没有「{panel}」的查看权限，这条我不能替你看"


def can_approve(panel: str, user_id: str) -> tuple[bool, str]:
    """能不能看这个面板的审批待办。"""
    try:
        access = access_of(user_id)
    except MesPermissionError as exc:
        return False, str(exc)
    if access.is_admin or panel in access.approve:
        return True, ""
    return False, f"你的 MES 账号对「{panel}」没有审批权"


def allowed_panels(user_id: str, candidates: list[str] | tuple[str, ...]) -> list[str]:
    """从候选面板里挑出这个人能看的；读不到权限就返回空表（宁可少查，不可越权）。"""
    try:
        access = access_of(user_id)
    except MesPermissionError:
        return []
    if access.is_admin:
        return list(candidates)
    return [panel for panel in candidates if panel in access.panels]
