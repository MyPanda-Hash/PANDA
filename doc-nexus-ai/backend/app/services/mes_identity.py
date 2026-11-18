"""MES 身份直通：AI 直接认 MES 签发的 JWT，不再走 ticket 换会话。

背景（2026-11-17 产品决定）：AI 助理与 MES 合并成一套账号体系。
- MES 是唯一身份源，签发 HS256 JWT（subject = 用户名，密钥 MES_JWT_SECRET）；
- AI 用同一密钥验签即可拿到「你是谁」，不需要 ticket，也不需要自己的登录页；
- 「你能做什么」不写死在 AI 里：每次按需向 MES 取 /auth/userinfo 与 /auth/perms，
  这样角色、面板权限、账套调整后在 AI 侧立刻生效。

为什么还要查库里的 User：AI 侧很多业务（待办、群聊、会话）都有 user_id 外键，
需要一行对应用户才能落库。这行只是「身份镜像」，不再承载权限判断。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import jwt
from sqlalchemy import select

from app.core.config import settings
from app.models.auth import User

logger = logging.getLogger(__name__)

# MES 权限结果缓存：同一 token 在 60 秒内复用，避免每个接口都打一次 MES
_PROFILE_TTL = 60.0
_profile_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class MesIdentityError(Exception):
    """MES 身份不可用（未配置密钥、票据无效、MES 不可达）。"""


def enabled() -> bool:
    return bool((settings.mes_jwt_secret or "").strip())


def verify_token(token: str) -> str:
    """验 MES JWT，返回用户名；失败抛 MesIdentityError。"""
    if not enabled():
        raise MesIdentityError("未配置 MES_JWT_SECRET，无法直连 MES 身份")
    try:
        # 算法由 MES 侧决定：jjwt 在密钥 ≥64 字节时自动用 HS512，
        # 所以这里放开 HMAC 家族三种（同一把密钥，安全性等价），
        # 写死 HS256 会直接判「票据无效」（2026-11-17 实测踩过）。
        payload = jwt.decode(
            token,
            settings.mes_jwt_secret.strip(),
            algorithms=["HS256", "HS384", "HS512"],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        raise MesIdentityError(f"MES 身份票据无效：{exc}") from exc
    username = str(payload.get("sub") or "").strip()
    if not username:
        raise MesIdentityError("MES 身份票据缺少用户名")
    return username


def profile_of(token: str) -> dict[str, Any]:
    """向 MES 取实时身份与权限（60 秒缓存）。

    拿不到就抛错 —— 权限判断宁可不放行，也不能默认给管理员。
    """
    cached = _profile_cache.get(token)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1]

    base = (settings.mes_auth_base_url or "").rstrip("/")
    if not base:
        raise MesIdentityError("未配置 MES 地址")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=6.0, trust_env=False) as client:
            info_resp = client.get(f"{base}/api/auth/userinfo", headers=headers)
            if info_resp.status_code >= 400:
                raise MesIdentityError(f"MES 用户信息返回 {info_resp.status_code}")
            info = (info_resp.json() or {}).get("data") or {}
            perms: dict[str, Any] = {}
            try:
                perms_resp = client.get(f"{base}/api/auth/perms", headers=headers)
                if perms_resp.status_code < 400:
                    perms = (perms_resp.json() or {}).get("data") or {}
            except httpx.HTTPError:
                # 权限接口挂了不影响「你是谁」，只是这份请求没有面板清单
                logger.warning("mes perms unavailable, fallback to userinfo only")
    except httpx.HTTPError as exc:
        raise MesIdentityError(f"MES 不可达：{exc}") from exc

    profile = {
        "username": str(info.get("userName") or "").strip(),
        "real_name": str(info.get("realName") or "").strip(),
        "role_code": str(info.get("roleCode") or perms.get("roleCode") or "").strip(),
        "is_admin": bool(info.get("isAdmin") or perms.get("isAdmin")),
        "visible_panels": perms.get("visiblePanels") or [],
        "approve_panels": perms.get("approvePanels") or [],
        "factory_code": str(info.get("factoryCode") or "").strip(),
    }
    _profile_cache[token] = (now + _PROFILE_TTL, profile)
    return profile


def mirror_user(db, username: str, profile: dict[str, Any]) -> User:
    """取（或建）AI 侧的身份镜像行，供待办/会话等外键使用。

    不写权限：role 只用来做「是不是管理员」的粗判，真正的权限判断一律看 profile。
    """
    user = db.scalar(select(User).where(User.username == username))
    if user is not None:
        return user
    from datetime import datetime, timezone

    user = User(
        id=f"usr_mes_{username}",
        username=username,
        display_name=profile.get("real_name") or username,
        password_hash="!",  # 不允许本地密码登录：身份由 MES 统一
        role="admin" if profile.get("is_admin") else "user",
        status="active",
        first_login=False,
        auth_version=1,
        failed_attempts=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    return user
