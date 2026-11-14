from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    ACCESS_COOKIE,
    current_user,
    current_user_allow_first_login,
    current_user_from_bearer_or_cookie,
)
from app.core.config import settings
from app.core.database import transaction
from app.core.security import (
    as_utc,
    create_access_token,
    hash_password,
    hash_token,
    iso,
    now,
    verify_password,
)
from app.models.auth import Session as LoginSession
from app.models.auth import User
from app.repositories import audit, users
from app.schemas.auth import (
    AcceptInviteRequest,
    ApiKeyItem,
    ApiKeyResponse,
    ChangePasswordRequest,
    DisplayNameUpdateRequest,
    InvitationInspectResponse,
    LoginRequest,
    SetupPasswordRequest,
    SetupStatusResponse,
    TokenResponse,
    UserResponse,
)
from app.schemas.linkmind import (
    LinkMindKeyListResponse,
    LinkMindKeyResponse,
    LinkMindTokenRevealResponse,
)
from app.services import auth, linkmind, mes_sso
from app.services.auth import public_user_with_permissions

router = APIRouter(prefix="/auth", tags=["auth"])

def _require_user(db: Session, user_id: str) -> User:
    user = users.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.get("/setup-status", response_model=SetupStatusResponse)
def setup_status() -> SetupStatusResponse:
    with transaction() as db:
        user = users.get_bootstrap_admin(db, settings.bootstrap_username)
        required = user is None or user.first_login
        return SetupStatusResponse(
            required=required,
            username=user.username if user is not None else settings.bootstrap_username,
            display_name=user.display_name if user is not None else "系统管理员",
            linkmind_enabled=settings.linkmind_enabled,
        )


@router.post("/setup", response_model=TokenResponse)
def setup(
    payload: SetupPasswordRequest,
    response: Response,
) -> TokenResponse:
    if payload.new_password != payload.password_confirmation:
        raise HTTPException(status_code=422, detail="两次输入的密码不一致")

    # Identity settings are intentionally collected only after setup creates
    # an authenticated admin session. Passwords and installer credentials
    # must never enter the LLM-backed onboarding conversation.

    with transaction() as db:
        user = users.get_bootstrap_admin(db, settings.bootstrap_username)
        if user is None:
            raise HTTPException(status_code=404, detail="管理员账号不存在")
        if not user.first_login:
            raise HTTPException(status_code=409, detail="管理员初始化已完成，请先登录")
        if verify_password(user.password_hash, payload.new_password):
            raise HTTPException(status_code=422, detail="新密码不能与安装初始密码相同")

        user.password_hash = hash_password(payload.new_password)
        user.auth_version += 1
        user.first_login = False
        user.failed_attempts = 0
        user.locked_until = None
        user.linkmind_login_status = "logged_in"
        refresh = auth.issue_refresh(db, user)
        public = public_user_with_permissions(db, user)
        linkmind.sync_user(db, user)
        user_id = user.id
        user_role = user.role
        user_auth_version = user.auth_version
        audit.record(db, user_id, "bootstrap_setup_completed", user_id, "success")

    access = create_access_token(user_id, user_role, user_auth_version)
    set_refresh_cookie(response, refresh)
    set_access_cookie(response, access)
    return TokenResponse(access_token=access, user=public)


def set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "doc_nexus_refresh",
        token,
        max_age=settings.refresh_ttl_days * 86400,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/auth",
    )


def set_access_cookie(response: Response, token: str) -> None:
    # Path=/ so a same-origin GET /api/files/... from the address bar carries it.
    # Not HttpOnly: the SPA also mirrors sessionStorage into this cookie.
    response.set_cookie(
        ACCESS_COOKIE,
        token,
        max_age=settings.access_ttl_minutes * 60,
        httponly=False,
        secure=False,
        samesite="lax",
        path="/",
    )


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    response: Response,
    x_request_id: Annotated[str | None, Header()] = None,
) -> TokenResponse:
    with transaction() as db:
        access, refresh, user = auth.login(db, payload, x_request_id)
    set_refresh_cookie(response, refresh)
    set_access_cookie(response, access)
    return TokenResponse(access_token=access, user=user)


@router.get("/mes-sso", response_model=None)
def mes_sso_login(ticket: str) -> RedirectResponse:
    """light-mes 点「AI 数字员工」跳进来：验票据 → 镜像用户 → 建会话 → 回对话页。

    浏览器直接跳转的入口，所以不返回 JSON：成功 302 到 CONFIG 里的对话页，
    失败抛 401 由浏览器显示文本。票据由 light-mes 用共享密钥签发，见
    app/services/mes_sso.py 的协议说明。
    """
    try:
        identity = mes_sso.verify_ticket(ticket)
    except mes_sso.MesSsoError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    from app.services import mes_auth

    with transaction() as db:
        user = mes_auth.sync_user(
            db,
            {
                "userName": identity["username"],
                "realName": identity["real_name"],
                "roleCode": identity["role_code"],
                "isAdmin": identity["is_admin"],
            },
        )
        refresh = auth.issue_refresh(db, user)
        user_id = user.id
        user_role = user.role
        user_auth_version = user.auth_version
        audit.record(db, user_id, "login_mes_sso", user_id, "success")

    access = create_access_token(user_id, user_role, user_auth_version)
    response = RedirectResponse(url=settings.mes_sso_redirect or "/chat", status_code=302)
    set_refresh_cookie(response, refresh)
    set_access_cookie(response, access)
    return response


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    response: Response,
    refresh_cookie: Annotated[str | None, Cookie(alias="doc_nexus_refresh")] = None,
) -> TokenResponse:
    if not refresh_cookie:
        raise HTTPException(status_code=401, detail="刷新会话不存在")
    with transaction() as db:
        session = db.scalar(
            select(LoginSession).where(
                LoginSession.refresh_hash == hash_token(refresh_cookie)
            )
        )
        if not session or session.revoked_at or as_utc(session.expires_at) <= now():
            raise HTTPException(status_code=401, detail="刷新会话已失效")
        user = _require_user(db, session.user_id)
        session.revoked_at = now()
        new_refresh = auth.issue_refresh(db, user)
        public = public_user_with_permissions(db, user)
    access = create_access_token(user.id, user.role, user.auth_version)
    set_refresh_cookie(response, new_refresh)
    set_access_cookie(response, access)
    return TokenResponse(access_token=access, user=public)


@router.post("/logout", status_code=204, response_model=None)
def logout(
    response: Response,
    refresh_cookie: Annotated[str | None, Cookie(alias="doc_nexus_refresh")] = None,
    user: User = Depends(current_user_allow_first_login),
) -> None:
    with transaction() as db:
        managed = _require_user(db, user.id)
        if refresh_cookie:
            session = db.scalar(
                select(LoginSession).where(
                    LoginSession.refresh_hash == hash_token(refresh_cookie),
                    LoginSession.user_id == managed.id,
                )
            )
            if session:
                session.revoked_at = now()
        managed.linkmind_login_status = "logged_out"
        linkmind.sync_user(db, managed)
        audit.record(db, managed.id, "logout", managed.id, "success")
    response.delete_cookie("doc_nexus_refresh", path="/auth")
    response.delete_cookie(ACCESS_COOKIE, path="/")


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(current_user_allow_first_login)) -> UserResponse:
    with transaction() as db:
        managed = _require_user(db, user.id)
        return public_user_with_permissions(db, managed)


@router.get("/api-key")
def get_legacy_api_key(user: User = Depends(current_user)) -> dict[str, str]:
    """Keep the original single-key contract for already-issued client keys."""
    with transaction() as db:
        managed = _require_user(db, user.id)
        if not managed.api_key:
            raise HTTPException(status_code=404, detail="API key not found")
        return {"api_key": managed.api_key}


@router.get("/api-keys", response_model=ApiKeyResponse)
def get_api_keys(user: User = Depends(current_user)) -> ApiKeyResponse:
    with transaction() as db:
        managed = _require_user(db, user.id)
        keys = users.list_api_keys(db, managed.id)
        return ApiKeyResponse(
            items=[
                ApiKeyItem(
                    id=k.id,
                    api_key=k.api_key,
                    status=k.status,
                    created_at=iso(k.created_at),
                )
                for k in keys
            ]
        )


@router.get("/linkmind-api-keys", response_model=LinkMindKeyListResponse)
def get_linkmind_api_keys(
    user: User = Depends(current_user),
) -> LinkMindKeyListResponse:
    try:
        items = [
            LinkMindKeyResponse(**linkmind.key_payload(key, can_reveal=can_reveal))
            for key, can_reveal in linkmind.list_keys(user.id)
        ]
    except linkmind.LinkMindError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return LinkMindKeyListResponse(items=items)


@router.post(
    "/linkmind-api-keys/{key_id}:reveal",
    response_model=LinkMindTokenRevealResponse,
)
def reveal_linkmind_api_key(
    key_id: int,
    user: User = Depends(current_user),
) -> LinkMindTokenRevealResponse:
    try:
        token = linkmind.reveal_key(user.id, key_id)
    except linkmind.LinkMindError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    with transaction() as db:
        audit.record(db, user.id, "linkmind_key_revealed", str(key_id), "success")
    return LinkMindTokenRevealResponse(token=token)


@router.post("/change-password", response_model=UserResponse)
def change_password(
    payload: ChangePasswordRequest,
    user: User = Depends(current_user_allow_first_login),
    refresh_cookie: Annotated[str | None, Cookie(alias="doc_nexus_refresh")] = None,
) -> UserResponse:
    with transaction() as db:
        managed_user = _require_user(db, user.id)
        return auth.change_password(
            db,
            managed_user,
            payload,
            keep_refresh_hash=hash_token(refresh_cookie) if refresh_cookie else None,
        )


@router.patch("/display-name", response_model=UserResponse)
def update_display_name(
    payload: DisplayNameUpdateRequest,
    # 群聊页走 cookie 会话，聊天页走 Bearer：两种都要认
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> UserResponse:
    with transaction() as db:
        managed_user = _require_user(db, user.id)
        return auth.update_display_name(db, managed_user, payload.display_name)


@router.get("/invitations/{token}", response_model=InvitationInspectResponse)
def inspect_invitation(token: str) -> InvitationInspectResponse:
    with transaction() as db:
        invitation = users.invitation_by_token(db, token)
        if (
            not invitation
            or invitation.accepted_at
            or invitation.revoked_at
            or as_utc(invitation.expires_at) <= now()
        ):
            raise HTTPException(status_code=404, detail="邀请已失效或不存在")
        user = _require_user(db, invitation.user_id)
        return InvitationInspectResponse(
            username=user.username,
            email=user.email,
            display_name=user.display_name,
            expires_at=iso(invitation.expires_at),
        )


@router.post("/invitations/{token}/accept", response_model=UserResponse)
def accept_invitation(token: str, payload: AcceptInviteRequest) -> UserResponse:
    with transaction() as db:
        return auth.accept_invitation(db, token, payload)
