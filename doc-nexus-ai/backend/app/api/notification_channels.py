"""外部通知配置与个人目标；所有密钥和验证码均不回显。"""
from __future__ import annotations

import hmac
import json
import secrets
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.api.deps import current_user
from app.core.database import transaction
from app.core.security import as_utc, iso, now
from app.models.auth import User
from app.models.notification_delivery import (
    NotificationConnection, NotificationOperation, NotificationPreference, NotificationTarget,
    NotificationTargetGrant, NotificationVerificationChallenge,
)
from app.repositories import audit
from app.schemas.notification_channels import (
    EmailTargetCreate, PreferencePatch, RequestId, VerifyRequest,
)
from app.services.notification_channels import (
    NotificationSecretError, challenge_digest, encrypt_secret, operation_digests, send_verification_email,
)

router = APIRouter(tags=["notification-channels"])


def _missing() -> HTTPException:
    return HTTPException(404, "通知目标不存在")


def _claim_operation(db, actor_id: str, action: str, request_id: str, payload: dict, result_id: str) -> tuple[str, bool]:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        digest, candidate_digests = operation_digests(canonical)
    except NotificationSecretError:
        raise HTTPException(503, "通知密钥不可用") from None
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(NotificationOperation)
    elif dialect == "sqlite":
        stmt = sqlite_insert(NotificationOperation)
    else:
        raise RuntimeError("通知操作只支持 SQLite/PostgreSQL")
    inserted = db.execute(stmt.values(
        id=f"nop_{uuid4().hex}", actor_user_id=actor_id, action=action,
        request_id=request_id, payload_digest=digest, result_id=result_id, created_at=now(),
    ).on_conflict_do_nothing(index_elements=["actor_user_id", "action", "request_id"]).returning(NotificationOperation.id)).scalar_one_or_none()
    if inserted:
        return result_id, True
    previous = db.scalar(select(NotificationOperation).where(
        NotificationOperation.actor_user_id == actor_id,
        NotificationOperation.action == action,
        NotificationOperation.request_id == request_id,
    ))
    if previous is None or not any(hmac.compare_digest(previous.payload_digest, candidate) for candidate in candidate_digests):
        raise HTTPException(409, "请求编号已用于其他通知操作")
    return previous.result_id, False


def _target(item: NotificationTarget) -> dict:
    return {"id": item.id, "connection_id": item.connection_id, "label": item.label, "kind": item.kind,
            "address_mask": item.address_mask, "status": item.status, "revision": item.revision,
            "verified_at": iso(item.verified_at) if item.verified_at else None,
            "consent_at": iso(item.consent_at) if item.consent_at else None}


def _challenge_response(challenge: NotificationVerificationChallenge) -> dict | JSONResponse:
    if challenge.delivery_status == "accepted":
        return {"expires_at": iso(challenge.expires_at), "delivery_status": "accepted"}
    if challenge.delivery_status == "unknown":
        return JSONResponse(status_code=202, content={
            "expires_at": iso(challenge.expires_at),
            "delivery_status": "unknown",
            "message": "验证码可能已发出，请检查收件箱；不要重复请求。",
        })
    # 进程中断、明确失败或安全可重试的发送错误均不能重放成已发送。
    raise HTTPException(503, "验证码暂未成功发送，请稍后使用新请求编号重试")


def _active_targets(db, user_id: str) -> list[NotificationTarget]:
    grants = select(NotificationTargetGrant.target_id).where(NotificationTargetGrant.user_id == user_id)
    return list(db.scalars(select(NotificationTarget).join(NotificationConnection, NotificationConnection.id == NotificationTarget.connection_id).where(
        NotificationTarget.status == "active", NotificationConnection.status == "active",
        NotificationTarget.verified_at.is_not(None), NotificationTarget.consent_at.is_not(None),
        (NotificationTarget.owner_user_id == user_id) | ((NotificationTarget.owner_user_id.is_(None)) & NotificationTarget.id.in_(grants)),
    ).order_by(NotificationTarget.created_at, NotificationTarget.id)))


@router.get("/notification-targets")
def available_targets(user: User = Depends(current_user)) -> dict:
    with transaction() as db:
        return {"items": [{k: row[k] for k in ("id", "label", "kind", "revision", "status")} for item in _active_targets(db, user.id) for row in [_target(item)]]}


@router.get("/me/notification-targets")
def my_targets(user: User = Depends(current_user)) -> dict:
    with transaction() as db:
        owned = list(db.scalars(select(NotificationTarget).where(NotificationTarget.owner_user_id == user.id).order_by(NotificationTarget.created_at)))
        granted = list(db.scalars(select(NotificationTarget).join(NotificationTargetGrant, NotificationTargetGrant.target_id == NotificationTarget.id).where(NotificationTargetGrant.user_id == user.id).order_by(NotificationTarget.created_at)))
        return {"items": [_target(item) for item in owned + granted]}


@router.get("/me/notification-connections")
def available_email_connections(_: User = Depends(current_user)) -> dict:
    with transaction() as db:
        return {"items": [{"id": item.id, "name": item.name} for item in db.scalars(
            select(NotificationConnection).where(NotificationConnection.kind == "smtp", NotificationConnection.status == "active")
            .order_by(NotificationConnection.name, NotificationConnection.id)
        )]}


@router.post("/me/notification-targets", status_code=201)
def create_email_target(payload: EmailTargetCreate, user: User = Depends(current_user)) -> dict:
    with transaction() as db:
        item_id, fresh = _claim_operation(db, user.id, "email_create", payload.request_id,
            {"connection_id": payload.connection_id, "label": payload.label, "address": str(payload.address).lower(), "consent": True},
            f"nta_{uuid4().hex}")
        if not fresh:
            existing = db.get(NotificationTarget, item_id)
            if existing is None:
                raise HTTPException(409, "通知创建记录已变化")
            return _target(existing)
        connection = db.get(NotificationConnection, payload.connection_id)
        if connection is None or connection.kind != "smtp" or connection.status != "active":
            raise HTTPException(422, "请选择已启用的邮件渠道")
        try:
            cipher, version = encrypt_secret(str(payload.address).lower())
        except NotificationSecretError:
            raise HTTPException(503, "通知密钥未配置") from None
        address = str(payload.address)
        local, domain = address.rsplit("@", 1)
        mask = f"{local[:1]}***@{domain}"
        stamp = now()
        item = NotificationTarget(id=item_id, connection_id=connection.id, kind="email", owner_user_id=user.id,
            label=payload.label, address_ciphertext=cipher, address_key_version=version,
            address_mask=mask, verified_at=None, consent_at=stamp, status="pending", revision=1,
            created_at=stamp, updated_at=stamp)
        db.add(item)
        audit.record(db, user.id, "notification_target_created", item.id, "success")
        return _target(item)


@router.post("/me/notification-targets/{target_id}/verification", response_model=None)
def issue_email_challenge(target_id: str, payload: RequestId, user: User = Depends(current_user)) -> dict | JSONResponse:
    # 挑战先持久化，再尝试发信；网络结果不确定时用户仍可用收到的码完成验证。
    with transaction() as db:
        target = db.get(NotificationTarget, target_id)
        if target is None or target.owner_user_id != user.id or target.kind != "email":
            raise _missing()
        connection = db.get(NotificationConnection, target.connection_id)
        if target.status != "pending" or connection is None or connection.status != "active":
            raise HTTPException(409, "通知目标不可验证")
        challenge_id, fresh = _claim_operation(db, user.id, "email_verification_issue", payload.request_id,
            {"target_id": target_id}, f"nvc_{uuid4().hex}")
        if not fresh:
            previous = db.get(NotificationVerificationChallenge, challenge_id)
            if previous is None:
                raise HTTPException(409, "验证码请求记录已变化")
            return _challenge_response(previous)
        latest = db.scalar(select(NotificationVerificationChallenge).where(NotificationVerificationChallenge.target_id == target.id).order_by(NotificationVerificationChallenge.created_at.desc()).limit(1))
        stamp = now()
        if latest and as_utc(latest.created_at) > stamp - timedelta(seconds=60):
            raise HTTPException(429, "请稍后再获取验证码")
        daily = db.scalar(select(func.count()).select_from(NotificationVerificationChallenge).where(
            NotificationVerificationChallenge.target_id == target.id,
            NotificationVerificationChallenge.created_at >= stamp - timedelta(days=1)))
        owner_daily = db.scalar(select(func.count()).select_from(NotificationVerificationChallenge).join(
            NotificationTarget, NotificationTarget.id == NotificationVerificationChallenge.target_id).where(
            NotificationTarget.owner_user_id == user.id,
            NotificationVerificationChallenge.created_at >= stamp - timedelta(days=1)))
        if (daily or 0) >= 10 or (owner_daily or 0) >= 30:
            raise HTTPException(429, "今日验证码请求次数已达上限")
        code = f"{secrets.randbelow(1_000_000):06d}"
        try:
            digest = challenge_digest(target.id, target.revision, code, target.address_key_version)
        except NotificationSecretError:
            raise HTTPException(503, "通知密钥不可用") from None
        challenge = NotificationVerificationChallenge(id=challenge_id, target_id=target.id,
            code_hash=digest,
            expires_at=stamp + timedelta(minutes=10), attempts=0, used_at=None, delivery_status="pending", created_at=stamp)
        db.add(challenge)
        audit.record(db, user.id, "notification_verification_issued", target.id, "success")
        # db 关闭后 ORM 字段仍可读，但不向客户端返回验证码或目标地址。
    result = send_verification_email(connection, target, code)
    with transaction() as db:
        persisted = db.get(NotificationVerificationChallenge, challenge_id)
        if persisted is None:
            raise HTTPException(503, "验证码请求未保存")
        persisted.delivery_status = result.status
        response = _challenge_response(persisted) if result.status in ("accepted", "unknown") else None
    if response is None:
        raise HTTPException(503, "验证码暂未成功发送，请稍后使用新请求编号重试")
    return response


@router.post("/me/notification-targets/{target_id}/verify")
def verify_email_target(target_id: str, payload: VerifyRequest, user: User = Depends(current_user)) -> dict:
    error = None
    response = None
    with transaction() as db:
        item = db.get(NotificationTarget, target_id)
        if item is None or item.owner_user_id != user.id or item.kind != "email":
            raise _missing()
        if item.status != "pending":
            _, fresh = _claim_operation(db, user.id, "email_verify", payload.request_id,
                {"target_id": target_id, "code": payload.code}, target_id)
            if not fresh:
                return _target(item)
            raise HTTPException(409, "通知目标不可验证")
        challenge = db.scalar(select(NotificationVerificationChallenge).where(NotificationVerificationChallenge.target_id == item.id).order_by(NotificationVerificationChallenge.created_at.desc()).limit(1))
        stamp = now()
        if challenge is None:
            error = HTTPException(422, "验证码无效或已过期")
        else:
            # UPDATE 条件同时限制次数/有效期/使用状态；SQLite 写锁和 PG 行锁均序列化并发尝试。
            claimed = db.execute(update(NotificationVerificationChallenge).where(
                NotificationVerificationChallenge.id == challenge.id,
                NotificationVerificationChallenge.attempts < 5,
                NotificationVerificationChallenge.used_at.is_(None),
                NotificationVerificationChallenge.expires_at > stamp,
            ).values(attempts=NotificationVerificationChallenge.attempts + 1).returning(NotificationVerificationChallenge.id).execution_options(synchronize_session=False))
            if claimed.scalar_one_or_none() != challenge.id:
                error = HTTPException(422, "验证码无效或已过期")
            else:
                try:
                    supplied = challenge_digest(item.id, item.revision, payload.code, item.address_key_version)
                except NotificationSecretError:
                    error = HTTPException(503, "通知密钥不可用")
                    supplied = ""
                if error is not None or not hmac.compare_digest(challenge.code_hash, supplied):
                    error = error or HTTPException(422, "验证码无效或已过期")
                else:
                    _, fresh = _claim_operation(db, user.id, "email_verify", payload.request_id,
                        {"target_id": target_id, "code": payload.code}, target_id)
                    if not fresh:
                        return _target(item)
                    challenge.used_at = stamp
                    item.verified_at = stamp
                    item.status = "active"
                    item.revision += 1
                    item.updated_at = stamp
                    audit.record(db, user.id, "notification_target_verified", item.id, "success")
                    response = _target(item)
    if error:
        raise error
    assert response is not None
    return response


@router.patch("/me/notification-targets/{target_id}")
def disable_email_target(target_id: str, payload: RequestId, user: User = Depends(current_user)) -> dict:
    with transaction() as db:
        item = db.get(NotificationTarget, target_id)
        if item is None or item.owner_user_id != user.id or item.kind != "email":
            raise _missing()
        _, fresh = _claim_operation(db, user.id, "email_disable", payload.request_id,
            {"target_id": target_id}, target_id)
        if not fresh:
            return _target(item)
        item.status = "disabled"
        item.revision += 1
        item.updated_at = now()
        audit.record(db, user.id, "notification_target_disabled", item.id, "success")
        return _target(item)


@router.get("/me/notification-preferences")
def get_preferences(user: User = Depends(current_user)) -> dict:
    with transaction() as db:
        item = db.get(NotificationPreference, user.id)
        return {"external_enabled": bool(item and item.external_enabled)}


@router.patch("/me/notification-preferences")
def patch_preferences(payload: PreferencePatch, user: User = Depends(current_user)) -> dict:
    with transaction() as db:
        _, fresh = _claim_operation(db, user.id, "preference_patch", payload.request_id,
            {"external_enabled": payload.external_enabled}, user.id)
        if not fresh:
            existing = db.get(NotificationPreference, user.id)
            return {"external_enabled": bool(existing and existing.external_enabled)}
        item = db.get(NotificationPreference, user.id)
        if item is None:
            item = NotificationPreference(user_id=user.id, external_enabled=payload.external_enabled, updated_at=now())
            db.add(item)
        else:
            item.external_enabled = payload.external_enabled
            item.updated_at = now()
        audit.record(db, user.id, "notification_preference_updated", user.id, "success")
        return {"external_enabled": item.external_enabled}
