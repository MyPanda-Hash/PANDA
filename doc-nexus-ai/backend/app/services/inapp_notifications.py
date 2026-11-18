"""站内通知的读写。

规则：
- **只提醒一次**：同一个人、同一件事（``dedupe_key``）在有未读的情况下不再堆一条；
- **谁的事给谁**：只有收件人自己的列表里有这条，管理员不代收；
- 已读只是打时间戳，不删行——留痕比省空间重要。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import new_token
from app.core.security import now as _now
from app.core.timeutil import as_utc
from app.models.inapp_notification import INAPP_KINDS, InappNotification

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return f"inap_{new_token(10)}"


def push(
    db: Session,
    *,
    user_id: str,
    kind: str,
    title: str,
    body: str = "",
    ref_type: str = "",
    ref_id: str | None = None,
    group_id: str | None = None,
    actor_name: str = "",
    dedupe_key: str = "",
) -> InappNotification | None:
    """给某个人推一条站内通知。重复未读会跳过，返回 None。"""
    if not user_id:
        return None
    if kind not in INAPP_KINDS:
        logger.warning("inapp.unknown_kind %s", kind)
        return None

    if dedupe_key:
        existing = db.scalar(
            select(InappNotification).where(
                InappNotification.user_id == user_id,
                InappNotification.dedupe_key == dedupe_key,
                InappNotification.read_at.is_(None),
            )
        )
        if existing is not None:
            return None

    row = InappNotification(
        id=_new_id(),
        user_id=user_id,
        kind=kind,
        title=(title or "").strip()[:255],
        body=(body or "").strip()[:4000],
        ref_type=ref_type,
        ref_id=ref_id,
        group_id=group_id,
        actor_name=(actor_name or "").strip()[:120],
        dedupe_key=dedupe_key[:160],
        created_at=_now(),
    )
    db.add(row)
    db.flush()
    return row


def pending(db: Session, user_id: str, *, limit: int = 20) -> list[InappNotification]:
    rows = db.scalars(
        select(InappNotification)
        .where(InappNotification.user_id == user_id, InappNotification.read_at.is_(None))
        .order_by(InappNotification.created_at.desc())
        .limit(max(1, min(limit, 50)))
    )
    return list(rows)


def unread_count(db: Session, user_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(InappNotification)
            .where(
                InappNotification.user_id == user_id,
                InappNotification.read_at.is_(None),
            )
        )
        or 0
    )


def mark_read(db: Session, user_id: str, ids: list[str] | None = None) -> int:
    stmt = select(InappNotification).where(
        InappNotification.user_id == user_id, InappNotification.read_at.is_(None)
    )
    if ids:
        stmt = stmt.where(InappNotification.id.in_(ids))
    rows = list(db.scalars(stmt))
    moment = _now()
    for row in rows:
        row.read_at = moment
    if rows:
        db.flush()
    return len(rows)


def render(row: InappNotification) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "refType": row.ref_type,
        "refId": row.ref_id,
        "groupId": row.group_id,
        "actorName": row.actor_name,
        "createdAt": as_utc(row.created_at).isoformat() if row.created_at else "",
    }