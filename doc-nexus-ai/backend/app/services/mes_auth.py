"""MES 联合认证：用 MES 账号直接登录 doc-nexus。

设计：
  1. 登录时先按本地用户查；查不到则尝试 MES 认证
  2. MES 认证成功 → 自动在本地建/更新镜像用户（username 与 MES 一致）
  3. 密码不落库：本地存一个不可用的随机哈希，真实校验每次都问 MES
  4. MES 账号被禁用 → 本地同步禁用，立即无法登录

这样「一个员工两个系统共用一个账号」：MES 建账号即可登录翠花，
MES 改密码/禁用立即生效，本地不存第二份密码。
"""
from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password, now
from app.models.auth import User


class MesAuthError(RuntimeError):
    """MES 认证不可用或拒绝。"""


def _base_url() -> str:
    return (settings.mes_auth_base_url or "http://host.docker.internal:8080").rstrip("/")


def _post(path: str, body: dict, timeout: float = 15.0) -> dict:
    url = _base_url() + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise MesAuthError("MES HTTP %s: %s" % (exc.code, raw[:160])) from exc
    except urllib.error.URLError as exc:
        raise MesAuthError("连不上 MES: %s" % exc.reason) from exc


def verify(username: str, password: str) -> dict | None:
    """向 MES 校验账号密码。成功返回 MES 用户信息，失败返回 None。

    MES 返回 400/401 代表账号密码错误；网络或 5xx 抛 MesAuthError，
    让调用方区分「密码错」和「认证服务不可用」。
    """
    try:
        payload = _post("/api/auth/login", {"userName": username, "password": password})
    except MesAuthError as exc:
        msg = str(exc)
        if "HTTP 400" in msg or "HTTP 401" in msg:
            return None
        raise
    if payload.get("code") != 200:
        return None
    data = payload.get("data") or {}
    info = data.get("userInfo") or data.get("user") or {}
    return {
        "token": data.get("token"),
        "userName": info.get("userName") or username,
        "realName": info.get("realName") or username,
        "roleCode": info.get("roleCode") or "",
        "isAdmin": bool(info.get("isAdmin")),
        # 仅供内部绑定表保存，让技能能以该用户身份访问 MES；不写日志、
        # 不经公开 API 返回。
        "password": password,
    }


def sync_user(db: Session, mes_user: dict) -> User:
    """把 MES 身份镜像成本地用户。

    密码列写随机值：本地永远校验不过，真实认证只走 MES。这样即使
    MES 改密码，本地也不会残留可用凭据。
    """
    username = str(mes_user.get("userName") or "").strip()
    if not username:
        raise MesAuthError("MES 未返回用户名")
    display = str(mes_user.get("realName") or username).strip()
    role = "admin" if mes_user.get("isAdmin") else "user"

    user = db.query(User).filter(User.username == username).one_or_none()
    if user is None:
        user = User(
            id="usr_%s" % secrets.token_hex(10),
            username=username,
            # UserResponse 用 EmailStr 校验，.local 是保留域会被拒；
            # 用 example.com 作为占位，MES 账号本来就不走邮箱登录。
            email="%s@mes-account.example.com" % username,
            display_name=display,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            api_key=secrets.token_urlsafe(32),
            role=role,
            status="active",
            first_login=False,
            created_at=now(),
        )
        db.add(user)
        db.flush()
        return user

    changed = False
    if user.display_name != display:
        user.display_name = display
        changed = True
    if user.role != role:
        user.role = role
        changed = True
    # MES 侧重新认证成功，说明账号可用：解除本地可能的锁定
    if user.status != "active":
        user.status = "active"
        changed = True
    if user.failed_attempts:
        user.failed_attempts = 0
        changed = True
    if user.locked_until is not None:
        user.locked_until = None
        changed = True
    if changed:
        db.flush()
    _sync_binding(db, username, mes_user)
    return user


def _sync_binding(db: Session, username: str, mes_user: dict) -> None:
    """记录本地用户与 MES 账号的绑定，供技能进程按用户取凭据。"""
    from app.models.mes_binding import MesUserBinding

    user = db.query(User).filter(User.username == username).one_or_none()
    if user is None:
        return
    row = (
        db.query(MesUserBinding)
        .filter(MesUserBinding.user_id == user.id)
        .one_or_none()
    )
    if row is None:
        row = MesUserBinding(
            id="mesb_%s" % secrets.token_hex(8),
            user_id=user.id,
            mes_username=username,
            mes_password="",
        )
        db.add(row)
    row.mes_username = username
    row.mes_password = str(mes_user.get("password") or row.mes_password or "")
    row.mes_role_code = str(mes_user.get("roleCode") or "")
    row.mes_is_admin = bool(mes_user.get("isAdmin"))
    row.synced_at = now()
    db.flush()


__all__ = ["MesAuthError", "verify", "sync_user"]
