"""外部通知渠道与群目标的管理端配置；所有密钥均不回显。"""
from __future__ import annotations

import json
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update

from app.api.deps import admin_user
from app.api.notification_channels import _claim_operation, _missing, _target
from app.core.database import transaction
from app.core.security import now
from app.models.auth import User
from app.models.notification_delivery import (
    NotificationConnection, NotificationTarget, NotificationTargetGrant,
)
from app.repositories import audit
from app.schemas.notification_channels import (
    ConnectionCreate, ConnectionPatch, GrantRequest, GroupTargetCreate, GroupTargetPatch, RequestId,
)
from app.services.notification_channels import (
    NotificationSecretError, encrypt_secret, fingerprint,
)

router = APIRouter(prefix="/admin", tags=["admin-notifications"])


def _config(kind: str, config: dict, credential: dict) -> tuple[str, str]:
    # 不允许透传 URL、header 或额外协议字段，webhook 永远由适配器拼接固定官方地址。
    if kind == "smtp":
        if set(config) != {"host", "port", "username", "sender", "tls_mode"} or set(credential) != {"password"}:
            raise HTTPException(422, "SMTP 配置字段不正确")
        host, port = config["host"], config["port"]
        if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host) or not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535 or config["tls_mode"] not in ("implicit", "starttls"):
            raise HTTPException(422, "SMTP 主机、端口或 TLS 模式无效")
        if not isinstance(config["username"], str) or not config["username"].strip() or not isinstance(config["sender"], str) or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", config["sender"]) or not isinstance(credential["password"], str) or not credential["password"]:
            raise HTTPException(422, "SMTP 身份配置无效")
    elif kind in ("dingtalk_group", "wecom_group"):
        required = {"access_token", "secret"} if kind == "dingtalk_group" else {"access_token"}
        if config or set(credential) != required or not all(isinstance(credential[k], str) and credential[k] for k in required):
            raise HTTPException(422, "群机器人凭据字段不正确")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", credential["access_token"]):
            raise HTTPException(422, "群机器人 token 格式无效")
    else:
        raise HTTPException(422, "通知渠道类型无效")
    return json.dumps(config, ensure_ascii=False, sort_keys=True), json.dumps(credential, ensure_ascii=False, sort_keys=True)


def _connection(item: NotificationConnection) -> dict:
    return {"id": item.id, "name": item.name, "kind": item.kind, "config": json.loads(item.config_json),
            "status": item.status, "revision": item.revision, "credential_configured": bool(item.credential_ciphertext),
            "credential_fingerprint": item.credential_fingerprint}


@router.get("/notification-connections")
def list_connections(_: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        return {"items": [_connection(item) for item in db.scalars(select(NotificationConnection).order_by(NotificationConnection.created_at))]}


@router.post("/notification-connections", status_code=201)
def create_connection(payload: ConnectionCreate, admin: User = Depends(admin_user)) -> dict:
    config, credential = _config(payload.kind, payload.config, payload.credential)
    with transaction() as db:
        item_id, fresh = _claim_operation(db, admin.id, "connection_create", payload.request_id,
            {"name": payload.name, "kind": payload.kind, "config": payload.config, "credential": payload.credential, "status": payload.status},
            f"nco_{uuid4().hex}")
        if not fresh:
            existing = db.get(NotificationConnection, item_id)
            if existing is None:
                raise HTTPException(409, "通知创建记录已变化")
            return _connection(existing)
        try:
            cipher, version = encrypt_secret(credential)
        except NotificationSecretError:
            raise HTTPException(503, "通知密钥未配置，无法启用渠道") from None
        stamp = now()
        item = NotificationConnection(id=item_id, name=payload.name, kind=payload.kind,
            config_json=config, credential_ciphertext=cipher, credential_key_version=version,
            credential_fingerprint=fingerprint(credential), status=payload.status, revision=1, created_at=stamp, updated_at=stamp)
        db.add(item)
        audit.record(db, admin.id, "notification_connection_created", item.id, "success")
        return _connection(item)

@router.patch("/notification-connections/{connection_id}")
def patch_connection(connection_id: str, payload: ConnectionPatch, admin: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        item = db.get(NotificationConnection, connection_id)
        if item is None:
            raise HTTPException(404, "通知渠道不存在")
        _, fresh = _claim_operation(db, admin.id, "connection_patch", payload.request_id,
            {"connection_id": connection_id, **payload.model_dump(exclude={"request_id"}, exclude_unset=True)}, connection_id)
        if not fresh:
            return _connection(item)
        # 条件 UPDATE 是数据库层 CAS；并发两个不同 request_id 不能覆盖同一版本。
        claimed = db.execute(update(NotificationConnection).where(
            NotificationConnection.id == item.id, NotificationConnection.revision == payload.revision,
        ).values(revision=NotificationConnection.revision + 1).returning(NotificationConnection.id)
        .execution_options(synchronize_session=False))
        if claimed.scalar_one_or_none() is None:
            raise HTTPException(409, "通知渠道版本已变化")
        db.refresh(item)
        config = payload.config if payload.config is not None else json.loads(item.config_json)
        if payload.credential is not None:
            _, credential = _config(item.kind, config, payload.credential)
            try:
                item.credential_ciphertext, item.credential_key_version = encrypt_secret(credential)
            except NotificationSecretError:
                raise HTTPException(503, "通知密钥未配置，无法修改渠道") from None
            item.credential_fingerprint = fingerprint(credential)
        elif payload.config is not None:
            # 验证配置但不解密已有凭据。
            placeholder = {"password": "validation-only"} if item.kind == "smtp" else {"access_token": "0" * 32}
            if item.kind == "dingtalk_group":
                placeholder["secret"] = "validation-only"
            _config(item.kind, config, placeholder)
        if payload.name is not None:
            item.name = payload.name
        if payload.status is not None:
            if payload.status == "active":
                try:
                    from app.services.notification_channels import decrypt_secret
                    decrypt_secret(item.credential_ciphertext, item.credential_key_version)
                except NotificationSecretError:
                    raise HTTPException(503, "通知密钥未配置，无法启用渠道") from None
            item.status = payload.status
        item.config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
        item.updated_at = now()
        # 连接变更必须让所有旧目标快照失效。
        for target in db.scalars(select(NotificationTarget).where(NotificationTarget.connection_id == item.id)):
            target.revision += 1
            target.updated_at = item.updated_at
        audit.record(db, admin.id, "notification_connection_updated", item.id, "success")
        return _connection(item)


@router.get("/notification-targets")
def list_group_targets(_: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        items = list(db.scalars(select(NotificationTarget).where(NotificationTarget.owner_user_id.is_(None)).order_by(NotificationTarget.created_at)))
        return {"items": [{**_target(item), "grants": list(db.scalars(select(NotificationTargetGrant.user_id).where(NotificationTargetGrant.target_id == item.id)))} for item in items]}


@router.post("/notification-targets", status_code=201)
def create_group_target(payload: GroupTargetCreate, admin: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        item_id, fresh = _claim_operation(db, admin.id, "group_create", payload.request_id,
            {"connection_id": payload.connection_id, "label": payload.label, "consent": True}, f"nta_{uuid4().hex}")
        if not fresh:
            existing = db.get(NotificationTarget, item_id)
            if existing is None:
                raise HTTPException(409, "通知创建记录已变化")
            return _target(existing)
        connection = db.get(NotificationConnection, payload.connection_id)
        if connection is None or connection.kind not in ("dingtalk_group", "wecom_group") or connection.status != "active":
            raise HTTPException(422, "请选择已启用的群通知渠道")
        try:
            cipher, version = encrypt_secret(connection.id)
        except NotificationSecretError:
            raise HTTPException(503, "通知密钥未配置") from None
        stamp = now()
        item = NotificationTarget(id=item_id, connection_id=connection.id, kind=connection.kind,
            owner_user_id=None, label=payload.label, address_ciphertext=cipher, address_key_version=version,
            address_mask="群机器人", verified_at=stamp, consent_at=stamp, status="active", revision=1,
            created_at=stamp, updated_at=stamp)
        db.add(item)
        audit.record(db, admin.id, "notification_target_created", item.id, "success")
        return _target(item)


@router.patch("/notification-targets/{target_id}")
def patch_group_target(target_id: str, payload: GroupTargetPatch, admin: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        item = db.get(NotificationTarget, target_id)
        if item is None or item.owner_user_id is not None:
            raise _missing()
        _, fresh = _claim_operation(db, admin.id, "group_patch", payload.request_id,
            {"target_id": target_id, **payload.model_dump(exclude={"request_id"}, exclude_unset=True)}, target_id)
        if not fresh:
            return _target(item)
        claimed = db.execute(update(NotificationTarget).where(
            NotificationTarget.id == item.id, NotificationTarget.revision == payload.revision,
        ).values(revision=NotificationTarget.revision + 1).returning(NotificationTarget.id)
        .execution_options(synchronize_session=False))
        if claimed.scalar_one_or_none() is None:
            raise HTTPException(409, "通知目标版本已变化")
        db.refresh(item)
        if payload.label is not None:
            item.label = payload.label
        if payload.status is not None:
            item.status = payload.status
        item.updated_at = now()
        audit.record(db, admin.id, "notification_target_updated", item.id, "success")
        return _target(item)


@router.post("/notification-targets/{target_id}/grants")
def add_group_grant(target_id: str, payload: GrantRequest, admin: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        _, fresh = _claim_operation(db, admin.id, "grant_add", payload.request_id,
            {"target_id": target_id, "user_id": payload.user_id}, target_id)
        if not fresh:
            existing = db.get(NotificationTarget, target_id)
            return {"target_id": target_id, "user_id": payload.user_id, "revision": existing.revision if existing else 0}
        item = db.get(NotificationTarget, target_id)
        user = db.get(User, payload.user_id)
        if item is None or item.owner_user_id is not None or item.kind == "email":
            raise _missing()
        if user is None or user.status != "active":
            raise HTTPException(422, "授权用户不可用")
        if db.get(NotificationTargetGrant, (target_id, user.id)) is None:
            db.add(NotificationTargetGrant(target_id=target_id, user_id=user.id, created_at=now()))
            item.revision += 1
            item.updated_at = now()
            audit.record(db, admin.id, "notification_grant_created", item.id, "success")
        return {"target_id": target_id, "user_id": user.id, "revision": item.revision}


@router.delete("/notification-targets/{target_id}/grants/{user_id}")
def revoke_group_grant(target_id: str, user_id: str, payload: RequestId, admin: User = Depends(admin_user)) -> dict:
    with transaction() as db:
        _, fresh = _claim_operation(db, admin.id, "grant_revoke", payload.request_id,
            {"target_id": target_id, "user_id": user_id}, target_id)
        if not fresh:
            item = db.get(NotificationTarget, target_id)
            return {"target_id": target_id, "user_id": user_id, "revision": item.revision if item else 0}
        item = db.get(NotificationTarget, target_id)
        if item is None or item.owner_user_id is not None:
            raise _missing()
        grant = db.get(NotificationTargetGrant, (target_id, user_id))
        if grant:
            db.delete(grant)
            item.revision += 1
            item.updated_at = now()
            audit.record(db, admin.id, "notification_grant_revoked", item.id, "success")
        return {"target_id": target_id, "user_id": user_id, "revision": item.revision}
