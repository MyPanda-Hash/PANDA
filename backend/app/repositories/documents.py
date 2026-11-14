from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from datetime import datetime, timezone

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.core.security import new_token, now
from app.models.access import KnowledgeSpace, SpaceMember
from app.models.auth import User
from app.models.documents import Document, DocumentJob, DocumentVersion
from app.models.document_uploads import DocumentUploadSession
from app.models.knowledge_graph import (
    KnowledgeEntity,
    KnowledgeEntityDocument,
    KnowledgeRelation,
)


PAGE_SIZE = 50
# Aggregate filter for UI “处理中”; includes all local pipeline stages.
PROCESSING_STAGES = (
    "queued",
    "scanning",
    "parsing",
    "extracting_text",
    "rendering_pages",
    "running_ocr",
    "quality_check",
    "chunking",
    "indexing",
    "processing",
)
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "ready", "cancelled"})


def creator_display_names(db: Session, user_ids: list[str]) -> dict[str, str]:
    ids = {user_id for user_id in user_ids if user_id}
    if not ids:
        return {}
    return {
        user.id: (user.display_name or user.username)
        for user in db.scalars(select(User).where(User.id.in_(ids))).all()
    }


def create_document(
    db: Session,
    *,
    space_id: str | None,
    category_id: str | None,
    name: str,
    created_by_user_id: str,
    file_path: str | None = None,
    source_relative_path: str | None = None,
    ext_task_id: str | None = None,
    is_temporary: bool = False,
    chat_session_id: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[Document, DocumentVersion]:
    created_at = now()
    document = Document(
        id=f"doc_{new_token(10)}",
        space_id=space_id,
        category_id=category_id,
        name=name,
        status="active",
        is_temporary=is_temporary,
        chat_session_id=chat_session_id,
        expires_at=expires_at,
        created_by_user_id=created_by_user_id,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(document)
    db.flush()
    version = DocumentVersion(
        id=f"ver_{new_token(10)}",
        document_id=document.id,
        version_number=1,
        processing_stage="queued",
        queryable=False,
        retryable=False,
        source_relative_path=source_relative_path,
        file_path=file_path,
        ext_task_id=ext_task_id,
        is_current_published=False,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(version)
    db.flush()
    return document, version


def get_document(db: Session, document_id: str) -> Document | None:
    return db.get(Document, document_id)


def create_job(
    db: Session,
    *,
    document: Document,
    version: DocumentVersion,
    created_by_user_id: str,
) -> DocumentJob:
    created_at = now()
    job = DocumentJob(
        id=f"job_{new_token(10)}",
        document_id=document.id,
        version_id=version.id,
        space_id=document.space_id,
        created_by_user_id=created_by_user_id,
        kind="document_processing",
        status=version.processing_stage,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(job)
    db.flush()
    return job


def get_job(db: Session, job_id: str) -> DocumentJob | None:
    return db.get(DocumentJob, job_id)


def document_resource_path(document: Document) -> str:
    """Return the frontend route for a document, never a dangling /documents/:id page."""
    if document.space_id:
        return f"/spaces/{document.space_id}/documents/{document.id}"
    if document.chat_session_id:
        return f"/chat/{document.chat_session_id}"
    return "/"


def public_job_status(job: DocumentJob, version: DocumentVersion) -> str:
    if job.status == "cancelled":
        return "cancelled"
    stage = version.processing_stage
    if stage == "ready":
        return "completed"
    if stage == "failed":
        return "failed"
    return stage or job.status


def job_is_cancellable(job: DocumentJob, version: DocumentVersion) -> bool:
    return (
        job.status not in TERMINAL_JOB_STATUSES
        and version.processing_stage in PROCESSING_STAGES
    )


def user_can_cancel_document_job(
    db: Session, *, user_id: str, is_admin: bool, document: Document
) -> bool:
    if document.space_id:
        if is_admin:
            return True
        member = db.scalar(
            select(SpaceMember).where(
                SpaceMember.space_id == document.space_id,
                SpaceMember.user_id == user_id,
            )
        )
        return bool(member and member.role in {"contributor", "manager"})
    return bool(document.is_temporary and document.created_by_user_id == user_id)


def reconcile_job_with_version(
    db: Session, job: DocumentJob, version: DocumentVersion
) -> DocumentJob:
    """If the version already finished, persist a matching terminal job status."""
    if job.status in TERMINAL_JOB_STATUSES:
        return job
    if version.processing_stage == "ready":
        job.status = "completed"
        job.updated_at = now()
    elif version.processing_stage == "failed":
        job.status = "failed"
        job.updated_at = now()
    return job


def cancel_job(db: Session, job: DocumentJob, version: DocumentVersion) -> DocumentJob:
    reconcile_job_with_version(db, job, version)
    if job.status in TERMINAL_JOB_STATUSES:
        return job
    job.status = "cancelled"
    job.updated_at = now()
    if version.processing_stage in PROCESSING_STAGES:
        update_version_stage(
            db,
            version,
            "failed",
            failure_code="cancelled",
            failure_message="任务已取消",
        )
    return job


def cancel_jobs_for_document(db: Session, document_id: str) -> int:
    """Cancel in-flight processing jobs when their document is deleted."""
    jobs = list(
        db.scalars(
            select(DocumentJob).where(
                DocumentJob.document_id == document_id,
                DocumentJob.status.notin_(tuple(TERMINAL_JOB_STATUSES)),
            )
        )
    )
    cancelled = 0
    for job in jobs:
        version = db.get(DocumentVersion, job.version_id)
        if version:
            cancel_job(db, job, version)
        else:
            job.status = "cancelled"
            job.updated_at = now()
        cancelled += 1
    return cancelled


def task_snapshot(
    db: Session,
    job: DocumentJob,
    *,
    user_id: str | None = None,
    is_admin: bool = False,
) -> dict:
    document = db.get(Document, job.document_id)
    version = db.get(DocumentVersion, job.version_id)
    if not document or not version:
        raise ValueError("document job references a missing resource")
    reconcile_job_with_version(db, job, version)
    retryable = is_retryable(db, version)
    cancellable = bool(
        user_id
        and job_is_cancellable(job, version)
        and user_can_cancel_document_job(
            db, user_id=user_id, is_admin=is_admin, document=document
        )
    )
    return {
        "id": job.id,
        "kind": job.kind,
        "status": public_job_status(job, version),
        "label": f"文档处理：{document.name}",
        "resource_path": document_resource_path(document),
        "retryable": retryable,
        "retry_action": {
            "path": f"/documents/{document.id}/versions/{version.id}:retry",
            "method": "POST",
        }
        if retryable
        else None,
        "cancel_action": {"path": f"/jobs/{job.id}:cancel", "method": "POST"}
        if cancellable
        else None,
        "operation_id": None,
        "job_id": job.id,
        "document_id": document.id,
    }


def list_active_jobs_for_user(
    db: Session, *, user_id: str, is_admin: bool
) -> list[DocumentJob]:
    query = (
        select(DocumentJob)
        .join(Document, Document.id == DocumentJob.document_id)
        .where(
            Document.status == "active",
            DocumentJob.status.notin_(tuple(TERMINAL_JOB_STATUSES)),
        )
        .order_by(DocumentJob.updated_at.desc())
    )
    if not is_admin:
        query = query.outerjoin(
            SpaceMember, SpaceMember.space_id == DocumentJob.space_id
        ).where(
            or_(
                SpaceMember.user_id == user_id,
                and_(
                    Document.is_temporary.is_(True),
                    Document.space_id.is_(None),
                    Document.created_by_user_id == user_id,
                ),
            )
        )
    jobs = list(db.scalars(query).unique())
    visible: list[DocumentJob] = []
    for job in jobs:
        version = db.get(DocumentVersion, job.version_id)
        if version:
            reconcile_job_with_version(db, job, version)
        elif job.status not in TERMINAL_JOB_STATUSES:
            job.status = "cancelled"
            job.updated_at = now()
        if job.status not in TERMINAL_JOB_STATUSES:
            visible.append(job)
    return visible


def get_job_for_version(db: Session, version_id: str) -> DocumentJob | None:
    return db.scalar(select(DocumentJob).where(DocumentJob.version_id == version_id))


def list_in_flight_versions(
    db: Session,
) -> list[tuple[DocumentVersion, DocumentJob | None]]:
    """Active-document versions still in the processing pipeline (restart orphans included)."""
    rows = list(
        db.execute(
            select(DocumentVersion, DocumentJob)
            .join(Document, Document.id == DocumentVersion.document_id)
            .outerjoin(DocumentJob, DocumentJob.version_id == DocumentVersion.id)
            .where(
                Document.status == "active",
                DocumentVersion.processing_stage.in_(PROCESSING_STAGES),
            )
            .order_by(DocumentVersion.updated_at.asc())
        ).all()
    )
    return [(version, job) for version, job in rows]


def list_expired_temporary_documents(
    db: Session, *, expires_before: datetime, limit: int = 100
) -> list[Document]:
    """Return expired temporary documents, oldest first, for bounded cleanup."""
    return list(
        db.scalars(
            select(Document)
            .where(
                Document.is_temporary.is_(True),
                Document.status.in_(("active", "deleted")),
                Document.expires_at.is_not(None),
                Document.expires_at <= expires_before,
                Document.temporary_cleanup_at.is_(None),
            )
            .order_by(Document.expires_at.asc(), Document.id.asc())
            .limit(max(1, min(limit, 1000)))
        )
    )


def list_temporary_documents_for_session(
    db: Session, *, chat_session_id: str
) -> list[Document]:
    """Return temporary attachments still bound to a chat session and not cleaned."""
    return list(
        db.scalars(
            select(Document)
            .where(
                Document.is_temporary.is_(True),
                Document.chat_session_id == chat_session_id,
                Document.temporary_cleanup_at.is_(None),
            )
            .order_by(Document.created_at.asc(), Document.id.asc())
        )
    )


def list_active_temporary_documents(
    db: Session,
    *,
    user_id: str,
    chat_session_id: str,
    now_at: datetime | None = None,
) -> list[Document]:
    """Return the owner's non-expired temporary attachments for one chat session."""
    cutoff = now_at or datetime.now(timezone.utc)
    return list(
        db.scalars(
            select(Document)
            .where(
                Document.is_temporary.is_(True),
                Document.created_by_user_id == user_id,
                Document.chat_session_id == chat_session_id,
                Document.status == "active",
                Document.temporary_cleanup_at.is_(None),
                or_(Document.expires_at.is_(None), Document.expires_at > cutoff),
            )
            .order_by(Document.created_at.desc(), Document.id.desc())
        )
    )


def latest_version(db: Session, document_id: str) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number.desc())
    )


def current_published_version(db: Session, document_id: str) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.is_current_published.is_(True),
        )
    )


def versions_for_document(db: Session, document_id: str) -> list[DocumentVersion]:
    return list(
        db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
        )
    )


def _like_pattern(needle: str) -> str:
    """Escape SQL LIKE wildcards so user input is matched literally.

    ``%`` / ``_`` / ``\\`` are escaped with a backslash; callers must pass
    ``escape="\\"`` to the ``ilike`` expression. A bare ``%`` therefore
    matches only document names that actually contain a percent sign instead
    of every row in the space.
    """
    return (
        needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


def search_documents_across_spaces(
    db: Session,
    *,
    user_id: str,
    is_admin: bool,
    query_text: str,
    queryable: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[tuple[Document, KnowledgeSpace]], int]:
    """Fuzzy-search documents by name across every space the user can read.

    Returns ``(rows, total)`` where each row is ``(document, space)`` and
    ``total`` counts the matches without pagination. Only non-temporary
    documents in active (non-archived) spaces are included. When ``queryable``
    is true, a document must have a current published, ready, queryable
    version; this intentionally does not use the latest version's stage.
    """
    needle = (query_text or "").strip()
    if not needle:
        return [], 0
    visible_spaces = select(KnowledgeSpace.id).where(
        KnowledgeSpace.status == "active"
    )
    if not is_admin:
        visible_spaces = visible_spaces.where(
            or_(
                KnowledgeSpace.owner_user_id == user_id,
                KnowledgeSpace.id.in_(
                    select(SpaceMember.space_id).where(
                        SpaceMember.user_id == user_id
                    )
                ),
            )
        )
    visible = visible_spaces.scalar_subquery()

    latest_number = (
        select(func.max(DocumentVersion.version_number))
        .where(DocumentVersion.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )
    latest = aliased(DocumentVersion)
    base = (
        select(Document, KnowledgeSpace)
        .join(
            KnowledgeSpace,
            KnowledgeSpace.id == Document.space_id,
        )
        .join(
            latest,
            and_(
                latest.document_id == Document.id,
                latest.version_number == latest_number,
            ),
        )
        .where(
            Document.space_id.is_not(None),
            Document.space_id.in_(visible),
            Document.status.notin_(("archived", "deleted")),
            Document.is_temporary.is_(False),
            Document.name.ilike(f"%{_like_pattern(needle)}%", escape="\\"),
        )
    )
    if queryable is True:
        published = aliased(DocumentVersion)
        base = base.join(
            published,
            and_(
                published.document_id == Document.id,
                published.is_current_published.is_(True),
                published.queryable.is_(True),
                published.processing_stage == "ready",
            ),
        )
    elif queryable is False:
        pub = aliased(DocumentVersion)
        base = base.where(
            ~exists().where(
                and_(
                    pub.document_id == Document.id,
                    pub.is_current_published.is_(True),
                    pub.queryable.is_(True),
                    pub.processing_stage == "ready",
                )
            )
        )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        base.order_by(Document.updated_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [(row[0], row[1]) for row in rows], total


def _document_list_query(
    db: Session,
    *,
    space_id: str,
    category_id: str | None,
    status: str | None,
    query_text: str | None,
    queryable: bool | None = None,
):
    latest_number = (
        select(func.max(DocumentVersion.version_number))
        .where(DocumentVersion.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )
    latest = aliased(DocumentVersion)
    query = (
        select(Document)
        .join(
            latest,
            and_(
                latest.document_id == Document.id,
                latest.version_number == latest_number,
            ),
        )
        .where(
            Document.space_id == space_id,
            Document.status.notin_(("archived", "deleted")),
        )
    )
    if category_id:
        query = query.where(Document.category_id == category_id)
    if status == "processing":
        query = query.where(latest.processing_stage.in_(PROCESSING_STAGES))
    elif status:
        query = query.where(latest.processing_stage == status)
    if queryable is True:
        published = aliased(DocumentVersion)
        query = query.join(
            published,
            and_(
                published.document_id == Document.id,
                published.is_current_published.is_(True),
                published.queryable.is_(True),
                published.processing_stage == "ready",
            ),
        )
    elif queryable is False:
        pub = aliased(DocumentVersion)
        query = query.where(
            ~exists().where(
                and_(
                    pub.document_id == Document.id,
                    pub.is_current_published.is_(True),
                    pub.queryable.is_(True),
                    pub.processing_stage == "ready",
                )
            )
        )
    if query_text:
        query = query.where(Document.name.ilike(f"%{query_text}%"))
    return query


def count_documents(
    db: Session,
    *,
    space_id: str,
    category_id: str | None,
    status: str | None,
    query_text: str | None,
    queryable: bool | None = None,
) -> int:
    query = _document_list_query(
        db,
        space_id=space_id,
        category_id=category_id,
        status=status,
        query_text=query_text,
        queryable=queryable,
    )
    return db.scalar(select(func.count()).select_from(query.subquery())) or 0


def count_documents_by_category(
    db: Session,
    *,
    space_id: str,
) -> tuple[int, dict[str, int]]:
    """Count visible documents once per document, grouped by direct category."""
    query = _document_list_query(
        db,
        space_id=space_id,
        category_id=None,
        status=None,
        query_text=None,
        queryable=None,
    ).subquery()
    rows = db.execute(
        select(
            query.c.category_id,
            func.count().label("document_count"),
        ).group_by(query.c.category_id)
    ).all()
    by_category: dict[str, int] = {}
    all_count = 0
    for category_id, document_count in rows:
        count = int(document_count or 0)
        all_count += count
        if category_id is not None:
            by_category[str(category_id)] = count
    return all_count, by_category


def list_documents(
    db: Session,
    *,
    space_id: str,
    category_id: str | None,
    status: str | None,
    query_text: str | None,
    cursor: str | None,
    queryable: bool | None = None,
    offset: int | None = None,
    limit: int | None = None,
) -> tuple[list[Document], str | None]:
    if cursor is not None and (offset is not None or limit is not None):
        raise ValueError("cursor and offset/limit are mutually exclusive")
    query = _document_list_query(
        db,
        space_id=space_id,
        category_id=category_id,
        status=status,
        query_text=query_text,
        queryable=queryable,
    )
    if offset is not None or limit is not None:
        effective_offset = offset or 0
        effective_limit = limit or PAGE_SIZE
        query = (
            query.order_by(Document.updated_at.desc(), Document.id.desc())
            .offset(effective_offset)
            .limit(effective_limit)
        )
        return list(db.scalars(query)), None

    cursor_position = decode_cursor(cursor)
    if cursor_position:
        updated_at, document_id = cursor_position
        query = query.where(
            or_(
                Document.updated_at < updated_at,
                and_(Document.updated_at == updated_at, Document.id < document_id),
            )
        )
    query = query.order_by(Document.updated_at.desc(), Document.id.desc()).limit(
        PAGE_SIZE + 1
    )
    documents = list(db.scalars(query))
    next_cursor = (
        encode_cursor(documents[PAGE_SIZE - 1]) if len(documents) > PAGE_SIZE else None
    )
    return documents[:PAGE_SIZE], next_cursor


def archive_document(document: Document) -> Document:
    document.status = "archived"
    document.updated_at = now()
    return document


def delete_document(db: Session, document: Document) -> list[str]:
    """Soft-delete a document and cancel its in-flight processing jobs."""
    paths = [
        (version.file_path or "").strip()
        for version in versions_for_document(db, document.id)
        if (version.file_path or "").strip()
    ]
    cancel_jobs_for_document(db, document.id)
    document.status = "deleted"
    document.updated_at = now()
    return paths


def batch_delete_documents(
    db: Session, space_id: str, document_ids: list[str]
) -> tuple[int, list[str]]:
    """Soft-delete multiple documents in a space; return (count, file_paths)."""
    docs = list(
        db.scalars(
            select(Document).where(
                Document.space_id == space_id,
                Document.id.in_(document_ids),
                Document.status != "deleted",
            )
        ).all()
    )
    all_paths: list[str] = []
    for doc in docs:
        paths = delete_document(db, doc)
        all_paths.extend(paths)
    return len(docs), all_paths


def is_retryable(db: Session, version: DocumentVersion) -> bool:
    has_retry = (
        db.scalar(
            select(DocumentVersion.id).where(
                DocumentVersion.retry_of_version_id == version.id
            )
        )
        is not None
    )
    return version.processing_stage == "failed" and version.retryable and not has_retry


def retry_failed_version(
    db: Session, document: Document, failed_version: DocumentVersion
) -> DocumentVersion:
    created_at = now()
    next_number = (
        db.scalar(
            select(func.max(DocumentVersion.version_number)).where(
                DocumentVersion.document_id == document.id
            )
        )
        or 0
    ) + 1
    retry = DocumentVersion(
        id=f"ver_{new_token(10)}",
        document_id=document.id,
        version_number=next_number,
        processing_stage="queued",
        queryable=False,
        retryable=False,
        retry_of_version_id=failed_version.id,
        source_relative_path=failed_version.source_relative_path,
        file_path=failed_version.file_path,
        is_current_published=False,
        created_at=created_at,
        updated_at=created_at,
    )
    document.updated_at = created_at
    db.add(retry)
    db.flush()
    return retry


def update_version_stage(
    db: Session,
    version: DocumentVersion,
    stage: str,
    *,
    failure_code: str | None = None,
    failure_message: str | None = None,
) -> DocumentVersion:
    """Transition version to a new processing stage."""
    version.processing_stage = stage
    version.updated_at = now()
    if stage == "failed":
        version.retryable = True
        version.failure_code = failure_code
        version.failure_message = failure_message
    elif stage == "ready":
        version.queryable = True
        version.is_current_published = True
    db.flush()
    return version


def persist_temporary_document(
    db: Session,
    document: Document,
    *,
    space_id: str,
    category_id: str | None,
) -> Document:
    """Promote a session-scoped temporary attachment into a knowledge space."""
    document.space_id = space_id
    document.is_temporary = False
    document.chat_session_id = None
    document.expires_at = None
    document.category_id = category_id
    document.updated_at = now()
    db.flush()
    return document


def move_document_to_space(
    db: Session,
    document: Document,
    *,
    space_id: str,
    category_id: str | None,
) -> bool:
    """以原空间和更新时间比较交换，跨进程串行化同一文档的移动。"""
    stamp = now()
    claimed = db.execute(
        update(Document)
        .where(
            Document.id == document.id,
            Document.space_id == document.space_id,
            Document.updated_at == document.updated_at,
            Document.status == "active",
        )
        .values(space_id=space_id, category_id=category_id, updated_at=stamp)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        return False
    db.refresh(document)
    for version in document.versions:
        if version.processing_stage == "ready":
            version.is_current_published = False
            version.queryable = False
            version.processing_stage = "queued"
            version.updated_at = stamp
    db.execute(
        update(DocumentJob)
        .where(DocumentJob.document_id == document.id)
        .values(space_id=space_id, updated_at=stamp)
    )
    db.execute(
        update(DocumentUploadSession)
        .where(DocumentUploadSession.document_id == document.id)
        .values(space_id=space_id, category_id=category_id, updated_at=stamp)
    )
    db.flush()
    return True


def invalidate_moved_document_graph(
    db: Session, *, source_space_id: str, version_ids: list[str]
) -> list[str]:
    """整条移除来源受影响的源实体，避免混合描述继续泄露移出文档。"""
    moved_ids = set(version_ids)
    if not moved_ids:
        return []
    links_by_entity: dict[str, set[str]] = {}
    for entity_id, version_id in db.execute(
        select(KnowledgeEntityDocument.entity_id, KnowledgeEntityDocument.document_version_id)
        .join(KnowledgeEntity, KnowledgeEntity.id == KnowledgeEntityDocument.entity_id)
        .where(KnowledgeEntity.space_id == source_space_id)
    ):
        links_by_entity.setdefault(entity_id, set()).add(version_id)

    invalid_ids: list[str] = []
    other_version_ids: set[str] = set()
    for entity_id, raw_sources in db.execute(
        select(KnowledgeEntity.id, KnowledgeEntity.source_document_version_ids_json)
        .where(KnowledgeEntity.space_id == source_space_id)
    ):
        try:
            sources = json.loads(raw_sources or "[]")
        except (ValueError, TypeError):
            sources = None
        # 来源损坏时无法证明混合正文安全，按失效处理，不猜测正文归属。
        invalid_sources = not isinstance(sources, list) or any(
            not isinstance(value, str) for value in sources
        )
        source_ids = set() if invalid_sources else set(sources)
        source_ids.update(links_by_entity.get(entity_id, set()))
        if invalid_sources or not source_ids or source_ids.intersection(moved_ids):
            invalid_ids.append(entity_id)
            other_version_ids.update(source_ids - moved_ids)

    db.execute(delete(KnowledgeEntityDocument).where(
        KnowledgeEntityDocument.document_version_id.in_(moved_ids),
        KnowledgeEntityDocument.entity_id.in_(
            select(KnowledgeEntity.id).where(KnowledgeEntity.space_id == source_space_id)
        ),
    ))
    if invalid_ids:
        # 显式清关系与链接，不依赖调用环境是否启用了外键级联。
        db.execute(delete(KnowledgeRelation).where(or_(
            KnowledgeRelation.subject_entity_id.in_(invalid_ids),
            KnowledgeRelation.object_entity_id.in_(invalid_ids),
        )))
        db.execute(delete(KnowledgeEntityDocument).where(
            KnowledgeEntityDocument.entity_id.in_(invalid_ids)
        ))
        db.execute(delete(KnowledgeEntity).where(
            KnowledgeEntity.space_id == source_space_id,
            KnowledgeEntity.id.in_(invalid_ids),
        ))
    db.flush()
    if not other_version_ids:
        return []
    return list(db.scalars(
        select(DocumentVersion.id).join(Document, Document.id == DocumentVersion.document_id)
        .where(
            DocumentVersion.id.in_(other_version_ids),
            Document.space_id == source_space_id,
            Document.status == "active",
        )
        .order_by(DocumentVersion.id)
    ))


def complete_version(
    db: Session,
    version: DocumentVersion,
    *,
    chunk_count: int = 0,
    summary: str | None = None,
    is_current_published: bool = True,
) -> DocumentVersion:
    """Mark version as ready with chunk metadata and optional summary."""
    version.processing_stage = "ready"
    version.queryable = True
    version.is_current_published = is_current_published
    version.chunk_count = chunk_count
    if summary is not None:
        version.summary = summary.strip() or None
    version.updated_at = now()
    db.flush()
    return version


def _cursor_timestamp(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def encode_cursor(document: Document) -> str:
    payload = json.dumps(
        {
            "version": 1,
            "updated_at": _cursor_timestamp(document.updated_at).isoformat(),
            "id": document.id,
        },
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(
        settings.jwt_secret.encode(), encoded.encode(), hashlib.sha256
    ).digest()
    signed = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"v1.{encoded}.{signed}"


def decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        version, encoded, signed = cursor.split(".")
        if version != "v1":
            raise ValueError
        expected = hmac.new(
            settings.jwt_secret.encode(), encoded.encode(), hashlib.sha256
        ).digest()
        # Compare the canonical base64url form as well as the MAC.  Decoding first
        # accepts equivalent non-canonical final sextets, so a one-character edit
        # can otherwise preserve the decoded signature bytes.
        expected_signed = base64.urlsafe_b64encode(expected).decode().rstrip("=")
        if not hmac.compare_digest(expected_signed, signed):
            raise ValueError
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        timestamp = datetime.fromisoformat(payload["updated_at"])
        document_id = payload["id"]
        if (
            payload["version"] != 1
            or not isinstance(document_id, str)
            or not document_id
        ):
            raise ValueError
        return _cursor_timestamp(timestamp), document_id
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise ValueError("invalid cursor") from exc
