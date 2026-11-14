"""Read-only system metadata queries for administrators and auditors.

This module deliberately exposes bounded metadata instead of arbitrary SQL or
secret-bearing ORM snapshots. Capability callers must still pass the current
user through the permission checks below on every request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.security import iso, now
from app.models.access import KnowledgeSpace
from app.models.auth import Invitation, Session as LoginSession, User
from app.models.documents import Document, DocumentVersion
from app.repositories import usage
from app.services.authorization import PermissionService

SYSTEM_OVERVIEW_PERMISSION = "system:overview:view"
USER_DIRECTORY_PERMISSION = "user:manage"
USER_USAGE_PERMISSION = "usage:view_all"

_MAX_QUERY_LENGTH = 120
_MAX_LIMIT = 100
_MAX_OFFSET = 1_000_000
_USER_STATUSES = frozenset({"active", "disabled", "deleted"})


class SystemInsightError(ValueError):
    """A bounded system insight request is invalid or unauthorized."""


def _require_permission(db: Session, user: User, permission_code: str) -> None:
    if not PermissionService.has_system_permission(db, user, permission_code):
        raise SystemInsightError("没有执行该系统查询的权限")


def _text_argument(arguments: dict[str, Any], key: str, *, max_length: int) -> str:
    value = arguments.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SystemInsightError(f"{key} must be a string")
    value = value.strip()
    if len(value) > max_length:
        raise SystemInsightError(f"{key} exceeds its length limit")
    return value
def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"




def _bounded_int(
    arguments: dict[str, Any], key: str, *, default: int, minimum: int, maximum: int
) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool):
        raise SystemInsightError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SystemInsightError(f"{key} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise SystemInsightError(f"{key} is outside its allowed range")
    return parsed


def _count(db: Session, model: Any, *conditions: Any) -> int:
    statement = select(func.count()).select_from(model)
    if conditions:
        statement = statement.where(*conditions)
    return int(db.scalar(statement) or 0)


def _group_counts(db: Session, column: Any, *conditions: Any) -> dict[str, int]:
    statement = select(column, func.count()).group_by(column)
    if conditions:
        statement = statement.where(*conditions)
    return {
        str(key or "unknown"): int(count)
        for key, count in db.execute(statement).all()
    }


def _user_view(user: User) -> dict[str, Any]:
    """Return fields safe for an agent response; never include credentials."""
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "status": user.status,
        "linkmind_login_status": user.linkmind_login_status,
        "first_login": bool(user.first_login),
        "created_at": iso(user.created_at),
    }


def _latest_document_stage_counts(db: Session) -> dict[str, int]:
    latest_number = (
        select(func.max(DocumentVersion.version_number))
        .where(DocumentVersion.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )
    statement = (
        select(DocumentVersion.processing_stage, func.count())
        .join(Document, DocumentVersion.document_id == Document.id)
        .where(
            Document.status == "active",
            DocumentVersion.version_number == latest_number,
        )
        .group_by(DocumentVersion.processing_stage)
    )
    return {
        str(stage or "unknown"): int(count)
        for stage, count in db.execute(statement).all()
    }


def get_system_overview(db: Session, user: User) -> dict[str, Any]:
    """Return bounded, current system counts for an authorized administrator."""
    _require_permission(db, user, SYSTEM_OVERVIEW_PERMISSION)
    timestamp = now()
    user_status = _group_counts(db, User.status)
    user_role = _group_counts(db, User.role)
    space_status = _group_counts(db, KnowledgeSpace.status)
    document_status = _group_counts(db, Document.status)
    document_stage = _latest_document_stage_counts(db)
    active_document_count = _count(db, Document, Document.status == "active")
    active_documents = select(Document.id).where(Document.status == "active")
    queryable_documents = _count(
        db,
        DocumentVersion,
        DocumentVersion.is_current_published.is_(True),
        DocumentVersion.queryable.is_(True),
        DocumentVersion.processing_stage == "ready",
        DocumentVersion.document_id.in_(active_documents),
    )
    pending_invitations = _count(
        db,
        Invitation,
        Invitation.accepted_at.is_(None),
        Invitation.revoked_at.is_(None),
        Invitation.expires_at > timestamp,
    )
    active_sessions = _count(
        db,
        LoginSession,
        LoginSession.revoked_at.is_(None),
        LoginSession.expires_at > timestamp,
    )
    return {
        "generated_at": iso(timestamp),
        "users": {
            "total": _count(db, User),
            "by_status": user_status,
            "by_role": user_role,
            "first_login_pending": _count(
                db, User, User.status == "active", User.first_login.is_(True)
            ),
        },
        "spaces": {
            "total": _count(db, KnowledgeSpace),
            "by_status": space_status,
        },
        "documents": {
            "total": _count(db, Document),
            "active": active_document_count,
            "by_status": document_status,
            "latest_version_by_stage": document_stage,
            "queryable_published": queryable_documents,
        },
        "invitations": {"pending": pending_invitations},
        "sessions": {"active_refresh_sessions": active_sessions},
    }


def list_system_users(
    db: Session, user: User, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Return a paginated, secret-free system user directory."""
    _require_permission(db, user, USER_DIRECTORY_PERMISSION)
    query_text = _text_argument(arguments, "query", max_length=_MAX_QUERY_LENGTH)
    status = _text_argument(arguments, "status", max_length=32)
    role = _text_argument(arguments, "role", max_length=32)
    if status and status not in _USER_STATUSES:
        raise SystemInsightError("status is invalid")
    limit = _bounded_int(
        arguments, "limit", default=50, minimum=1, maximum=_MAX_LIMIT
    )
    offset = _bounded_int(
        arguments, "offset", default=0, minimum=0, maximum=_MAX_OFFSET
    )

    statement = select(User)
    if query_text:
        pattern = _like_pattern(query_text)
        statement = statement.where(
            or_(
                User.username.ilike(pattern, escape="\\"),
                User.display_name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
            )
        )
    if status:
        statement = statement.where(User.status == status)
    if role:
        statement = statement.where(User.role == role)

    total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    rows = list(
        db.scalars(
            statement.order_by(User.created_at.asc(), User.id.asc())
            .offset(offset)
            .limit(limit)
        )
    )
    next_offset = offset + limit if offset + limit < total else None
    return {
        "items": [_user_view(item) for item in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
    }


def _parse_datetime_argument(arguments: dict[str, Any], key: str) -> datetime | None:
    value = _text_argument(arguments, key, max_length=64)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemInsightError(f"{key} must be an ISO datetime") from exc


def _resolve_user(db: Session, arguments: dict[str, Any]) -> User:
    user_id = _text_argument(arguments, "user_id", max_length=64)
    if user_id:
        found = db.get(User, user_id)
        if found is None:
            raise SystemInsightError("用户不存在")
        return found
    query_text = _text_argument(arguments, "query", max_length=_MAX_QUERY_LENGTH)
    if not query_text:
        raise SystemInsightError("user_id or query is required")
    pattern = _like_pattern(query_text)
    matches = list(
        db.scalars(
            select(User)
            .where(
                or_(
                    User.username.ilike(pattern, escape="\\"),
                    User.display_name.ilike(pattern, escape="\\"),
                    User.email.ilike(pattern, escape="\\"),
                )
            )
            .order_by(User.created_at.asc(), User.id.asc())
            .limit(2)
        )
    )
    if not matches:
        raise SystemInsightError("用户不存在")
    if len(matches) > 1:
        raise SystemInsightError("匹配到多个用户，请改用 user_id")
    return matches[0]


def get_user_usage(
    db: Session, user: User, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Return aggregate usage metadata for one authorized user."""
    _require_permission(db, user, USER_USAGE_PERMISSION)
    target = _resolve_user(db, arguments)
    date_from = _parse_datetime_argument(arguments, "date_from")
    date_to = _parse_datetime_argument(arguments, "date_to")
    if date_from is not None and date_to is not None and date_from > date_to:
        raise SystemInsightError("date_from must not be later than date_to")
    result = usage.summarize_usage_by_user(
        db,
        date_from=date_from,
        date_to=date_to,
        include_zero=True,
    )
    summary = next(
        (item for item in result.items if item.user_id == target.id),
        None,
    )
    if summary is None:
        raise SystemInsightError("用户用量不存在")
    return {
        "user": _user_view(target),
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "operation_count": summary.operation_count,
        "input_tokens": summary.input_tokens,
        "input_uncached_tokens": summary.input_uncached_tokens,
        "cache_read_tokens": summary.cache_read_tokens,
        "cache_write_tokens": summary.cache_write_tokens,
        "output_tokens": summary.output_tokens,
        "total_tokens": summary.total_tokens,
        "cost": summary.cost,
        "by_feature": dict(summary.by_feature),
        "by_model": dict(summary.by_model),
    }


__all__ = [
    "SYSTEM_OVERVIEW_PERMISSION",
    "USER_DIRECTORY_PERMISSION",
    "USER_USAGE_PERMISSION",
    "SystemInsightError",
    "get_system_overview",
    "list_system_users",
    "get_user_usage",
]
