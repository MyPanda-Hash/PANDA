from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    as_utc,
    create_access_token,
    hash_password,
    hash_token,
    now,
    verify_password,
)
from app.models.auth import Session as LoginSession
from app.models.auth import User
from app.repositories import audit, system_config, users
from app.schemas.auth import (
    AcceptInviteRequest,
    ChangePasswordRequest,
    LoginRequest,
    UserResponse,
)
from app.services.authorization import PermissionService
from app.services import linkmind


def public_user(
    user: User,
    *,
    avatar_url: str | None = None,
    permissions: list[str] | None = None,
    linkmind_sso_url: str | None = None,
    linkmind_enabled: bool = False,
    company_name: str = "",
    persona_name: str = "",
) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        avatar_url=avatar_url,
        role=user.role,
        first_login=user.first_login,
        permissions=permissions or [],
        linkmind_sso_url=linkmind_sso_url,
        linkmind_enabled=linkmind_enabled,
        company_name=company_name,
        persona_name=persona_name,
    )


def _avatar_url(user: User) -> str | None:
    """头像地址：文件存在才算有（不加数据库列，换头像立刻生效）。"""
    from app.api.user_avatar import avatar_url

    return avatar_url(user.id)


def public_user_with_permissions(db: Session, user: User) -> UserResponse:
    return public_user(
        user,
        avatar_url=_avatar_url(user),
        permissions=PermissionService.list_permission_codes(db, user),
        linkmind_sso_url=linkmind.sso_url(db, user),
        linkmind_enabled=linkmind.enabled(),
        company_name=system_config.get_value(db, "app_name").strip(),
        persona_name=system_config.get_value(db, "pi_persona_name").strip(),
    )


def issue_refresh(db: Session, user: User) -> str:
    raw = secrets.token_urlsafe(48)
    created = now()
    db.add(
        LoginSession(
            id=f"ses_{secrets.token_urlsafe(10)}",
            user_id=user.id,
            refresh_hash=hash_token(raw),
            expires_at=created + timedelta(days=settings.refresh_ttl_days),
            created_at=created,
        )
    )
    db.flush()
    return raw


def _mes_login(db: Session, payload: LoginRequest, request_id: str | None):
    """Try MES as the identity provider.

    Returns a local User on success, None when MES rejects the credentials.
    Raises MesAuthError only for infrastructure failures so the caller can
    distinguish "wrong password" from "MES unreachable".
    """
    from app.services import mes_auth

    mes_user = mes_auth.verify(payload.username, payload.password)
    if mes_user is None:
        audit.record(db, None, "login_failed", payload.username, "mes_rejected",
                     request_id)
        db.commit()
        return None
    user = mes_auth.sync_user(db, mes_user)
    audit.record(db, user.id, "login_mes", user.id, "success", request_id)
    db.flush()
    return user


def login(
    db: Session, payload: LoginRequest, request_id: str | None
) -> tuple[str, str, UserResponse]:
    user = users.get_by_login_identifier(db, payload.username)
    if not user:
        # MES 联合认证：本地没有这个账号时，问 MES。MES 认识就镜像过来。
        if settings.mes_auth_enabled:
            from app.services import mes_auth

            try:
                user = _mes_login(db, payload, request_id)
            except mes_auth.MesAuthError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="MES 认证服务暂不可用，请稍后重试或联系管理员",
                ) from exc
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误"
            )
    if user.locked_until and as_utc(user.locked_until) > now():
        raise HTTPException(status_code=423, detail="登录尝试过多，请稍后重试")
    password_ok = verify_password(user.password_hash, payload.password)
    if not password_ok and settings.mes_auth_enabled:
        # MES 是权威密码源：本地哈希只在 MES 不可用时兜底，MES 改密码后
        # 旧密码必须立即失效。镜像用户的本地哈希是随机值，永远走这里。
        from app.services import mes_auth

        try:
            mes_user = mes_auth.verify(payload.username, payload.password)
        except mes_auth.MesAuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MES 认证服务暂不可用，请稍后重试或联系管理员",
            ) from exc
        if mes_user is not None:
            user = mes_auth.sync_user(db, mes_user)
            password_ok = True
    if not password_ok:
        user.failed_attempts += 1
        if user.failed_attempts >= 5:
            user.locked_until = now() + timedelta(minutes=15)
        audit.record(
            db,
            None,
            "login_failed",
            user.id,
            "locked" if user.locked_until else "rejected",
            request_id,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误"
        )
    protected_bootstrap_setup = (
        settings.environment.strip().lower() in {"production", "prod"}
        and "bootstrap_password" in settings.model_fields_set
    )
    if (
        protected_bootstrap_setup
        and user.first_login
        and users.is_bootstrap_admin(db, user, settings.bootstrap_username)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员首次初始化必须使用安装时生成的初始化链接",
        )
    if user.status != "active":
        raise HTTPException(status_code=403, detail="账户当前不可用，请联系系统管理员")
    user.failed_attempts = 0
    user.locked_until = None
    if settings.linkmind_enabled and user.first_login:
        user.linkmind_login_status = "logged_out"
    else:
        user.linkmind_login_status = "logged_in"
    refresh = issue_refresh(db, user)
    audit.record(db, user.id, "login_succeeded", user.id, "success", request_id)
    linkmind.sync_user(db, user)
    return (
        create_access_token(user.id, user.role, user.auth_version),
        refresh,
        public_user_with_permissions(db, user),
    )


def accept_invitation(
    db: Session, token: str, payload: AcceptInviteRequest
) -> UserResponse:
    if payload.password != payload.password_confirmation:
        raise HTTPException(status_code=422, detail="两次输入的密码不一致")
    invitation = users.invitation_by_token(db, token)
    if (
        not invitation
        or invitation.accepted_at
        or invitation.revoked_at
        or as_utc(invitation.expires_at) <= now()
    ):
        raise HTTPException(status_code=404, detail="邀请已失效或不存在")
    user = users.get_by_id(db, invitation.user_id)
    user.password_hash = hash_password(payload.password)
    user.auth_version += 1
    user.first_login = False
    # Accepting an invite always enables the account for immediate login.
    user.status = "active"
    user.failed_attempts = 0
    user.locked_until = None
    invitation.accepted_at = now()
    linkmind.sync_user(db, user)
    audit.record(db, user.id, "invitation_accepted", user.id, "success")
    return public_user_with_permissions(db, user)


def change_password(
    db: Session,
    user: User,
    payload: ChangePasswordRequest,
    *,
    keep_refresh_hash: str | None = None,
) -> UserResponse:
    if payload.new_password != payload.password_confirmation:
        raise HTTPException(status_code=422, detail="两次输入的密码不一致")
    if not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if verify_password(user.password_hash, payload.new_password):
        raise HTTPException(status_code=422, detail="新密码不能与当前密码相同")
    user.password_hash = hash_password(payload.new_password)
    user.auth_version += 1
    user.first_login = False
    if settings.linkmind_enabled:
        user.linkmind_login_status = "logged_in"
    users.revoke_sessions(
        db,
        user.id,
        except_refresh_hash=keep_refresh_hash,
    )
    audit.record(db, user.id, "password_changed", user.id, "success")
    linkmind.sync_user(db, user)
    return public_user_with_permissions(db, user)


def update_display_name(db: Session, user: User, display_name: str) -> UserResponse:
    normalized = display_name.strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="显示名不能为空")
    user.display_name = normalized
    audit.record(db, user.id, "display_name_changed", user.id, "success")
    linkmind.sync_user(db, user)
    return public_user_with_permissions(db, user)
