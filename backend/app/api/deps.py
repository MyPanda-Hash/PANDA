from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException
from jwt.exceptions import PyJWTError

from app.core.config import settings
from app.core.security import decode_access_token
from app.core.database import SessionLocal
from app.models.auth import User
from app.repositories.users import get_by_id
from app.services.authorization import PermissionService

ACCESS_COOKIE = "doc_nexus_access"


def _user_from_token(token: str, *, allow_first_login: bool = False) -> User:
    try:
        payload = decode_access_token(token)
    except PyJWTError:
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


def admin_user(user: User = Depends(current_user)) -> User:
    with SessionLocal() as db:
        PermissionService.require_system_permission(db, user, "user:manage")
    return user


def usage_viewer(user: User = Depends(current_user)) -> User:
    with SessionLocal() as db:
        PermissionService.require_system_permission(db, user, "usage:view_all")
    return user
