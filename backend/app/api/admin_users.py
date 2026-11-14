from __future__ import annotations

import secrets
from typing import Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import admin_user, usage_viewer
from app.core.config import settings
from app.core.database import transaction
from app.core.security import iso
from app.models.auth import User
from app.repositories import audit, system_config, users
from app.schemas.auth import (
    AdminCreateUserRequest,
    AdminResetPasswordRequest,
    DisplayNameUpdateRequest,
    InviteRequest,
)
from app.schemas.linkmind import (
    LinkMindCreatedKeyResponse,
    LinkMindKeyCreateRequest,
    LinkMindKeyListResponse,
    LinkMindKeyResponse,
    LinkMindKeyUpdateRequest,
)
from app.services import auth, linkmind
from app.services.invite_origins import invite_origin_payload

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


@router.get("/invite-origins")
def get_invite_origins(_admin: User = Depends(admin_user)) -> dict:
    """Return configured frontend origins so invite QR codes can use a LAN URL."""
    return invite_origin_payload()


def user_snapshot(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "status": user.status,
        "first_login": user.first_login,
        "created_at": iso(user.created_at),
        "api_key": user.api_key,
    }


def _generate_temp_password() -> str:
    # Readable temporary password that satisfies min_length=8.
    return f"Tmp-{secrets.token_urlsafe(9)}"


def _assert_mutable_target(
    db: Session, admin: User, target: User, *, action: str
) -> None:
    if target.id == admin.id:
        raise HTTPException(status_code=409, detail=f"不能{action}当前登录账号")
    if users.is_bootstrap_admin(db, target, settings.bootstrap_username):
        raise HTTPException(status_code=409, detail=f"不能{action}系统引导管理员")
    if target.status == "deleted" and action != "删除":
        raise HTTPException(status_code=409, detail="该用户已删除")


def _raise_linkmind_error(exc: linkmind.LinkMindError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("")
def list_users(
    limit: int = 50, offset: int = 0, _: User = Depends(admin_user)
) -> dict:
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)
    with transaction() as db:
        items = list(
            db.scalars(
                select(User)
                .order_by(User.created_at, User.id)
                .offset(safe_offset)
                .limit(safe_limit + 1)
            )
        )
        has_more = len(items) > safe_limit
        return {
            "items": [user_snapshot(item) for item in items[:safe_limit]],
            "next_offset": safe_offset + safe_limit if has_more else None,
            "next_cursor": None,
        }


@router.post("", status_code=201)
def create_user(
    payload: AdminCreateUserRequest, admin: User = Depends(admin_user)
) -> dict:
    with transaction() as db:
        action, existing = users.resolve_create_target(db, payload)
        if action == "conflict":
            raise HTTPException(status_code=409, detail="账号或邮箱已存在")
        invite_payload = InviteRequest.model_validate(
            payload.model_dump(exclude={"password"})
        )
        if payload.password:
            if action == "restore" and existing is not None:
                user, raw_api_key = users.restore_with_password(
                    db, existing, invite_payload, payload.password
                )
                audit.record(db, admin.id, "user_restored", user.id, "success")
            else:
                user, raw_api_key = users.create_with_password(
                    db, invite_payload, payload.password
                )
                audit.record(db, admin.id, "user_created", user.id, "success")
            result = {
                "mode": "password",
                "user": user_snapshot(user),
                "user_id": user.id,
                "temporary_password": payload.password,
                "api_key": raw_api_key,
                "invite_token": None,
                "invite_path": None,
                "expires_at": None,
            }
        elif action == "restore" and existing is not None:
            user, raw_token, expires_at, raw_api_key = users.restore_invited(
                db, existing, invite_payload
            )
            audit.record(db, admin.id, "user_restored", user.id, "success")
            result = {
                "mode": "invite",
                "user": user_snapshot(user),
                "user_id": user.id,
                "temporary_password": None,
                "api_key": raw_api_key,
                "invite_token": raw_token,
                "invite_path": f"/invites/{raw_token}",
                "expires_at": expires_at,
            }
        else:
            user, raw_token, expires_at, raw_api_key = users.create_invited(
                db, invite_payload
            )
            audit.record(db, admin.id, "user_invited", user.id, "success")
            result = {
                "mode": "invite",
                "user": user_snapshot(user),
                "user_id": user.id,
                "temporary_password": None,
                "api_key": raw_api_key,
                "invite_token": raw_token,
                "invite_path": f"/invites/{raw_token}",
                "expires_at": expires_at,
            }
        user_id = user.id

    try:
        linkmind.sync_user_by_id(user_id)
        if linkmind.enabled():
            key = linkmind.create_key(user_id)
            result["linkmind_key"] = {
                **linkmind.key_payload(key, can_reveal=True),
                "token": key.token,
            }
        else:
            result["linkmind_key"] = None
    except linkmind.LinkMindError as exc:
        with transaction() as db:
            audit.record(
                db,
                admin.id,
                "linkmind_key_provision_failed",
                user_id,
                "failed",
            )
        _raise_linkmind_error(exc)
    return result


@router.post("/{user_id}:disable")
def disable_user(user_id: str, admin: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        user = users.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        _assert_mutable_target(db, admin, user, action="停用")

    try:
        if linkmind.enabled():
            linkmind.disable_all_keys(user_id)
    except linkmind.LinkMindError as exc:
        _raise_linkmind_error(exc)

    with transaction() as db:
        user = users.get_by_id(db, user_id)
        assert user is not None
        user.status = "disabled"
        user.linkmind_login_status = "logged_out"
        users.revoke_sessions(db, user.id)
        linkmind.sync_user(db, user)
        audit_event_id = audit.record(db, admin.id, "user_disabled", user.id, "success")
        return {"user": user_snapshot(user), "audit_event_id": audit_event_id}


@router.post("/{user_id}:enable")
def enable_user(user_id: str, admin: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        user = users.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user.status == "deleted":
            raise HTTPException(
                status_code=409, detail="已删除用户无法直接启用，请重新邀请"
            )
        if user.id == admin.id:
            raise HTTPException(status_code=409, detail="当前账号已处于可用状态")
        user.status = "active"
        user.linkmind_login_status = "logged_out"
        user.failed_attempts = 0
        user.locked_until = None
        linkmind.sync_user(db, user)
        audit_event_id = audit.record(db, admin.id, "user_enabled", user.id, "success")
        return {"user": user_snapshot(user), "audit_event_id": audit_event_id}


@router.post("/{user_id}:revoke-sessions")
def revoke_sessions(user_id: str, admin: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        user = users.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        count = users.revoke_sessions(db, user.id)
        user.linkmind_login_status = "logged_out"
        linkmind.sync_user(db, user)
        audit_event_id = audit.record(
            db, admin.id, "user_sessions_revoked", user.id, "success"
        )
        return {"revoked_session_count": count, "audit_event_id": audit_event_id}


@router.post("/{user_id}:reset-password")
def reset_password(
    user_id: str,
    payload: AdminResetPasswordRequest,
    admin: User = Depends(admin_user),
) -> dict:
    with transaction() as db:
        user = users.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        _assert_mutable_target(db, admin, user, action="重置密码")
        temporary = payload.new_password or _generate_temp_password()
        users.set_password(db, user, temporary, require_change=True)
        user.linkmind_login_status = "logged_out"
        revoked = users.revoke_sessions(db, user.id)
        linkmind.sync_user(db, user)
        audit_event_id = audit.record(
            db, admin.id, "user_password_reset", user.id, "success"
        )
        return {
            "user": user_snapshot(user),
            "temporary_password": temporary,
            "revoked_session_count": revoked,
            "audit_event_id": audit_event_id,
        }


@router.patch("/{user_id}/display-name")
def update_user_display_name(
    user_id: str,
    payload: DisplayNameUpdateRequest,
    admin: User = Depends(admin_user),
) -> dict:
    with transaction() as db:
        user = users.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user.status == "deleted":
            raise HTTPException(status_code=409, detail="该用户已删除")
        auth.update_display_name(db, user, payload.display_name)
        audit_event_id = audit.record(
            db, admin.id, "display_name_changed", user.id, "success"
        )
        return {"user": user_snapshot(user), "audit_event_id": audit_event_id}


@router.delete("/{user_id}")
def delete_user(user_id: str, admin: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        user = users.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        _assert_mutable_target(db, admin, user, action="删除")
        if user.role == "admin" and user.status != "deleted":
            admin_count = (
                db.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(User.role == "admin", User.status != "deleted")
                )
                or 0
            )
            if admin_count <= 1:
                raise HTTPException(status_code=409, detail="不能删除唯一的管理员")
        hard_delete = user.status == "deleted"

    try:
        if linkmind.enabled():
            if hard_delete:
                linkmind.revoke_all_keys(user_id)
                linkmind.delete_shared_user(user_id)
            else:
                linkmind.disable_all_keys(user_id)
    except linkmind.LinkMindError as exc:
        _raise_linkmind_error(exc)

    with transaction() as db:
        user = users.get_by_id(db, user_id)
        assert user is not None
        if hard_delete:
            snapshot = user_snapshot(user)
            users.hard_delete(db, user, reassign_to_user_id=admin.id)
            audit_event_id = audit.record(
                db, admin.id, "user_purged", snapshot["id"], "success"
            )
            return {"user": None, "purged": True, "audit_event_id": audit_event_id}
        users.soft_delete(db, user)
        linkmind.sync_user(db, user)
        audit_event_id = audit.record(db, admin.id, "user_deleted", user.id, "success")
        return {
            "user": user_snapshot(user),
            "purged": False,
            "audit_event_id": audit_event_id,
        }


@router.get(
    "/{user_id}/linkmind-api-keys",
    response_model=LinkMindKeyListResponse,
)
def list_linkmind_api_keys(
    user_id: str,
    _: User = Depends(admin_user),
) -> LinkMindKeyListResponse:
    with transaction() as db:
        if not users.get_by_id(db, user_id):
            raise HTTPException(status_code=404, detail="用户不存在")
    try:
        items = [
            LinkMindKeyResponse(**linkmind.key_payload(key, can_reveal=can_reveal))
            for key, can_reveal in linkmind.list_keys(user_id)
        ]
    except linkmind.LinkMindError as exc:
        _raise_linkmind_error(exc)
    return LinkMindKeyListResponse(items=items)


@router.post(
    "/{user_id}/linkmind-api-keys:ensure",
    status_code=204,
    response_model=None,
)
def ensure_linkmind_api_key(
    user_id: str,
    admin: User = Depends(admin_user),
) -> None:
    with transaction() as db:
        user = users.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user.status != "active":
            raise HTTPException(status_code=409, detail="当前用户不可创建 LinkMind Key")
        stored_config = system_config.get_all(db)
        setup_completed = stored_config.get("onboarding_completed") == "1" or bool(
            (stored_config.get("app_name") or "").strip()
            and (stored_config.get("pi_persona_name") or "").strip()
        )
        should_initialize_box = (
            user_id == admin.id
            and not setup_completed
            and stored_config.get(linkmind.LINKMIND_BOX_INITIALIZED_CONFIG_KEY)
            != linkmind.LINKMIND_BOX_INITIALIZATION_COMPLETE
        )
    box_initialized = False
    try:
        linkmind.sync_user_by_id(user_id)
        linkmind.ensure_user_key(user_id)
        if should_initialize_box:
            box_initialized = linkmind.initialize_box_once(admin.id) is not None
    except linkmind.LinkMindError as exc:
        _raise_linkmind_error(exc)
    with transaction() as db:
        audit.record(db, admin.id, "linkmind_key_ensured", user_id, "success")
        if box_initialized:
            audit.record(
                db,
                admin.id,
                "linkmind_box_initialized",
                admin.id,
                "success",
            )


@router.post(
    "/{user_id}/linkmind-api-keys",
    response_model=LinkMindCreatedKeyResponse,
    status_code=201,
)
def create_linkmind_api_key(
    user_id: str,
    payload: LinkMindKeyCreateRequest,
    admin: User = Depends(admin_user),
) -> LinkMindCreatedKeyResponse:
    with transaction() as db:
        user = users.get_by_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user.status != "active":
            raise HTTPException(status_code=409, detail="当前用户不可创建 LinkMind Key")
    try:
        linkmind.sync_user_by_id(user_id)
        key = linkmind.create_key(user_id, remark=payload.remark)
    except linkmind.LinkMindError as exc:
        _raise_linkmind_error(exc)
    with transaction() as db:
        audit.record(db, admin.id, "linkmind_key_created", user_id, "success")
    return LinkMindCreatedKeyResponse(
        **linkmind.key_payload(key, can_reveal=True),
        token=key.token or "",
    )


@router.patch(
    "/{user_id}/linkmind-api-keys/{key_id}",
    response_model=LinkMindKeyResponse,
)
def update_linkmind_api_key(
    user_id: str,
    key_id: int,
    payload: LinkMindKeyUpdateRequest,
    admin: User = Depends(admin_user),
) -> LinkMindKeyResponse:
    try:
        key = linkmind.update_key(user_id, key_id, remark=payload.remark)
    except linkmind.LinkMindError as exc:
        _raise_linkmind_error(exc)
    with transaction() as db:
        audit.record(db, admin.id, "linkmind_key_updated", user_id, "success")
    return LinkMindKeyResponse(**linkmind.key_payload(key))


@router.post(
    "/{user_id}/linkmind-api-keys/{key_id}:revoke",
    response_model=LinkMindKeyResponse,
)
def revoke_linkmind_api_key(
    user_id: str,
    key_id: int,
    admin: User = Depends(admin_user),
) -> LinkMindKeyResponse:
    try:
        key = linkmind.revoke_key(user_id, key_id)
    except linkmind.LinkMindError as exc:
        _raise_linkmind_error(exc)
    with transaction() as db:
        audit.record(db, admin.id, "linkmind_key_revoked", user_id, "success")
    return LinkMindKeyResponse(**linkmind.key_payload(key))


@router.get("/{user_id}/linkmind-token-usage/overview")
def linkmind_token_usage_overview(
    user_id: str,
    range: Literal["today", "week", "month", "all"] = "month",
    _: User = Depends(usage_viewer),
) -> dict:
    try:
        return linkmind.get_client().token_usage(user_id, "overview", range=range)
    except linkmind.LinkMindError as exc:
        _raise_linkmind_error(exc)


@router.get("/{user_id}/linkmind-token-usage/details")
def linkmind_token_usage_details(
    user_id: str,
    range: Literal["today", "week", "month", "all"] = "month",
    page: int = 1,
    page_size: int = 20,
    _: User = Depends(usage_viewer),
) -> dict:
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(status_code=422, detail="分页参数无效")
    try:
        return linkmind.get_client().token_usage(
            user_id,
            "details",
            range=range,
            page=page,
            pageSize=page_size,
        )
    except linkmind.LinkMindError as exc:
        _raise_linkmind_error(exc)


@router.get("/{user_id}/linkmind-token-usage/sessions")
def linkmind_token_usage_sessions(
    user_id: str,
    range: Literal["today", "week", "month", "all"] = "month",
    page: int = 1,
    page_size: int = 20,
    _: User = Depends(usage_viewer),
) -> dict:
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(status_code=422, detail="分页参数无效")
    try:
        return linkmind.get_client().token_usage(
            user_id,
            "sessions",
            range=range,
            page=page,
            pageSize=page_size,
        )
    except linkmind.LinkMindError as exc:
        _raise_linkmind_error(exc)


@router.get("/{user_id}/linkmind-token-usage/guard")
def linkmind_token_usage_guard(
    user_id: str,
    _: User = Depends(usage_viewer),
) -> dict:
    try:
        return linkmind.get_client().token_usage(user_id, "guard")
    except linkmind.LinkMindError as exc:
        _raise_linkmind_error(exc)
