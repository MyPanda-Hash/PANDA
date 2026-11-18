"""Atomic owner-scoped binding operations; callers own the transaction."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import as_utc, hash_token, new_token, now
from app.models.auth import User
from app.models.user_channel_bindings import (
    UserChannelBinding,
    UserChannelBindingChallenge,
    UserChannelBindingRateLimit,
)
from app.models.user_memory import UserMemoryTurn
from app.repositories import user_memory

_CHALLENGE_TTL = timedelta(minutes=10)
_CHALLENGE_LIMIT = 5
_CONSUME_LIMIT = 10


class ChannelBindingError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _digest(parts: list[object]) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verified_channel_key(channel_kind: str, *identifiers: str | int) -> str:
    if (
        channel_kind not in {"app", "aibot"}
        or not identifiers
        or any(not str(value).strip() for value in identifiers)
    ):
        raise ValueError("a verified channel requires configured identifiers")
    return _digest(["wecom", channel_kind, *identifiers])


def binding_runtime_session_id(binding: UserChannelBinding) -> str:
    return (
        "wecom-bound-"
        + _digest(
            [
                binding.owner_user_id,
                binding.channel_kind,
                binding.verified_channel_key,
                binding.external_user_id,
                binding.id,
                binding.version,
            ]
        )[:48]
    )


def _lock_active_owner(db: Session, owner_user_id: str) -> None:
    # A conditional write serializes both PostgreSQL and SQLite. FOR UPDATE
    # alone does not serialize SQLite challenge issuance or account revocation.
    changed = db.execute(
        update(User)
        .where(User.id == owner_user_id, User.status == "active")
        .values(auth_version=User.auth_version)
    )
    if changed.rowcount != 1:
        raise ChannelBindingError("binding_user_inactive", "账号不可用", 403)


def list_owned_bindings(db: Session, *, owner_user_id: str) -> list[UserChannelBinding]:
    return list(
        db.scalars(
            select(UserChannelBinding)
            .where(UserChannelBinding.owner_user_id == owner_user_id)
            .order_by(UserChannelBinding.created_at.desc(), UserChannelBinding.id)
        )
    )


def get_active_binding(
    db: Session, *, channel_kind: str, verified_channel_key: str, external_user_id: str
) -> UserChannelBinding | None:
    return db.scalar(
        select(UserChannelBinding)
        .join(User, User.id == UserChannelBinding.owner_user_id)
        .where(
            UserChannelBinding.provider == "wecom",
            UserChannelBinding.channel_kind == channel_kind,
            UserChannelBinding.verified_channel_key == verified_channel_key,
            UserChannelBinding.external_user_id == external_user_id,
            UserChannelBinding.status == "active",
            User.status == "active",
        )
        .execution_options(populate_existing=True)
    )


def get_active_binding_by_id(
    db: Session, *, owner_user_id: str, binding_id: str, version: int
) -> UserChannelBinding | None:
    return db.scalar(
        select(UserChannelBinding)
        .join(User, User.id == UserChannelBinding.owner_user_id)
        .where(
            UserChannelBinding.owner_user_id == owner_user_id,
            UserChannelBinding.id == binding_id,
            UserChannelBinding.version == version,
            UserChannelBinding.status == "active",
            User.status == "active",
        )
        .execution_options(populate_existing=True)
    )


def create_challenge(
    db: Session, *, owner_user_id: str, channel_kind: str, verified_channel_key: str
) -> tuple[str, UserChannelBindingChallenge]:
    _lock_active_owner(db, owner_user_id)
    stamp = now()
    count = (
        db.scalar(
            select(func.count())
            .select_from(UserChannelBindingChallenge)
            .where(
                UserChannelBindingChallenge.owner_user_id == owner_user_id,
                UserChannelBindingChallenge.created_at > stamp - _CHALLENGE_TTL,
            )
        )
        or 0
    )
    if count >= _CHALLENGE_LIMIT:
        raise ChannelBindingError(
            "binding_rate_limited", "绑定码申请过于频繁，请稍后重试", 429
        )
    code = new_token(24)
    challenge = UserChannelBindingChallenge(
        id=f"ubc_{new_token(16)}",
        owner_user_id=owner_user_id,
        channel_kind=channel_kind,
        verified_channel_key=verified_channel_key,
        token_hash=hash_token(code),
        expires_at=stamp + _CHALLENGE_TTL,
        created_at=stamp,
    )
    db.add(challenge)
    db.flush()
    return code, challenge


def _reserve_consume_attempt(
    db: Session, *, channel_kind: str, channel_key: str, sender: str
) -> None:
    key = _digest(["wecom", channel_kind, channel_key, sender])
    stamp = now()
    limiter = db.get(UserChannelBindingRateLimit, key)
    if limiter is None:
        try:
            with db.begin_nested():
                db.add(
                    UserChannelBindingRateLimit(
                        key=key, window_started_at=stamp, attempts=0
                    )
                )
                db.flush()
        except IntegrityError:
            pass
    db.execute(
        update(UserChannelBindingRateLimit)
        .where(
            UserChannelBindingRateLimit.key == key,
            UserChannelBindingRateLimit.window_started_at <= stamp - _CHALLENGE_TTL,
        )
        .values(window_started_at=stamp, attempts=0)
        .execution_options(synchronize_session="fetch")
    )
    reserved = db.execute(
        update(UserChannelBindingRateLimit)
        .where(
            UserChannelBindingRateLimit.key == key,
            UserChannelBindingRateLimit.attempts < _CONSUME_LIMIT,
        )
        .values(attempts=UserChannelBindingRateLimit.attempts + 1)
    )
    if reserved.rowcount != 1:
        raise ChannelBindingError(
            "binding_rate_limited", "绑定尝试过于频繁，请稍后重试", 429
        )


def consume_challenge(
    db: Session,
    *,
    code: str,
    channel_kind: str,
    verified_channel_key: str,
    external_user_id: str,
) -> UserChannelBinding:
    """Consume once; callers commit the attempt counter even for rejected codes."""
    _reserve_consume_attempt(
        db,
        channel_kind=channel_kind,
        channel_key=verified_channel_key,
        sender=external_user_id,
    )
    if (
        not external_user_id
        or len(external_user_id) > 255
        or not re.fullmatch(r"[A-Za-z0-9_-]{32}", code)
    ):
        raise ChannelBindingError(
            "binding_challenge_invalid", "绑定码无效或已过期", 400
        )
    challenge = db.scalar(
        select(UserChannelBindingChallenge).where(
            UserChannelBindingChallenge.token_hash == hash_token(code),
            UserChannelBindingChallenge.channel_kind == channel_kind,
            UserChannelBindingChallenge.verified_channel_key == verified_channel_key,
        )
    )
    stamp = now()
    if challenge is None or challenge.used_at or as_utc(challenge.expires_at) <= stamp:
        raise ChannelBindingError(
            "binding_challenge_invalid", "绑定码无效或已过期", 400
        )
    _lock_active_owner(db, challenge.owner_user_id)
    try:
        with db.begin_nested():
            consumed = db.execute(
                update(UserChannelBindingChallenge)
                .where(
                    UserChannelBindingChallenge.id == challenge.id,
                    UserChannelBindingChallenge.used_at.is_(None),
                    UserChannelBindingChallenge.expires_at > stamp,
                )
                .values(used_at=stamp)
                .execution_options(synchronize_session="fetch")
            )
            if consumed.rowcount != 1:
                raise ChannelBindingError(
                    "binding_challenge_invalid", "绑定码无效或已过期", 400
                )
            existing = db.scalar(
                select(UserChannelBinding).where(
                    UserChannelBinding.provider == "wecom",
                    UserChannelBinding.channel_kind == channel_kind,
                    UserChannelBinding.verified_channel_key == verified_channel_key,
                    UserChannelBinding.external_user_id == external_user_id,
                )
            )
            if existing is not None and existing.status == "active":
                raise ChannelBindingError(
                    "binding_already_bound",
                    "该企业微信账号已绑定，请先由原账号在网页中解除绑定",
                )
            binding_id = f"ucb_{new_token(16)}"
            if existing is None:
                binding = UserChannelBinding(
                    id=binding_id,
                    provider="wecom",
                    channel_kind=channel_kind,
                    verified_channel_key=verified_channel_key,
                    external_user_id=external_user_id,
                    owner_user_id=challenge.owner_user_id,
                    status="active",
                    version=1,
                    created_at=stamp,
                    updated_at=stamp,
                )
                db.add(binding)
            else:
                # Rotate identity, not just owner: old grants and Pi histories
                # must never become valid for a replacement binding.
                changed = db.execute(
                    update(UserChannelBinding)
                    .where(
                        UserChannelBinding.id == existing.id,
                        UserChannelBinding.version == existing.version,
                        UserChannelBinding.status == "revoked",
                    )
                    .values(
                        id=binding_id,
                        owner_user_id=challenge.owner_user_id,
                        status="active",
                        version=existing.version + 1,
                        created_at=stamp,
                        updated_at=stamp,
                        revoked_at=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                if changed.rowcount != 1:
                    raise ChannelBindingError(
                        "binding_already_bound", "绑定状态已变化，请重新申请绑定码"
                    )
                binding = db.get(UserChannelBinding, binding_id)
                if binding is None:
                    raise ChannelBindingError(
                        "binding_unavailable", "绑定暂时不可用", 503
                    )
            db.flush()
            return binding
    except IntegrityError as exc:
        raise ChannelBindingError(
            "binding_already_bound", "该企业微信账号已绑定，请先由原账号解除绑定"
        ) from exc


def revoke_binding(
    db: Session, *, owner_user_id: str, binding_id: str, expected_version: int
) -> None:
    _lock_active_owner(db, owner_user_id)
    user_memory.lock_owned_state(db, owner_user_id)
    binding = db.scalar(
        select(UserChannelBinding).where(
            UserChannelBinding.owner_user_id == owner_user_id,
            UserChannelBinding.id == binding_id,
        )
    )
    if binding is None:
        raise ChannelBindingError("binding_not_found", "绑定不存在", 404)
    if binding.version != expected_version or binding.status != "active":
        raise ChannelBindingError(
            "binding_version_conflict", "绑定状态已变化，请刷新后重新确认"
        )
    runtime_session_id = binding_runtime_session_id(binding)
    stamp = now()
    changed = db.execute(
        update(UserChannelBinding)
        .where(
            UserChannelBinding.owner_user_id == owner_user_id,
            UserChannelBinding.id == binding_id,
            UserChannelBinding.version == expected_version,
            UserChannelBinding.status == "active",
        )
        .values(
            status="revoked",
            version=expected_version + 1,
            revoked_at=stamp,
            updated_at=stamp,
        )
    )
    if changed.rowcount != 1:
        raise ChannelBindingError(
            "binding_version_conflict", "绑定状态已变化，请刷新后重新确认"
        )
    turn_ids = list(
        db.scalars(
            select(UserMemoryTurn.id).where(
                UserMemoryTurn.owner_user_id == owner_user_id,
                UserMemoryTurn.channel == "wecom",
                UserMemoryTurn.runtime_session_id == runtime_session_id,
            )
        )
    )
    user_memory.invalidate_turn_sources(
        db, owner_user_id=owner_user_id, turn_ids=turn_ids
    )
