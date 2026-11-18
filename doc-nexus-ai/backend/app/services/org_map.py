"""人力地图：AI 账号 → MES 账号 → 部门归属（只读，带缓存）。

群成员面板原来只知道「谁在群里、是什么群角色」，看不出「他在哪个部门」。
部门数据的唯一来源是 MES 的 `sys_dept` / `sys_user.dept_id`，这里按项目既有
约定走 MES 开放接口（`GET /api/open/org/map`），不去直连对方的库。

三条纪律，缺一条都会在真实使用里出问题：

1. **降级不报错**：MES 没开开放接口、没配令牌或临时挂了，成员列表照常显示，
   只是没有部门；「查不到部门」绝不能把群聊打成 500。
2. **一次拿全量**：部门树和人是同一份组织快照，缓存 60 秒。若逐人查，一个 6 人的群
   就是 6 次 MES 请求，而这份数据一小时也不会变。
3. **映射有回退**：AI 账号与 MES 账号靠 `mes_user_bindings` 关联；没绑定的账号按
   同名 `username` 试一次（种子账号 admin / sales / planner … 两边同名），
   仍然对不上就归到「未分配部门」，不猜。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import User
from app.models.mes_binding import MesUserBinding
from app.services.mes_client import MesClient, MesWatchError

logger = logging.getLogger(__name__)

#: 组织快照的缓存时长。部门调整是低频动作，一分钟足够新。
CACHE_TTL_SECONDS = 60.0
#: 拉取失败后的冷却时间：MES 不可用时别让每个请求都去等超时。
FAILED_TTL_SECONDS = 15.0

NO_DEPARTMENT: dict[str, Any] = {
    "dept_id": None,
    "dept_name": "",
    "dept_path": "",
    "position": "",
}

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {
    "expires_at": 0.0,
    "departments": [],
    "users": {},
}


def invalidate() -> None:
    """部门调整后立即失效（测试与后台任务用）。"""
    with _LOCK:
        _CACHE["expires_at"] = 0.0


def _normalize_departments(rows: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            dept_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        parent = row.get("parentId", 0)
        try:
            parent_id = int(parent) if parent is not None else 0
        except (TypeError, ValueError):
            parent_id = 0
        out.append(
            {
                "id": dept_id,
                "parent_id": parent_id,
                "name": str(row.get("name") or ""),
                "path": str(row.get("path") or ""),
                "depth": int(row.get("depth") or 0),
                "sort": int(row.get("sort") or 0),
            }
        )
    return out


def _normalize_users(rows: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("userName") or "").strip()
        if not name:
            continue
        dept_id = row.get("deptId")
        try:
            dept_id = int(dept_id) if dept_id is not None else None
        except (TypeError, ValueError):
            dept_id = None
        out[name] = {
            "user_name": name,
            "real_name": str(row.get("realName") or ""),
            "dept_id": dept_id,
            "dept_name": str(row.get("deptName") or ""),
            "dept_path": str(row.get("deptPath") or ""),
            "position": str(row.get("roleName") or ""),
        }
    return out


def snapshot(*, client: MesClient | None = None, ttl: float = CACHE_TTL_SECONDS) -> dict[str, Any]:
    """组织快照 `{departments, users}`；拿不到就返回空壳，不抛。"""
    with _LOCK:
        if _CACHE["expires_at"] > time.monotonic():
            return {"departments": _CACHE["departments"], "users": _CACHE["users"]}

    departments: list[dict[str, Any]] = []
    users: dict[str, dict[str, Any]] = {}
    try:
        raw = (client or MesClient()).org_map()
        departments = _normalize_departments(raw.get("departments"))
        users = _normalize_users(raw.get("users"))
        failed = not departments and not users
    except (MesWatchError, OSError, ValueError) as exc:
        logger.warning("org map unavailable, degrade to empty: %s", exc)
        failed = True

    with _LOCK:
        _CACHE["departments"] = departments
        _CACHE["users"] = users
        _CACHE["expires_at"] = time.monotonic() + (FAILED_TTL_SECONDS if failed else ttl)
    return {"departments": departments, "users": users}


def mes_username_of(db: Session, user: User) -> str:
    """这个人对应的 MES 账号名：优先绑定关系，其次同名回退。"""
    row = db.scalars(
        select(MesUserBinding).where(MesUserBinding.user_id == user.id)
    ).first()
    bound = (row.mes_username or "").strip() if row is not None else ""
    return bound or (user.username or "").strip()


def fields_of(db: Session, user: User, *, snap: dict[str, Any] | None = None) -> dict[str, Any]:
    """这个人的部门字段；查不到就是空部门（前端显示「未分配部门」）。"""
    name = mes_username_of(db, user)
    if not name:
        return dict(NO_DEPARTMENT)
    users = (snap or snapshot())["users"]
    row = users.get(name)
    if row is None:
        return dict(NO_DEPARTMENT)
    return {
        "dept_id": row["dept_id"],
        "dept_name": row["dept_name"],
        "dept_path": row["dept_path"],
        "position": row["position"],
    }


def group_map(
    db: Session, member_ids: list[str], *, snap: dict[str, Any] | None = None
) -> dict[str, Any]:
    """群的「人力地图」：部门树上挂成员，另有未分配名单。

    返回的 `members` 是 user_id → 部门字段，前端直接按 id 取，不必二次合并。
    """
    snap = snap or snapshot()
    placement: dict[int, list[str]] = {}
    members: dict[str, dict[str, Any]] = {}
    unassigned: list[str] = []
    for user_id in member_ids:
        user = db.get(User, user_id)
        if user is None:
            continue
        info = fields_of(db, user, snap=snap)
        members[user_id] = info
        dept_id = info.get("dept_id")
        if dept_id is None:
            unassigned.append(user_id)
        else:
            placement.setdefault(int(dept_id), []).append(user_id)

    departments = [
        {**row, "member_ids": placement.get(row["id"], [])}
        for row in snap["departments"]
    ]
    return {"departments": departments, "unassigned": unassigned, "members": members}
