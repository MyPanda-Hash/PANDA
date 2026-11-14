from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, aliased

from app.core.security import new_token, now
from app.models.external_object_cleanup import ExternalObjectCleanupOutbox


def enqueue(
    db: Session,
    uri: str,
    *,
    resource_kind: str = "minio_object",
) -> ExternalObjectCleanupOutbox:
    """Insert one cleanup tombstone, preserving the first row for duplicate URIs."""
    normalized_uri = (uri or "").strip()
    if not normalized_uri:
        raise ValueError("external object URI is required")
    normalized_kind = (resource_kind or "minio_object").strip() or "minio_object"
    stamp = now()

    existing = db.scalar(
        select(ExternalObjectCleanupOutbox).where(
            ExternalObjectCleanupOutbox.uri == normalized_uri
        )
    )
    if existing is not None:
        # A deferred/succeeded row can become live again (for example when a
        # second task reuses the same image).  Wake it without stealing an
        # active worker lease.
        if existing.status != "processing":
            existing.status = "pending"
            existing.next_attempt_at = stamp
            existing.last_error = None
            existing.claimed_at = None
            existing.claim_token = None
            existing.completed_at = None
            existing.updated_at = stamp
            db.flush()
        return existing

    values = {
        "id": f"eoc_{new_token(10)}",
        "uri": normalized_uri,
        "resource_kind": normalized_kind,
        "status": "pending",
        "attempt_count": 0,
        "next_attempt_at": stamp,
        "last_error": None,
        "claimed_at": None,
        "claim_token": None,
        "created_at": stamp,
        "updated_at": stamp,
        "completed_at": None,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        db.execute(
            sqlite_insert(ExternalObjectCleanupOutbox)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["uri"])
        )
    elif dialect == "postgresql":
        db.execute(
            postgres_insert(ExternalObjectCleanupOutbox)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["uri"])
        )
    else:
        existing = db.scalar(
            select(ExternalObjectCleanupOutbox).where(
                ExternalObjectCleanupOutbox.uri == normalized_uri
            )
        )
        if existing is None:
            db.add(ExternalObjectCleanupOutbox(**values))
            db.flush()
    item = db.scalar(
        select(ExternalObjectCleanupOutbox).where(
            ExternalObjectCleanupOutbox.uri == normalized_uri
        )
    )
    if item is None:
        raise RuntimeError("cleanup tombstone was not persisted")
    return item


def enqueue_many(
    db: Session,
    refs: Iterable[tuple[str, str]],
) -> list[ExternalObjectCleanupOutbox]:
    """Insert unique URI/kind pairs and return their durable tombstones."""
    seen: set[str] = set()
    rows: list[ExternalObjectCleanupOutbox] = []
    for uri, resource_kind in refs:
        normalized_uri = (uri or "").strip()
        if not normalized_uri or normalized_uri in seen:
            continue
        seen.add(normalized_uri)
        rows.append(enqueue(db, normalized_uri, resource_kind=resource_kind))
    return rows


def claim_next(
    db: Session,
    *,
    lease_seconds: int = 300,
    now_at: datetime | None = None,
) -> ExternalObjectCleanupOutbox | None:
    """Claim one ready row; expired processing leases are reclaimable."""
    current = now_at or now()
    stale_before = current - timedelta(seconds=max(1, lease_seconds))
    ready_clause = or_(
        and_(
            ExternalObjectCleanupOutbox.status == "pending",
            ExternalObjectCleanupOutbox.next_attempt_at <= current,
        ),
        and_(
            ExternalObjectCleanupOutbox.status == "processing",
            or_(
                ExternalObjectCleanupOutbox.claimed_at.is_(None),
                ExternalObjectCleanupOutbox.claimed_at <= stale_before,
            ),
        ),
    )

    if db.get_bind().dialect.name == "sqlite":
        # SQLite has no SKIP LOCKED.  A single conditional UPDATE makes the
        # claim winner atomic even when two independent sessions selected the
        # same candidate before either transaction committed.
        candidate = aliased(ExternalObjectCleanupOutbox)
        candidate_id = (
            select(candidate.id)
            .where(
                or_(
                    and_(
                        candidate.status == "pending",
                        candidate.next_attempt_at <= current,
                    ),
                    and_(
                        candidate.status == "processing",
                        or_(
                            candidate.claimed_at.is_(None),
                            candidate.claimed_at <= stale_before,
                        ),
                    ),
                )
            )
            .order_by(candidate.next_attempt_at, candidate.created_at)
            .limit(1)
            .correlate(None)
            .scalar_subquery()
        )
        claim_token = new_token(24)
        result = db.execute(
            update(ExternalObjectCleanupOutbox)
            .where(
                ExternalObjectCleanupOutbox.id == candidate_id,
                ready_clause,
            )
            .values(
                status="processing",
                attempt_count=ExternalObjectCleanupOutbox.attempt_count + 1,
                claimed_at=current,
                claim_token=claim_token,
                updated_at=current,
            )
            .returning(ExternalObjectCleanupOutbox.id)
        )
        claimed_id = result.scalar_one_or_none()
        if claimed_id is None:
            return None
        return db.get(ExternalObjectCleanupOutbox, claimed_id)

    statement = (
        select(ExternalObjectCleanupOutbox)
        .where(ready_clause)
        .order_by(
            ExternalObjectCleanupOutbox.next_attempt_at,
            ExternalObjectCleanupOutbox.created_at,
        )
        .limit(1)
    )
    if db.get_bind().dialect.name != "sqlite":
        statement = statement.with_for_update(skip_locked=True)
    item = db.scalar(statement)
    if item is None:
        return None
    item.status = "processing"
    item.attempt_count += 1
    item.claimed_at = current
    item.claim_token = new_token(24)
    item.updated_at = current
    db.flush()
    return item


def mark_succeeded(
    db: Session,
    *,
    outbox_id: str,
    claim_token: str,
    now_at: datetime | None = None,
) -> bool:
    item = db.scalar(
        select(ExternalObjectCleanupOutbox).where(
            ExternalObjectCleanupOutbox.id == outbox_id,
            ExternalObjectCleanupOutbox.status == "processing",
            ExternalObjectCleanupOutbox.claim_token == claim_token,
        )
    )
    if item is None:
        return False
    current = now_at or now()
    item.status = "succeeded"
    item.claimed_at = None
    item.claim_token = None
    item.last_error = None
    item.completed_at = current
    item.updated_at = current
    db.flush()
    return True


def mark_retry(
    db: Session,
    *,
    outbox_id: str,
    claim_token: str,
    error: str,
    retry_base_seconds: float = 2.0,
    retry_max_seconds: float = 300.0,
    now_at: datetime | None = None,
) -> bool:
    item = db.scalar(
        select(ExternalObjectCleanupOutbox).where(
            ExternalObjectCleanupOutbox.id == outbox_id,
            ExternalObjectCleanupOutbox.status == "processing",
            ExternalObjectCleanupOutbox.claim_token == claim_token,
        )
    )
    if item is None:
        return False
    current = now_at or now()
    delay = min(
        max(0.0, retry_max_seconds),
        max(0.0, retry_base_seconds)
        * 2 ** min(max(0, item.attempt_count - 1), 8),
    )
    item.status = "pending"
    item.next_attempt_at = current + timedelta(seconds=delay)
    item.claimed_at = None
    item.claim_token = None
    item.last_error = (error or "external object cleanup failed")[:1_000]
    item.completed_at = None
    item.updated_at = current
    db.flush()
    return True


def mark_deferred(
    db: Session,
    *,
    outbox_id: str,
    claim_token: str,
    reason: str,
    delay_seconds: float,
    now_at: datetime | None = None,
) -> bool:
    """Return a claimed row to pending without treating an active reference as a failure."""
    item = db.scalar(
        select(ExternalObjectCleanupOutbox).where(
            ExternalObjectCleanupOutbox.id == outbox_id,
            ExternalObjectCleanupOutbox.status == "processing",
            ExternalObjectCleanupOutbox.claim_token == claim_token,
        )
    )
    if item is None:
        return False
    current = now_at or now()
    item.status = "pending"
    item.next_attempt_at = current + timedelta(seconds=max(1.0, delay_seconds))
    item.claimed_at = None
    item.claim_token = None
    item.last_error = (reason or "external object cleanup deferred")[:1_000]
    item.completed_at = None
    item.updated_at = current
    db.flush()
    return True
