from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    hash_password,
    hash_token,
    iso,
    new_api_key,
    new_token,
    now,
)
from app.models.access import KnowledgeSpace, UserRole
from app.models.auth import Invitation, User, UserApiKey
from app.models.auth import Session as LoginSession
from app.models.documents import Document, DocumentJob
from app.models.system_config import SystemConfig
from app.schemas.auth import InviteRequest

BOOTSTRAP_ADMIN_ID_KEY = "bootstrap_admin_user_id"


def get_by_id(db: Session, user_id: str) -> User | None:
    return db.scalar(select(User).where(User.id == user_id))


def get_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_bootstrap_admin(db: Session, username: str) -> User | None:
    marker = db.get(SystemConfig, BOOTSTRAP_ADMIN_ID_KEY)
    if marker and marker.value:
        user = get_by_id(db, marker.value)
        if user is not None:
            return user
    return get_by_username(db, username)


def remember_bootstrap_admin(db: Session, user: User) -> None:
    marker = db.get(SystemConfig, BOOTSTRAP_ADMIN_ID_KEY)
    if marker is None:
        db.add(
            SystemConfig(
                key=BOOTSTRAP_ADMIN_ID_KEY,
                value=user.id,
                description="Doc Nexus bootstrap administrator identity",
                updated_at=now(),
            )
        )
        return
    marker.value = user.id
    marker.updated_at = now()


def is_bootstrap_admin(db: Session, user: User, username: str) -> bool:
    bootstrap = get_bootstrap_admin(db, username)
    return bootstrap is not None and bootstrap.id == user.id


def get_by_email(db: Session, email: str) -> User | None:
    key = email.strip()
    if not key:
        return None
    return db.scalar(select(User).where(User.email == key))


def get_by_login_identifier(db: Session, identifier: str) -> User | None:
    """Resolve a login account by username or email (email match is case-insensitive)."""
    key = identifier.strip()
    if not key:
        return None
    user = get_by_username(db, key)
    if user:
        return user
    return db.scalar(select(User).where(func.lower(User.email) == key.lower()))


def get_by_api_key(db: Session, raw_api_key: str) -> User | None:
    """Resolve an active integration key, including legacy user keys."""
    value = raw_api_key.strip()
    if not value:
        return None
    key = db.scalar(
        select(UserApiKey).where(
            UserApiKey.api_key == value
        )
    )
    if key is not None:
        return key.user if key.status == "active" and key.user and key.user.status == "active" else None
    return db.scalar(select(User).where(User.api_key == value, User.status == "active"))


def _normalized_email(payload: InviteRequest) -> str | None:
    if payload.email is None:
        return None
    return str(payload.email)


def get_by_email_or_username(db: Session, payload: InviteRequest) -> User | None:
    clauses = [User.username == payload.username]
    email = _normalized_email(payload)
    if email is not None:
        clauses.append(User.email == email)
    return db.scalar(select(User).where(or_(*clauses)))


def resolve_create_target(
    db: Session, payload: InviteRequest
) -> tuple[str, User | None]:
    """Classify username/email for create: conflict | restore | create.

    Soft-deleted rows still occupy unique indexes, so recreating the same
    account restores that row instead of inserting a duplicate.
    """
    by_username = get_by_username(db, payload.username)
    email = _normalized_email(payload)
    by_email = get_by_email(db, email) if email else None
    active = [
        user
        for user in (by_username, by_email)
        if user is not None and user.status != "deleted"
    ]
    if active:
        return "conflict", None
    deleted = {
        user.id: user
        for user in (by_username, by_email)
        if user is not None and user.status == "deleted"
    }
    if len(deleted) > 1:
        return "conflict", None
    if deleted:
        return "restore", next(iter(deleted.values()))
    return "create", None


def resolve_by_identifier(db: Session, identifier: str) -> User | None:
    """Resolve a user by id, username, or email."""
    key = identifier.strip()
    if not key:
        return None
    return get_by_id(db, key) or get_by_username(db, key) or get_by_email(db, key)


def list_directory(db: Session, query: str = "", limit: int = 200) -> list[User]:
    """Return active employees for space-member pickers, filtered in memory.

    The product scale is about 100 users, so a full active-user scan is cheaper
    and safer than SQL LIKE wildcards from untrusted search text.
    """
    safe_limit = max(1, min(limit, 200))
    items = list(
        db.scalars(
            select(User)
            .where(User.status == "active")
            .order_by(User.display_name, User.username, User.id)
        )
    )
    needle = query.strip().lower()
    if needle:
        items = [
            user
            for user in items
            if needle in (user.display_name or "").lower()
            or needle in (user.username or "").lower()
            or needle in (user.email or "").lower()
        ]
    return items[:safe_limit]


def _apply_profile(user: User, payload: InviteRequest) -> None:
    user.username = payload.username
    user.email = _normalized_email(payload)
    user.display_name = payload.display_name
    user.status = "active"
    user.failed_attempts = 0
    user.locked_until = None
    user.linkmind_login_status = "logged_out"


def _revoke_pending_invitations(db: Session, user_id: str) -> None:
    stamped = now()
    pending = list(
        db.scalars(
            select(Invitation).where(
                Invitation.user_id == user_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
        )
    )
    for invitation in pending:
        invitation.revoked_at = stamped


def create_invited(db: Session, payload: InviteRequest) -> tuple[User, str, str, str]:
    user_id = f"usr_{new_token(10)}"
    invitation_id = f"inv_{new_token(10)}"
    raw_token = new_token(32)
    raw_api_key = new_api_key()
    created = now()
    user = User(
        id=user_id,
        username=payload.username,
        email=_normalized_email(payload),
        display_name=payload.display_name,
        password_hash=hash_password(secrets.token_urlsafe(24)),
        api_key=raw_api_key,
        role="user",
        first_login=True,
        created_at=created,
    )
    invitation = Invitation(
        id=invitation_id,
        user_id=user_id,
        token_hash=hash_token(raw_token),
        expires_at=created + timedelta(hours=settings.invite_ttl_hours),
        created_at=created,
    )
    db.add_all([user, invitation])
    db.flush()
    return user, raw_token, iso(invitation.expires_at), raw_api_key


def restore_invited(
    db: Session, user: User, payload: InviteRequest
) -> tuple[User, str, str, str]:
    _apply_profile(user, payload)
    user.password_hash = hash_password(secrets.token_urlsafe(24))
    user.auth_version += 1
    user.first_login = True
    if settings.linkmind_enabled:
        user.linkmind_login_status = "logged_out"
    raw_api_key = new_api_key()
    user.api_key = raw_api_key
    _revoke_pending_invitations(db, user.id)
    revoke_sessions(db, user.id)
    raw_token = new_token(32)
    created = now()
    invitation = Invitation(
        id=f"inv_{new_token(10)}",
        user_id=user.id,
        token_hash=hash_token(raw_token),
        expires_at=created + timedelta(hours=settings.invite_ttl_hours),
        created_at=created,
    )
    db.add(invitation)
    db.flush()
    return user, raw_token, iso(invitation.expires_at), raw_api_key


def create_with_password(
    db: Session, payload: InviteRequest, password: str
) -> tuple[User, str]:
    """Create an active user who can log in immediately with the given password."""
    created = now()
    raw_api_key = new_api_key()
    user = User(
        id=f"usr_{new_token(10)}",
        username=payload.username,
        email=_normalized_email(payload),
        display_name=payload.display_name,
        password_hash=hash_password(password),
        api_key=raw_api_key,
        role="user",
        status="active",
        first_login=True,
        created_at=created,
    )
    db.add(user)
    db.flush()
    return user, raw_api_key


def restore_with_password(
    db: Session, user: User, payload: InviteRequest, password: str
) -> tuple[User, str]:
    """Re-activate a soft-deleted user with a fresh API key."""
    _apply_profile(user, payload)
    set_password(db, user, password, require_change=True)
    raw_api_key = new_api_key()
    user.api_key = raw_api_key
    _revoke_pending_invitations(db, user.id)
    revoke_sessions(db, user.id)
    db.flush()
    return user, raw_api_key


def set_password(
    db: Session, user: User, password: str, *, require_change: bool = True
) -> None:
    user.password_hash = hash_password(password)
    user.auth_version += 1
    user.first_login = require_change
    if settings.linkmind_enabled and require_change:
        user.linkmind_login_status = "logged_out"
    user.failed_attempts = 0
    user.locked_until = None


def revoke_sessions(
    db: Session,
    user_id: str,
    *,
    except_refresh_hash: str | None = None,
) -> int:
    statement = select(LoginSession).where(
        LoginSession.user_id == user_id,
        LoginSession.revoked_at.is_(None),
    )
    if except_refresh_hash:
        statement = statement.where(LoginSession.refresh_hash != except_refresh_hash)
    sessions = list(db.scalars(statement))
    stamped = now()
    for session in sessions:
        session.revoked_at = stamped
    return len(sessions)


def create_api_key(db: Session, user_id: str) -> UserApiKey:
    key = UserApiKey(
        id=f"uak_{new_token(10)}",
        user_id=user_id,
        api_key=new_api_key(),
        created_at=now(),
    )
    db.add(key)
    db.flush()
    return key


def list_api_keys(db: Session, user_id: str) -> list[UserApiKey]:
    return list(
        db.scalars(
            select(UserApiKey)
            .where(UserApiKey.user_id == user_id)
            .order_by(UserApiKey.created_at.desc())
        )
    )


def revoke_api_key(db: Session, user_id: str, key_id: str) -> UserApiKey | None:
    key = db.scalar(
        select(UserApiKey).where(UserApiKey.id == key_id, UserApiKey.user_id == user_id)
    )
    if key:
        key.status = "revoked"
    return key


def soft_delete(db: Session, user: User) -> None:
    user.status = "deleted"
    user.linkmind_login_status = "logged_out"
    revoke_sessions(db, user.id)


def hard_delete(db: Session, user: User, *, reassign_to_user_id: str) -> None:
    """Permanently remove a soft-deleted user and free username/email.

    Non-cascade ownership fields are reassigned so historical spaces/documents remain.
    """
    if user.id == reassign_to_user_id:
        raise ValueError("cannot reassign ownership to the user being deleted")
    from app.services import external_object_cleanup

    external_object_cleanup.enqueue_user_media(db, user.id)
    db.execute(
        update(KnowledgeSpace)
        .where(KnowledgeSpace.owner_user_id == user.id)
        .values(owner_user_id=reassign_to_user_id)
    )
    db.execute(
        update(Document)
        .where(Document.created_by_user_id == user.id)
        .values(created_by_user_id=reassign_to_user_id)
    )
    db.execute(
        update(DocumentJob)
        .where(DocumentJob.created_by_user_id == user.id)
        .values(created_by_user_id=reassign_to_user_id)
    )
    for binding in db.scalars(select(UserRole).where(UserRole.user_id == user.id)):
        db.delete(binding)
    revoke_sessions(db, user.id)
    _revoke_pending_invitations(db, user.id)
    db.delete(user)
    db.flush()


def invitation_by_token(db: Session, raw_token: str) -> Invitation | None:
    return db.scalar(
        select(Invitation).where(Invitation.token_hash == hash_token(raw_token))
    )
