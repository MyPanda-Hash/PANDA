from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException
from jwt.exceptions import PyJWTError

from app.core.config import settings
from app.core.security import decode_access_token
from app.core.database import SessionLocal
from app.models.auth import User
from app.repositories.users import get_by_id
from app.services.authorization import PermissionService

logger = logging.getLogger(__name__)

ACCESS_COOKIE = "doc_nexus_access"


def _mes_user(token: str) -> User | None:
    """MES 身份直通：验 MES JWT 并取实时权限，成功则返回镜像用户。

    合并账号体系后，浏览器带的是 MES 的登录态；这里不再要求 AI 有自己的会话。
    失败返回 None，让上层继续按原逻辑报 401（未配置密钥的老部署行为不变）。
    """
    from app.services import mes_identity

    if not mes_identity.enabled():
        return None
    try:
        username = mes_identity.verify_token(token)
        profile = mes_identity.profile_of(token)
    except mes_identity.MesIdentityError as exc:
        # 不是 MES 票据（可能是 AI 自己的 token 过期），交给原来的分支处理
        logger.debug("mes identity skipped: %s", exc)
        return None
    with SessionLocal() as db:
        user = mes_identity.mirror_user(db, username or profile.get("username") or "", profile)
        db.commit()
        db.expunge(user)
    # 权限用实时值覆盖到对象上，供 admin_user 等依赖直接判断
    user.mes_is_admin = bool(profile.get("is_admin"))
    user.mes_role_code = profile.get("role_code") or ""
    user.mes_visible_panels = profile.get("visible_panels") or []
    user.mes_approve_panels = profile.get("approve_panels") or []
    return user


def _user_from_token(token: str, *, allow_first_login: bool = False) -> User:
    try:
        payload = decode_access_token(token)
    except PyJWTError:
        # AI 自己的 token 解不开：试试是不是 MES 签发的（合并账号体系后的正常路径）
        mes_user = _mes_user(token)
        if mes_user is not None:
            return mes_user
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录") from None
    with SessionLocal() as db:
        user = get_by_id(db, payload.get("sub"))
        if not user or user.status != "active":
            raise HTTPException(status_code=401, detail="账户当前不可用")
        try:
            token_auth_version = int(payload.get("auth_version", 0))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=401, detail="登录状态已失效，请重新登录"
            ) from None
        if token_auth_version != user.auth_version:
            raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")
        if settings.linkmind_enabled and user.first_login and not allow_first_login:
            raise HTTPException(status_code=403, detail="首次登录必须先修改密码")
        db.expunge(user)
        return user


def current_user(authorization: Annotated[str | None, Header()] = None) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    return _user_from_token(authorization[7:])
def current_user_allow_first_login(
    authorization: Annotated[str | None, Header()] = None,
    access_cookie: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None,
) -> User:
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    elif access_cookie:
        token = access_cookie.strip()
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    return _user_from_token(token, allow_first_login=True)


def current_user_from_bearer_or_cookie(
    authorization: Annotated[str | None, Header()] = None,
    access_cookie: Annotated[str | None, Cookie(alias=ACCESS_COOKIE)] = None,
) -> User:
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    elif access_cookie:
        token = access_cookie.strip()
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    return _user_from_token(token)


def admin_user(
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> User:
    """管理员校验。

    依赖 bearer_or_cookie 而不是 current_user：浏览器端是 Cookie 会话，
    只认 Authorization 头会让新工作台里所有管理页都 401
    （实测：/admin/model-endpoints、/branding 都踩过这个坑）。
    """
    # MES 直通的身份：管理员判断以 MES 的实时权限为准（账号等级跟 MES 走）
    if getattr(user, "mes_is_admin", None) is not None:
        if not user.mes_is_admin:
            raise HTTPException(status_code=403, detail="需要管理员权限")
        return user
    with SessionLocal() as db:
        PermissionService.require_system_permission(db, user, "user:manage")
    return user


def usage_viewer(user: User = Depends(current_user)) -> User:
    with SessionLocal() as db:
        PermissionService.require_system_permission(db, user, "usage:view_all")
    return user
