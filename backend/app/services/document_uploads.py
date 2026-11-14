"""Durable knowledge-document multipart uploads.

The browser uploads only private MinIO parts.  A durable dispatcher verifies the
assembled object, creates the normal document/version/job checkpoint, promotes
one immutable canonical object, and then invokes the existing document pipeline
without holding request bytes or a database transaction during storage I/O.
"""

from __future__ import annotations

import hashlib
import logging
import os
import socket
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import transaction
from app.core.security import new_token, now
from app.models.auth import User
from app.models.access import DocumentCategory, KnowledgeSpace, SpaceMember
from app.models.documents import Document, DocumentJob, DocumentVersion
from app.models.document_uploads import DocumentUploadSession
from app.repositories import documents
from app.services.authorization import PermissionService
from app.services import minio_storage
from app.services.document_processor import (
    normalize_source_relative_path,
    validate_file,
)

logger = logging.getLogger(__name__)

UPLOAD_STATUSES = frozenset(
    {
        "uploading",
        "queued",
        "verifying",
        "processing",
        "completed",
        "failed",
        "aborted",
        "expired",
    }
)
ACTIVE_STATUSES = frozenset({"uploading", "queued", "verifying", "processing"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "aborted", "expired"})


class DocumentUploadError(RuntimeError):
    """Safe service error carrying an HTTP status for API callers."""

    def __init__(
        self, detail: str, status_code: int = 409, code: str = "process"
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


class UploadManifestError(DocumentUploadError):
    pass


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _max_bytes() -> int:
    return max(1, int(settings.document_upload_max_mb)) * 1024 * 1024


def _part_size() -> int:
    # The same-origin nginx staging route accepts at most 16 MiB per PUT.
    return min(16, max(5, int(settings.document_upload_part_size_mb))) * 1024 * 1024


def _ttl_seconds() -> int:
    return max(300, int(settings.document_upload_ttl_seconds))


def _part_url_ttl() -> int:
    return max(60, int(settings.document_upload_part_url_ttl_seconds))


def _lease_seconds() -> int:
    return max(30, int(settings.document_upload_lease_seconds))


def _part_count(size_bytes: int, part_size: int) -> int:
    return max(1, (size_bytes + part_size - 1) // part_size)


def _safe_error(code: str) -> str:
    return {
        "disabled": "大文件上传当前不可用，请稍后重试。",
        "permission": "知识空间权限已变化，请重新上传。",
        "expired": "上传会话已过期，请重新上传。",
        "aborted": "上传已取消。",
        "missing_parts": "上传分片不完整，请继续上传后重试。",
        "invalid_parts": "上传分片大小不正确，请重新上传。",
        "checksum": "文件校验失败，请重新上传。",
        "size": "文件大小校验失败，请重新上传。",
        "archive": "文件安全检查失败，请重新上传。",
        "storage": "文件存储暂时不可用，请稍后重试。",
        "process": "文档处理失败，请重试。",
        "missing_object": "上传文件已丢失，请重新上传。",
        "lease": "上传任务已由其他工作进程接管。",
    }.get(code, "文档上传失败，请重试。")


def upload_policy() -> dict[str, int | bool]:
    enabled = bool(
        settings.document_uploads_enabled and minio_storage.is_minio_enabled()
    )
    return {
        "enabled": enabled,
        "max_file_bytes": _max_bytes(),
        "part_size": _part_size(),
        "expires_in_seconds": _ttl_seconds(),
    }


def _require_enabled() -> None:
    if not settings.document_uploads_enabled:
        raise DocumentUploadError(_safe_error("disabled"), 503)
    if not minio_storage.is_minio_enabled():
        raise DocumentUploadError(_safe_error("disabled"), 503)


def _space_access(db: Session, user_id: str, space_id: str) -> None:
    user = db.get(User, user_id)
    if not user or user.status != "active":
        raise DocumentUploadError(_safe_error("permission"), 404, "permission")
    try:
        PermissionService.require_space_member_access(
            db, user, space_id, minimum_role="contributor"
        )
    except HTTPException as exc:
        raise DocumentUploadError(
            _safe_error("permission"), exc.status_code, "permission"
        ) from exc


def _session_access(db: Session, session: DocumentUploadSession, user_id: str) -> None:
    if session.owner_user_id != user_id:
        raise DocumentUploadError("上传会话不存在", 404)
    _space_access(db, user_id, session.space_id)


def _session_key(session: DocumentUploadSession) -> tuple[str, str]:
    bucket = (session.staging_bucket or "").strip()
    key = (session.staging_object_key or "").strip()
    if not bucket or not key or not key.startswith("document-uploads/"):
        raise DocumentUploadError(_safe_error("storage"), 500)
    return bucket, key


def _canonical_key(session: DocumentUploadSession) -> tuple[str, str]:
    bucket = (session.canonical_bucket or "").strip()
    key = (session.canonical_object_key or "").strip()
    if not bucket or not key:
        raise DocumentUploadError(_safe_error("storage"), 500)
    return bucket, key


def _expected_part_size(session: DocumentUploadSession, part_number: int) -> int:
    if part_number < 1 or part_number > session.part_count:
        raise UploadManifestError("分片编号超出范围", 422, "invalid_parts")
    if part_number < session.part_count:
        return session.part_size
    return session.size_bytes - session.part_size * (session.part_count - 1)


def _parts_for_session(session: DocumentUploadSession) -> list[dict[str, int | str]]:
    upload_id = (session.multipart_upload_id or "").strip()
    if not upload_id or not (session.staging_object_key or "").strip():
        return []
    bucket = (session.staging_bucket or "").strip() or None
    return minio_storage.list_multipart_parts(
        object_key=session.staging_object_key,
        upload_id=upload_id,
        bucket=bucket,
    )


def _validate_parts(
    session: DocumentUploadSession, parts: list[dict[str, int | str]]
) -> None:
    expected_numbers = set(range(1, session.part_count + 1))
    actual_numbers = [int(item["part_number"]) for item in parts]
    if (
        len(actual_numbers) != len(set(actual_numbers))
        or set(actual_numbers) != expected_numbers
    ):
        raise UploadManifestError(_safe_error("missing_parts"), 409, "missing_parts")
    by_number = {int(item["part_number"]): item for item in parts}
    for part_number in range(1, session.part_count + 1):
        actual = int(by_number[part_number]["size_bytes"])
        expected = _expected_part_size(session, part_number)
        if actual != expected:
            raise UploadManifestError(
                _safe_error("invalid_parts"), 409, "invalid_parts"
            )


def _hash_file(path: Path, expected_size: int) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(4 * 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > expected_size:
                raise DocumentUploadError(_safe_error("size"), 422, "size")
            digest.update(chunk)
    if total != expected_size:
        raise DocumentUploadError(_safe_error("size"), 422, "size")
    return digest.hexdigest()


def _validate_archive(path: Path, filename: str) -> None:
    if Path(filename).suffix.lower() != ".pptx":
        return
    from app.services.document_processor import validate_pptx_archive

    try:
        validate_pptx_archive(path)
    except Exception as exc:
        logger.info("PPTX upload archive rejected: %s", type(exc).__name__)
        raise DocumentUploadError(_safe_error("archive"), 422, "archive") from exc


def _new_session_id() -> str:
    return f"upl_{new_token(12)}"


def _load_session(db: Session, session_id: str) -> DocumentUploadSession:
    session = db.get(DocumentUploadSession, session_id)
    if not session:
        raise DocumentUploadError("上传会话不存在", 404)
    return session


def _same_upload_payload(
    session: DocumentUploadSession,
    *,
    space_id: str,
    filename: str,
    size_bytes: int,
    sha256: str,
    name: str,
    category_id: str | None,
    source_relative_path: str,
) -> bool:
    return (
        session.space_id == space_id
        and session.filename == filename
        and session.size_bytes == size_bytes
        and session.sha256 == sha256
        and session.name == name
        and session.category_id == category_id
        and session.source_relative_path == source_relative_path
    )


def create_upload_session(
    *,
    user_id: str,
    space_id: str,
    client_upload_id: str,
    filename: str,
    size_bytes: int,
    sha256: str,
    name: str | None = None,
    category_id: str | None = None,
    source_relative_path: str | None = None,
) -> DocumentUploadSession:
    """Validate metadata, initiate private multipart storage, and persist a row."""
    _require_enabled()
    client_upload_id = (client_upload_id or "").strip()
    filename = (filename or "").strip()
    sha256 = (sha256 or "").strip()
    if not client_upload_id or len(client_upload_id) > 128:
        raise DocumentUploadError("client_upload_id 无效", 422)
    if not filename or len(filename) > 255:
        raise DocumentUploadError("文件名无效", 422)
    if size_bytes < 1 or size_bytes > _max_bytes():
        raise DocumentUploadError(
            f"文件大小超过限制（{settings.document_upload_max_mb} MB）。", 422
        )
    if len(sha256) != 64 or sha256.lower() != sha256:
        raise DocumentUploadError("sha256 必须是 64 位小写十六进制摘要", 422)
    try:
        int(sha256, 16)
    except ValueError as exc:
        raise DocumentUploadError("sha256 必须是 64 位小写十六进制摘要", 422) from exc
    try:
        normalized_source_path = normalize_source_relative_path(
            source_relative_path, filename
        )
    except ValueError as exc:
        raise DocumentUploadError(str(exc), 422) from exc
    validation_error = validate_file(filename, size_bytes, max_bytes=_max_bytes())
    if validation_error:
        raise DocumentUploadError(validation_error, 422)
    part_size = _part_size()
    part_count = _part_count(size_bytes, part_size)
    doc_name = (name or "").strip() or Path(filename).stem
    if len(doc_name) > 255:
        raise DocumentUploadError("文档名称过长", 422)
    category_id = (category_id or "").strip() or None

    with transaction() as db:
        _space_access(db, user_id, space_id)
        existing = db.scalar(
            select(DocumentUploadSession).where(
                DocumentUploadSession.owner_user_id == user_id,
                DocumentUploadSession.client_upload_id == client_upload_id,
            )
        )
        if existing:
            if not _same_upload_payload(
                existing,
                space_id=space_id,
                filename=filename,
                size_bytes=size_bytes,
                sha256=sha256,
                name=doc_name,
                category_id=category_id,
                source_relative_path=normalized_source_path,
            ):
                raise DocumentUploadError(
                    "client_upload_id 已用于不同文件，不能复用。", 409
                )
            return existing
        if category_id and not db.scalar(
            select(DocumentCategory.id).where(
                DocumentCategory.id == category_id,
                DocumentCategory.space_id == space_id,
            )
        ):
            raise DocumentUploadError("文档分类不存在", 404)
        active_ids = list(
            db.scalars(
                select(DocumentUploadSession.id)
                .where(
                    DocumentUploadSession.owner_user_id == user_id,
                    DocumentUploadSession.status.in_(tuple(ACTIVE_STATUSES)),
                )
                .limit(int(settings.document_upload_max_sessions_per_user) + 1)
            ).all()
        )
        if len(active_ids) >= int(settings.document_upload_max_sessions_per_user):
            raise DocumentUploadError("上传中的文件过多，请稍后再试。", 429)

    cfg = minio_storage.load_minio_settings()
    if cfg is None:
        raise DocumentUploadError(_safe_error("disabled"), 503)
    session_id = _new_session_id()
    digest_key = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:16]
    staging_key = f"document-uploads/{user_id}/{session_id}/{digest_key}"
    upload_id = ""
    try:
        upload_id = minio_storage.initiate_multipart_upload(
            object_key=staging_key,
            bucket=cfg.temp_bucket,
            metadata={"X-Amz-Meta-Sha256": sha256},
        )
        stamp = now()
        created_session: DocumentUploadSession | None = None
        with transaction() as db:
            _space_access(db, user_id, space_id)
            existing = db.scalar(
                select(DocumentUploadSession).where(
                    DocumentUploadSession.owner_user_id == user_id,
                    DocumentUploadSession.client_upload_id == client_upload_id,
                )
            )
            if existing:
                if not _same_upload_payload(
                    existing,
                    space_id=space_id,
                    filename=filename,
                    size_bytes=size_bytes,
                    sha256=sha256,
                    name=doc_name,
                    category_id=category_id,
                    source_relative_path=normalized_source_path,
                ):
                    raise DocumentUploadError(
                        "client_upload_id 已用于不同文件，不能复用。", 409
                    )
                created_session = existing
            else:
                if category_id and not db.scalar(
                    select(DocumentCategory.id).where(
                        DocumentCategory.id == category_id,
                        DocumentCategory.space_id == space_id,
                    )
                ):
                    raise DocumentUploadError("文档分类不存在", 404)
                active_ids = list(
                    db.scalars(
                        select(DocumentUploadSession.id)
                        .where(
                            DocumentUploadSession.owner_user_id == user_id,
                            DocumentUploadSession.status.in_(tuple(ACTIVE_STATUSES)),
                        )
                        .limit(int(settings.document_upload_max_sessions_per_user) + 1)
                    ).all()
                )
                if len(active_ids) >= int(
                    settings.document_upload_max_sessions_per_user
                ):
                    raise DocumentUploadError("上传中的文件过多，请稍后再试。", 429)
                created_session = DocumentUploadSession(
                    id=session_id,
                    owner_user_id=user_id,
                    space_id=space_id,
                    client_upload_id=client_upload_id,
                    filename=filename,
                    size_bytes=size_bytes,
                    sha256=sha256,
                    name=doc_name,
                    category_id=category_id,
                    source_relative_path=normalized_source_path,
                    part_size=part_size,
                    part_count=part_count,
                    staging_bucket=cfg.temp_bucket,
                    staging_object_key=staging_key,
                    multipart_upload_id=upload_id,
                    status="uploading",
                    expires_at=stamp + timedelta(seconds=_ttl_seconds()),
                    next_attempt_at=None,
                    created_at=stamp,
                    updated_at=stamp,
                )
                db.add(created_session)
                db.flush()
        assert created_session is not None
        if created_session.id != session_id:
            try:
                minio_storage.abort_multipart_upload(
                    object_key=staging_key,
                    upload_id=upload_id,
                    bucket=cfg.temp_bucket,
                )
            except Exception:
                logger.warning("orphan multipart abort failed", exc_info=True)
        return created_session
    except IntegrityError as exc:
        if upload_id:
            try:
                minio_storage.abort_multipart_upload(
                    object_key=staging_key,
                    upload_id=upload_id,
                    bucket=cfg.temp_bucket,
                )
            except Exception:
                logger.warning("orphan multipart abort failed", exc_info=True)
        with transaction() as db:
            _space_access(db, user_id, space_id)
            existing = db.scalar(
                select(DocumentUploadSession).where(
                    DocumentUploadSession.owner_user_id == user_id,
                    DocumentUploadSession.client_upload_id == client_upload_id,
                )
            )
            if existing and _same_upload_payload(
                existing,
                space_id=space_id,
                filename=filename,
                size_bytes=size_bytes,
                sha256=sha256,
                name=doc_name,
                category_id=category_id,
                source_relative_path=normalized_source_path,
            ):
                return existing
            if existing:
                raise DocumentUploadError(
                    "client_upload_id 已用于不同文件，不能复用。", 409
                ) from exc
        raise DocumentUploadError("上传会话创建冲突，请重试。", 409) from exc
    except Exception:
        if upload_id:
            try:
                minio_storage.abort_multipart_upload(
                    object_key=staging_key,
                    upload_id=upload_id,
                    bucket=cfg.temp_bucket,
                )
            except Exception:
                logger.warning(
                    "multipart cleanup after create failure failed", exc_info=True
                )
        raise


def get_session(session_id: str, user_id: str) -> dict[str, Any]:
    with transaction() as db:
        session = db.scalar(
            select(DocumentUploadSession)
            .where(DocumentUploadSession.id == session_id)
            .with_for_update(of=DocumentUploadSession)
            .execution_options(populate_existing=True)
        )
        if session is None:
            raise DocumentUploadError("上传会话不存在", 404)
        _session_access(db, session, user_id)
        stamp = now()
        expires_at = _utc(session.expires_at) or datetime.min.replace(
            tzinfo=timezone.utc
        )
        lease_expires_at = _utc(session.lease_expires_at) or datetime.min.replace(
            tzinfo=timezone.utc
        )
        lease_live = (
            session.status in {"verifying", "processing"} and lease_expires_at > stamp
        )
        if session.status in ACTIVE_STATUSES and expires_at <= stamp and not lease_live:
            _expire_session_checkpoint(db, session)
        return _serialize_session(db, session, user_id)


def sign_part(session_id: str, part_number: int, user_id: str) -> dict[str, Any]:
    _require_enabled()
    expired = False
    signed: dict[str, Any] | None = None
    with transaction() as db:
        session = _load_session(db, session_id)
        _session_access(db, session, user_id)
        if session.status != "uploading":
            if session.status in TERMINAL_STATUSES:
                raise DocumentUploadError(_safe_error(session.status), 409)
            raise DocumentUploadError("上传会话正在处理中，请稍后再试。", 409)
        if (
            _utc(session.expires_at) or datetime.min.replace(tzinfo=timezone.utc)
        ) <= now():
            _expire_session_locked(session)
            expired = True
        else:
            _expected_part_size(session, part_number)
            bucket = (session.staging_bucket or "").strip()
            key = (session.staging_object_key or "").strip()
            upload_id = (session.multipart_upload_id or "").strip()
            if not bucket or not key or not upload_id:
                raise DocumentUploadError(_safe_error("storage"), 500)
            url, headers = minio_storage.presign_upload_part(
                bucket=bucket,
                object_key=key,
                upload_id=upload_id,
                part_number=part_number,
                expires_seconds=_part_url_ttl(),
                public_url=settings.document_upload_public_url,
            )
            signed = {"url": url, "headers": headers}
    if expired:
        raise DocumentUploadError(_safe_error("expired"), 410, "expired")
    assert signed is not None
    return signed


def _expire_session_locked(session: DocumentUploadSession) -> None:
    if session.status in TERMINAL_STATUSES:
        return
    session.status = "expired"
    session.error = _safe_error("expired")
    session.lease_owner = None
    session.lease_expires_at = None
    session.updated_at = now()


def _expire_session_checkpoint(db: Session, session: DocumentUploadSession) -> None:
    if session.status in TERMINAL_STATUSES:
        return
    stamp = now()
    expired = db.execute(
        update(DocumentUploadSession)
        .where(
            DocumentUploadSession.id == session.id,
            DocumentUploadSession.status == session.status,
            DocumentUploadSession.expires_at == session.expires_at,
            DocumentUploadSession.lease_owner == session.lease_owner,
            DocumentUploadSession.lease_expires_at == session.lease_expires_at,
        )
        .values(
            status="expired",
            error=_safe_error("expired"),
            lease_owner=None,
            lease_expires_at=None,
            updated_at=stamp,
        )
        .execution_options(synchronize_session=False)
    )
    db.refresh(session)
    if expired.rowcount != 1:
        return
    if session.version_id:
        version = db.get(DocumentVersion, session.version_id)
        if version and version.processing_stage in documents.PROCESSING_STAGES:
            documents.update_version_stage(
                db,
                version,
                "failed",
                failure_code="process_timeout",
                failure_message="文档处理超时，请重试。",
            )
    if session.job_id:
        job = db.get(DocumentJob, session.job_id)
        if job and job.status not in documents.TERMINAL_JOB_STATUSES:
            job.status = "failed"
            job.updated_at = now()


def complete_session(session_id: str, user_id: str) -> dict[str, Any]:
    claim_token = new_token(24)
    expired = False
    with transaction() as db:
        session = _load_session(db, session_id)
        _session_access(db, session, user_id)
        stamp = now()
        if (
            session.status == "queued"
            and (_utc(session.expires_at) or datetime.min.replace(tzinfo=timezone.utc))
            <= stamp
        ):
            _expire_session_locked(session)
            expired = True
        elif session.status in {"queued", "verifying", "processing", "completed"}:
            return _serialize_session(db, session, user_id)
        elif session.status in {"aborted", "expired", "failed"}:
            raise DocumentUploadError(_safe_error(session.status), 409)
        elif (
            _utc(session.expires_at) or datetime.min.replace(tzinfo=timezone.utc)
        ) <= stamp:
            _expire_session_locked(session)
            expired = True
        else:
            bucket, key = _session_key(session)
            upload_id = session.multipart_upload_id or ""
            result = db.execute(
                update(DocumentUploadSession)
                .execution_options(synchronize_session="fetch")
                .where(
                    DocumentUploadSession.id == session_id,
                    DocumentUploadSession.status == "uploading",
                    DocumentUploadSession.expires_at > stamp,
                )
                .values(
                    status="verifying",
                    lease_owner=claim_token,
                    lease_expires_at=stamp + timedelta(seconds=_lease_seconds()),
                    updated_at=stamp,
                )
            )
            if int(getattr(result, "rowcount", 0)) != 1:
                current = db.get(DocumentUploadSession, session_id)
                if current and current.status in {
                    "queued",
                    "verifying",
                    "processing",
                    "completed",
                }:
                    return _serialize_session(db, current, user_id)
                raise DocumentUploadError("上传会话状态已变化，请重试。", 409)
            session.status = "verifying"
            session.lease_owner = claim_token
            session.lease_expires_at = stamp + timedelta(seconds=_lease_seconds())
            session.updated_at = stamp

    if expired:
        raise DocumentUploadError(_safe_error("expired"), 410, "expired")

    try:
        if not upload_id:
            raise DocumentUploadError(_safe_error("storage"), 500)
        parts = minio_storage.list_multipart_parts(
            object_key=key, upload_id=upload_id, bucket=bucket
        )
        _validate_parts(session, parts)
    except UploadManifestError:
        with transaction() as db:
            current = db.get(DocumentUploadSession, session_id)
            if (
                current
                and current.status == "verifying"
                and current.lease_owner == claim_token
            ):
                current.status = "uploading"
                current.error = None
                current.lease_owner = None
                current.lease_expires_at = None
                current.updated_at = now()
        raise
    except Exception as exc:
        with transaction() as db:
            current = db.get(DocumentUploadSession, session_id)
            if (
                current
                and current.status == "verifying"
                and current.lease_owner == claim_token
            ):
                current.status = "uploading"
                current.error = _safe_error("storage")
                current.lease_owner = None
                current.lease_expires_at = None
                current.updated_at = now()
        logger.info("multipart manifest listing failed: %s", type(exc).__name__)
        raise DocumentUploadError(_safe_error("storage"), 502) from exc

    try:
        minio_storage.complete_multipart_upload(
            object_key=key, upload_id=upload_id, parts=parts, bucket=bucket
        )
    except Exception as exc:
        # A completed object is safe to resume after a response/network failure.
        try:
            stat = minio_storage.stat_object(object_key=key, bucket=bucket)
            if int(getattr(stat, "size", -1)) != session.size_bytes:
                raise RuntimeError("staging object has wrong size")
        except Exception:
            with transaction() as db:
                current = db.get(DocumentUploadSession, session_id)
                if (
                    current
                    and current.status == "verifying"
                    and current.lease_owner == claim_token
                ):
                    current.status = "uploading"
                    current.error = _safe_error("storage")
                    current.lease_owner = None
                    current.lease_expires_at = None
                    current.updated_at = now()
            logger.info("multipart completion failed: %s", type(exc).__name__)
            raise DocumentUploadError(_safe_error("storage"), 502) from exc

    with transaction() as db:
        current = db.get(DocumentUploadSession, session_id)
        if not current:
            raise DocumentUploadError("上传会话不存在", 404)
        if current.status == "aborted":
            raise DocumentUploadError(_safe_error("aborted"), 409)
        if current.status == "verifying" and current.lease_owner == claim_token:
            current.status = "queued"
            current.error = None
            current.next_attempt_at = now()
            current.lease_owner = None
            current.lease_expires_at = None
            current.updated_at = now()
        return _serialize_session(db, current, user_id)


def abort_session(session_id: str, user_id: str) -> dict[str, Any]:
    object_info: tuple[str, str, str | None] | None = None
    with transaction() as db:
        session = _load_session(db, session_id)
        _session_access(db, session, user_id)
        if session.status in {"aborted", "expired", "failed", "completed"}:
            return _serialize_session(db, session, user_id)
        if (
            session.status in {"verifying", "processing"}
            and (
                _utc(session.lease_expires_at)
                or datetime.min.replace(tzinfo=timezone.utc)
            )
            > now()
        ):
            raise DocumentUploadError("上传任务正在处理，暂不能取消。", 409)
        if session.version_id or session.document_id or session.job_id:
            raise DocumentUploadError("文档处理已开始，暂不能取消。", 409)
        session.status = "aborted"
        session.error = _safe_error("aborted")
        session.lease_owner = None
        session.lease_expires_at = None
        session.updated_at = now()
        if session.staging_bucket and session.staging_object_key:
            object_info = (
                session.staging_bucket,
                session.staging_object_key,
                session.multipart_upload_id,
            )
        response = _serialize_session(db, session, user_id)

    if object_info:
        bucket, key, upload_id = object_info
        if upload_id:
            try:
                minio_storage.abort_multipart_upload(
                    object_key=key, upload_id=upload_id, bucket=bucket
                )
            except Exception:
                logger.info("multipart abort cleanup failed", exc_info=True)
        _delete_staging_object(bucket, key)
    return response


def _delete_staging_object(bucket: str, key: str) -> bool:
    if not key.startswith("document-uploads/"):
        return False
    try:
        minio_storage.delete_object(object_key=key, bucket=bucket)
        return True
    except Exception:
        logger.info(
            "staging object cleanup failed bucket=%s key=%s",
            bucket,
            key,
            exc_info=True,
        )
        return False


def _serialize_session(
    db: Session, session: DocumentUploadSession, user_id: str
) -> dict[str, Any]:
    uploaded_parts: list[dict[str, int | str]] = []
    if session.status in {"uploading", "verifying"} and session.multipart_upload_id:
        try:
            uploaded_parts = _parts_for_session(session)
        except Exception:
            logger.info("could not list upload parts for session %s", session.id)
    result: dict[str, Any] | None = None
    if session.document_id and session.job_id and session.version_id:
        document = db.get(Document, session.document_id)
        job = db.get(DocumentJob, session.job_id)
        if document and job:
            from app.api.documents import document_response, processing_task

            owner = db.get(User, user_id)
            if owner:
                result = {
                    "document": document_response(db, document),
                    "task": processing_task(db, job, owner),
                }
    return {
        "id": session.id,
        "status": session.status,
        "filename": session.filename,
        "size_bytes": session.size_bytes,
        "part_size": session.part_size,
        "expires_at": session.expires_at,
        "uploaded_parts": uploaded_parts,
        "error": session.error,
        "result": result,
    }


class _LeaseHeartbeat:
    def __init__(self, session_id: str, owner: str, lease_seconds: int) -> None:
        self.session_id = session_id
        self.owner = owner
        self.lease_seconds = lease_seconds
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="document-upload-lease", daemon=True
        )

    def _run(self) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while not self.stop.wait(interval):
            if not _renew_lease(self.session_id, self.owner, self.lease_seconds):
                return

    def __enter__(self) -> "_LeaseHeartbeat":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join(timeout=max(1.0, self.lease_seconds / 3))


def _renew_lease(session_id: str, owner: str, lease_seconds: int) -> bool:
    stamp = now()
    with transaction() as db:
        result = db.execute(
            update(DocumentUploadSession)
            .execution_options(synchronize_session="fetch")
            .where(
                DocumentUploadSession.id == session_id,
                DocumentUploadSession.lease_owner == owner,
                DocumentUploadSession.status.in_(("verifying", "processing")),
            )
            .values(
                lease_expires_at=stamp + timedelta(seconds=lease_seconds),
                updated_at=stamp,
            )
        )
        return int(getattr(result, "rowcount", 0)) == 1


def _lease_is_owned(
    session_id: str,
    owner: str,
    *,
    db: Session | None = None,
) -> bool:
    def check(current_db: Session) -> bool:
        session = current_db.scalar(
            select(DocumentUploadSession)
            .where(DocumentUploadSession.id == session_id)
            .with_for_update(of=DocumentUploadSession)
            .execution_options(populate_existing=True)
        )
        if (
            not session
            or session.lease_owner != owner
            or session.status != "processing"
        ):
            return False
        stamp = now()
        if (
            _utc(session.lease_expires_at) or datetime.min.replace(tzinfo=timezone.utc)
        ) <= stamp:
            return False

        user = current_db.scalar(
            select(User)
            .where(User.id == session.owner_user_id)
            .with_for_update(of=User)
            .execution_options(populate_existing=True)
        )
        if not user or user.status != "active":
            return False
        space = current_db.scalar(
            select(KnowledgeSpace)
            .where(KnowledgeSpace.id == session.space_id)
            .with_for_update(of=KnowledgeSpace)
            .execution_options(populate_existing=True)
        )
        if not space or space.status != "active":
            return False
        current_db.scalar(
            select(SpaceMember)
            .where(
                SpaceMember.space_id == session.space_id,
                SpaceMember.user_id == user.id,
            )
            .with_for_update(of=SpaceMember)
            .execution_options(populate_existing=True)
        )
        if not PermissionService.has_space_member_role(
            current_db, user, session.space_id, minimum_role="contributor"
        ):
            return False

        document = (
            current_db.scalar(
                select(Document)
                .where(Document.id == session.document_id)
                .with_for_update(of=Document)
                .execution_options(populate_existing=True)
            )
            if session.document_id
            else None
        )
        if (
            not document
            or document.status != "active"
            or document.space_id != session.space_id
        ):
            return False
        job = (
            current_db.scalar(
                select(DocumentJob)
                .where(DocumentJob.id == session.job_id)
                .with_for_update(of=DocumentJob)
                .execution_options(populate_existing=True)
            )
            if session.job_id
            else None
        )
        if job and (
            job.document_id != document.id or job.status in {"failed", "cancelled"}
        ):
            return False
        version = (
            current_db.scalar(
                select(DocumentVersion)
                .where(DocumentVersion.id == session.version_id)
                .with_for_update(of=DocumentVersion)
                .execution_options(populate_existing=True)
            )
            if session.version_id
            else None
        )
        if version and (
            version.document_id != document.id or version.processing_stage == "failed"
        ):
            return False
        return True

    if db is not None:
        return check(db)
    with transaction() as owned_db:
        return check(owned_db)


def _claim_next(worker_id: str) -> tuple[str, str] | None:
    stamp = now()
    stale = or_(
        DocumentUploadSession.lease_expires_at.is_(None),
        DocumentUploadSession.lease_expires_at <= stamp,
    )
    with transaction() as db:
        query = (
            select(DocumentUploadSession)
            .where(
                or_(
                    and_(
                        DocumentUploadSession.status == "queued",
                        DocumentUploadSession.expires_at > stamp,
                        or_(
                            DocumentUploadSession.next_attempt_at.is_(None),
                            DocumentUploadSession.next_attempt_at <= stamp,
                        ),
                    ),
                    and_(
                        DocumentUploadSession.status.in_(("verifying", "processing")),
                        stale,
                    ),
                )
            )
            .order_by(DocumentUploadSession.updated_at, DocumentUploadSession.id)
            .limit(1)
        )
        candidate = db.scalar(query)
        if not candidate:
            return None
        token = f"{worker_id}:{new_token(12)}"
        expected_status = candidate.status
        values: dict[str, Any] = {
            "lease_owner": token,
            "lease_expires_at": stamp + timedelta(seconds=_lease_seconds()),
            "attempts": DocumentUploadSession.attempts + 1,
            "updated_at": stamp,
        }
        if expected_status == "queued":
            values["status"] = "verifying"
        where_clauses = [
            DocumentUploadSession.id == candidate.id,
            DocumentUploadSession.status == expected_status,
        ]
        if expected_status != "queued":
            where_clauses.append(stale)
        else:
            where_clauses.append(DocumentUploadSession.expires_at > stamp)
        result = db.execute(
            update(DocumentUploadSession)
            .execution_options(synchronize_session="fetch")
            .where(*where_clauses)
            .values(**values)
        )
        if int(getattr(result, "rowcount", 0)) != 1:
            return None
        return candidate.id, token


def _mark_failed(session_id: str, owner: str, code: str) -> None:
    message = _safe_error(code)
    with transaction() as db:
        session = db.get(DocumentUploadSession, session_id)
        if not session or session.lease_owner != owner:
            return
        session.status = "failed"
        session.error = message
        session.lease_owner = None
        session.lease_expires_at = None
        session.updated_at = now()
        if session.version_id:
            version = db.get(DocumentVersion, session.version_id)
            if version and version.processing_stage in documents.PROCESSING_STAGES:
                documents.update_version_stage(
                    db,
                    version,
                    "failed",
                    failure_code=code,
                    failure_message=message,
                )
            if session.job_id:
                job = db.get(DocumentJob, session.job_id)
                if job and job.status not in documents.TERMINAL_JOB_STATUSES:
                    job.status = "failed"
                    job.updated_at = now()


def _requeue_storage_retry(session_id: str, owner: str) -> None:
    with transaction() as db:
        session = db.get(DocumentUploadSession, session_id)
        if not session or session.lease_owner != owner:
            return
        session.status = "queued"
        session.error = _safe_error("storage")
        session.lease_owner = None
        session.lease_expires_at = None
        session.next_attempt_at = now()
        session.updated_at = now()


def _worker_permission(db: Session, session: DocumentUploadSession) -> None:
    _space_access(db, session.owner_user_id, session.space_id)


def _prepare_document_checkpoint(
    session_id: str,
    owner: str,
    *,
    canonical_bucket: str,
    canonical_key: str,
) -> tuple[str, str, str]:
    with transaction() as db:
        session = db.get(DocumentUploadSession, session_id)
        if not session or session.lease_owner != owner:
            raise DocumentUploadError(_safe_error("lease"), 409, "lease")
        _worker_permission(db, session)
        if session.version_id and session.document_id and session.job_id:
            return session.document_id, session.version_id, session.job_id
        document, version = documents.create_document(
            db,
            space_id=session.space_id,
            category_id=session.category_id,
            name=session.name,
            created_by_user_id=session.owner_user_id,
            file_path=(
                minio_storage.build_minio_uri(canonical_bucket, canonical_key)
                if canonical_key
                else None
            ),
            source_relative_path=session.source_relative_path,
        )
        job = documents.create_job(
            db,
            document=document,
            version=version,
            created_by_user_id=session.owner_user_id,
        )
        session.document_id = document.id
        session.version_id = version.id
        session.job_id = job.id
        session.canonical_bucket = canonical_bucket
        session.canonical_object_key = canonical_key or None
        session.status = "verifying"
        session.updated_at = now()
        return document.id, version.id, job.id


def _mark_processing(session_id: str, owner: str) -> None:
    with transaction() as db:
        session = db.get(DocumentUploadSession, session_id)
        if not session or session.lease_owner != owner:
            raise DocumentUploadError(_safe_error("lease"), 409)
        _worker_permission(db, session)
        session.status = "processing"
        session.error = None
        session.updated_at = now()


def _process_claim_inner(session_id: str, owner: str) -> None:
    with transaction() as db:
        session = db.get(DocumentUploadSession, session_id)
        if not session or session.lease_owner != owner:
            raise DocumentUploadError(_safe_error("lease"), 409, "lease")
        _worker_permission(db, session)
        session_snapshot = {
            "id": session.id,
            "filename": session.filename,
            "size_bytes": session.size_bytes,
            "sha256": session.sha256,
            "source_session_id": session.source_session_id,
            "staging_bucket": session.staging_bucket,
            "staging_object_key": session.staging_object_key,
            "multipart_upload_id": session.multipart_upload_id,
            "canonical_bucket": session.canonical_bucket,
            "canonical_object_key": session.canonical_object_key,
            "version_id": session.version_id,
            "document_id": session.document_id,
            "job_id": session.job_id,
            "space_id": session.space_id,
            "owner_user_id": session.owner_user_id,
        }

    with tempfile.TemporaryDirectory(prefix="document-upload-") as temporary:
        filename = str(session_snapshot["filename"])
        source_path = Path(temporary) / f"source{Path(filename).suffix.lower()}"
        canonical_bucket = str(session_snapshot["canonical_bucket"] or "")
        canonical_key = str(session_snapshot["canonical_object_key"] or "")
        if session_snapshot["source_session_id"]:
            if not canonical_bucket or not canonical_key:
                raise DocumentUploadError(
                    _safe_error("missing_object"), 422, "missing_object"
                )
            minio_storage.download_to_path(
                object_key=canonical_key,
                bucket=canonical_bucket,
                path=source_path,
            )
        else:
            staging_bucket = str(session_snapshot["staging_bucket"] or "")
            staging_key = str(session_snapshot["staging_object_key"] or "")
            upload_id = str(session_snapshot["multipart_upload_id"] or "")
            if not staging_bucket or not staging_key.startswith("document-uploads/"):
                raise DocumentUploadError(
                    _safe_error("missing_object"), 422, "missing_object"
                )
            use_canonical = False
            try:
                stat = minio_storage.stat_object(
                    object_key=staging_key, bucket=staging_bucket
                )
                if int(getattr(stat, "size", -1)) != int(
                    session_snapshot["size_bytes"]
                ):
                    raise DocumentUploadError(_safe_error("size"), 422, "size")
            except DocumentUploadError:
                raise
            except Exception:
                if canonical_bucket and canonical_key:
                    try:
                        stat = minio_storage.stat_object(
                            object_key=canonical_key, bucket=canonical_bucket
                        )
                        if int(getattr(stat, "size", -1)) == int(
                            session_snapshot["size_bytes"]
                        ):
                            minio_storage.download_to_path(
                                object_key=canonical_key,
                                bucket=canonical_bucket,
                                path=source_path,
                            )
                            use_canonical = True
                    except Exception:
                        use_canonical = False
                if not use_canonical:
                    if not upload_id:
                        raise DocumentUploadError(
                            _safe_error("missing_object"), 422, "missing_object"
                        )
                    parts = minio_storage.list_multipart_parts(
                        object_key=staging_key,
                        upload_id=upload_id,
                        bucket=staging_bucket,
                    )
                    with transaction() as db:
                        current = db.get(DocumentUploadSession, session_id)
                        if not current:
                            raise DocumentUploadError("上传会话不存在", 404)
                        _validate_parts(current, parts)
                    minio_storage.complete_multipart_upload(
                        object_key=staging_key,
                        upload_id=upload_id,
                        parts=parts,
                        bucket=staging_bucket,
                    )
            if not use_canonical:
                minio_storage.download_to_path(
                    object_key=staging_key,
                    bucket=staging_bucket,
                    path=source_path,
                )

        digest = _hash_file(source_path, int(session_snapshot["size_bytes"]))
        if digest != session_snapshot["sha256"]:
            raise DocumentUploadError(_safe_error("checksum"), 422, "checksum")
        _validate_archive(source_path, filename)
        from app.services.image_documents import (
            IMAGE_EXTENSIONS, ImageDocumentError, validate_image_document,
        )
        if Path(filename).suffix.lower() in IMAGE_EXTENSIONS:
            try:
                validate_image_document(source_path, filename)
            except ImageDocumentError as exc:
                raise DocumentUploadError(
                    "图片安全检查失败，请检查文件内容、页数和尺寸后重新上传。",
                    422, "archive",
                ) from exc

        if not session_snapshot["source_session_id"]:
            cfg = minio_storage.load_minio_settings()
            if cfg is None:
                raise DocumentUploadError(_safe_error("disabled"), 503, "disabled")
            canonical_checkpoint_pending = False
            if not canonical_bucket or not canonical_key:
                document_id, version_id, job_id = _prepare_document_checkpoint(
                    session_id,
                    owner,
                    canonical_bucket=cfg.bucket,
                    canonical_key="",
                )
                canonical_bucket = cfg.bucket
                canonical_key = minio_storage.object_key_for_version(
                    space_id=str(session_snapshot["space_id"]),
                    document_id=document_id,
                    version_id=version_id,
                    filename=filename,
                )
                canonical_checkpoint_pending = True
            else:
                document_id = str(session_snapshot["document_id"])
                version_id = str(session_snapshot["version_id"])
                job_id = str(session_snapshot["job_id"])
            try:
                stat = minio_storage.stat_object(
                    object_key=canonical_key, bucket=canonical_bucket
                )
                if int(getattr(stat, "size", -1)) != int(
                    session_snapshot["size_bytes"]
                ):
                    minio_storage.delete_object(
                        object_key=canonical_key, bucket=canonical_bucket
                    )
                    raise FileNotFoundError("canonical object size mismatch")
            except Exception:
                staging_bucket = str(session_snapshot["staging_bucket"] or "")
                staging_key = str(session_snapshot["staging_object_key"] or "")
                try:
                    minio_storage.copy_object(
                        source_bucket=staging_bucket,
                        source_key=staging_key,
                        target_bucket=canonical_bucket,
                        target_key=canonical_key,
                    )
                    stat = minio_storage.stat_object(
                        object_key=canonical_key, bucket=canonical_bucket
                    )
                    if int(getattr(stat, "size", -1)) != int(
                        session_snapshot["size_bytes"]
                    ):
                        raise DocumentUploadError(
                            _safe_error("storage"), 502, "storage"
                        )
                except Exception as exc:
                    raise DocumentUploadError(
                        _safe_error("storage"), 503, "storage_retryable"
                    ) from exc
            if canonical_checkpoint_pending:
                try:
                    with transaction() as db:
                        current = db.scalar(
                            select(DocumentUploadSession)
                            .where(DocumentUploadSession.id == session_id)
                            .with_for_update(of=DocumentUploadSession)
                            .execution_options(populate_existing=True)
                        )
                        if not current or current.lease_owner != owner:
                            raise DocumentUploadError(
                                _safe_error("lease"), 409, "lease"
                            )
                        _worker_permission(db, current)
                        current.canonical_bucket = canonical_bucket
                        current.canonical_object_key = canonical_key
                        version = db.get(DocumentVersion, current.version_id)
                        if not version:
                            raise DocumentUploadError(
                                _safe_error("storage"), 502, "storage"
                            )
                        version.file_path = minio_storage.build_minio_uri(
                            canonical_bucket, canonical_key
                        )
                        version.updated_at = now()
                except DocumentUploadError:
                    raise
                except Exception as exc:
                    raise DocumentUploadError(
                        _safe_error("storage"), 503, "storage_retryable"
                    ) from exc
        else:
            document_id = str(session_snapshot["document_id"])
            version_id = str(session_snapshot["version_id"])
            job_id = str(session_snapshot["job_id"])

        _mark_processing(session_id, owner)
        terminal = False
        with transaction() as db:
            current = db.get(DocumentUploadSession, session_id)
            current_version = (
                db.get(DocumentVersion, current.version_id)
                if current and current.version_id
                else None
            )
            current_job = (
                db.get(DocumentJob, current.job_id)
                if current and current.job_id
                else None
            )
            if current and current.lease_owner == owner:
                if (
                    current_version
                    and current_version.processing_stage == "ready"
                    and current_job
                    and current_job.status in {"completed", "ready"}
                ):
                    current.status = "completed"
                    current.completed_at = now()
                    current.error = None
                    terminal = True
                elif current_version and current_version.processing_stage == "failed":
                    current.status = "failed"
                    current.error = _safe_error("process")
                    terminal = True
                if terminal:
                    current.lease_owner = None
                    current.lease_expires_at = None
                    current.updated_at = now()
        if terminal:
            return

        def lease_check() -> bool:
            return _lease_is_owned(session_id, owner)

        def publication_check(current_db: Session) -> bool:
            return _lease_is_owned(session_id, owner, db=current_db)

        from app.api.documents import process_stored_document

        process_stored_document(
            document_id=document_id,
            version_id=version_id,
            job_id=job_id,
            user_id=str(session_snapshot["owner_user_id"]),
            source_path=source_path,
            lease_check=lease_check,
            publication_check=publication_check,
        )

        with transaction() as db:
            session = db.get(DocumentUploadSession, session_id)
            if not session or session.lease_owner != owner:
                return
            _worker_permission(db, session)
            version = (
                db.get(DocumentVersion, session.version_id)
                if session.version_id
                else None
            )
            job = db.get(DocumentJob, session.job_id) if session.job_id else None
            if (
                version
                and version.processing_stage == "ready"
                and job
                and job.status in {"completed", "ready"}
            ):
                session.status = "completed"
                session.completed_at = now()
                session.error = None
            else:
                session.status = "failed"
                session.error = _safe_error("process")
            session.lease_owner = None
            session.lease_expires_at = None
            session.updated_at = now()


def _process_claim(session_id: str, owner: str) -> None:
    with _LeaseHeartbeat(session_id, owner, _lease_seconds()):
        _process_claim_inner(session_id, owner)


class DocumentUploadDispatcher:
    """Claim and process one durable upload without request-scoped bytes."""

    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"

    def run_once(self) -> bool:
        if (
            not settings.document_uploads_enabled
            or not minio_storage.is_minio_enabled()
        ):
            return False
        claimed = _claim_next(self.worker_id)
        if claimed is None:
            return False
        session_id, owner = claimed
        try:
            _process_claim(session_id, owner)
        except DocumentUploadError as exc:
            if exc.code == "storage_retryable":
                _requeue_storage_retry(session_id, owner)
                logger.info(
                    "document upload %s storage unavailable; queued for retry",
                    session_id,
                )
            else:
                _mark_failed(session_id, owner, exc.code)
                logger.info("document upload %s failed: %s", session_id, exc.detail)
        except Exception:
            logger.exception("document upload worker failed for %s", session_id)
            _mark_failed(session_id, owner, "storage")
        return True


def _cleanup_orphan_multipart_uploads(cutoff: datetime) -> tuple[int, int]:
    cfg = minio_storage.load_minio_settings()
    if cfg is None:
        return 0, 0
    try:
        uploads = minio_storage.list_multipart_uploads(
            bucket=cfg.temp_bucket,
            prefix="document-uploads/",
            max_uploads=int(settings.document_upload_cleanup_batch_size) * 10,
        )
    except Exception:
        logger.info("orphan multipart listing failed", exc_info=True)
        return 0, 1
    with transaction() as db:
        known_ids = {
            value
            for value in db.scalars(
                select(DocumentUploadSession.multipart_upload_id).where(
                    DocumentUploadSession.staging_bucket == cfg.temp_bucket,
                    DocumentUploadSession.staging_object_key.like("document-uploads/%"),
                    DocumentUploadSession.multipart_upload_id.is_not(None),
                )
            ).all()
            if value
        }
    grace = timedelta(seconds=max(600, min(_ttl_seconds(), 3600)))
    removed = 0
    failed = 0
    for row in uploads:
        upload_id = str(row.get("upload_id") or "")
        object_key = str(row.get("object_key") or "")
        initiated = row.get("initiated")
        initiated_at = _utc(initiated) if isinstance(initiated, datetime) else None
        if (
            not upload_id
            or not object_key.startswith("document-uploads/")
            or upload_id in known_ids
            or initiated_at is None
            or initiated_at > cutoff - grace
        ):
            continue
        try:
            minio_storage.abort_multipart_upload(
                object_key=object_key,
                upload_id=upload_id,
                bucket=cfg.temp_bucket,
            )
            removed += 1
        except Exception:
            failed += 1
            logger.info("orphan multipart abort failed", exc_info=True)
    return removed, failed


def _cleanup_one(session_id: str) -> str:
    object_info: tuple[str, str, str | None] | None = None
    with transaction() as db:
        session = db.get(DocumentUploadSession, session_id)
        if not session:
            return "skipped"
        if session.status in ACTIVE_STATUSES:
            if (
                _utc(session.expires_at) or datetime.min.replace(tzinfo=timezone.utc)
            ) > now():
                return "skipped"
            if (
                session.status in {"verifying", "processing"}
                and (
                    _utc(session.lease_expires_at)
                    or datetime.min.replace(tzinfo=timezone.utc)
                )
                > now()
            ):
                return "skipped"
            session.status = "expired"
            session.error = _safe_error("expired")
            session.lease_owner = None
            session.lease_expires_at = None
            session.updated_at = now()
        if session.status not in {"completed", "expired", "failed", "aborted"}:
            return "skipped"
        if session.source_session_id:
            return "cleaned"
        key = (session.staging_object_key or "").strip()
        bucket = (session.staging_bucket or "").strip()
        upload_id = (session.multipart_upload_id or "").strip() or None
        if key.startswith("document-uploads/") and bucket:
            object_info = (bucket, key, upload_id)

    if object_info:
        bucket, key, upload_id = object_info
        abort_ok = True
        if upload_id:
            try:
                minio_storage.abort_multipart_upload(
                    object_key=key, upload_id=upload_id, bucket=bucket
                )
            except Exception as exc:
                if getattr(exc, "code", None) == "NoSuchUpload":
                    logger.info("expired multipart upload already absent")
                else:
                    abort_ok = False
                    logger.info("expired multipart abort failed", exc_info=True)
        deleted = _delete_staging_object(bucket, key)
        if deleted and abort_ok:
            with transaction() as db:
                current = db.get(DocumentUploadSession, session_id)
                if (
                    current
                    and current.status in {"completed", "expired", "failed", "aborted"}
                    and current.staging_object_key == key
                ):
                    current.staging_object_key = ""
                    current.multipart_upload_id = None
                    current.updated_at = now()
    return "cleaned"


def cleanup_expired_uploads(
    *, now_at: datetime | None = None, limit: int | None = None
) -> dict[str, int]:
    if not settings.document_uploads_enabled or not minio_storage.is_minio_enabled():
        return {
            "scanned": 0,
            "cleaned": 0,
            "skipped": 0,
            "failed": 0,
            "orphan_uploads": 0,
            "orphan_failed": 0,
        }
    cutoff = now_at or now()
    batch = max(1, int(limit or settings.document_upload_cleanup_batch_size))
    with transaction() as db:
        ids = list(
            db.scalars(
                select(DocumentUploadSession.id)
                .where(
                    or_(
                        and_(
                            DocumentUploadSession.status.in_(
                                ("uploading", "queued", "verifying", "processing")
                            ),
                            DocumentUploadSession.expires_at <= cutoff,
                        ),
                        and_(
                            DocumentUploadSession.status.in_(
                                ("verifying", "processing")
                            ),
                            or_(
                                DocumentUploadSession.lease_expires_at.is_(None),
                                DocumentUploadSession.lease_expires_at <= cutoff,
                            ),
                        ),
                        and_(
                            DocumentUploadSession.status.in_(
                                ("completed", "failed", "aborted", "expired")
                            ),
                            DocumentUploadSession.staging_object_key != "",
                        ),
                    )
                )
                .order_by(DocumentUploadSession.updated_at)
                .limit(batch)
            ).all()
        )
    stats = {
        "scanned": len(ids),
        "cleaned": 0,
        "skipped": 0,
        "failed": 0,
        "orphan_uploads": 0,
        "orphan_failed": 0,
    }
    for session_id in ids:
        try:
            result = _cleanup_one(session_id)
            stats[result] += 1
        except Exception:
            stats["failed"] += 1
            logger.exception("document upload cleanup failed for %s", session_id)
    orphan_removed, orphan_failed = _cleanup_orphan_multipart_uploads(cutoff)
    stats["orphan_uploads"] = orphan_removed
    stats["orphan_failed"] = orphan_failed
    return stats


def enqueue_stored_retry(
    db: Session,
    *,
    document: Document,
    version: DocumentVersion,
    job: DocumentJob,
    source_version_id: str,
    user_id: str,
) -> bool:
    """Queue a retry against an existing immutable canonical upload object.

    Main creates the retry version/job first.  This function only adds a durable
    upload-session queue row; it never downloads or re-uploads source bytes.
    """
    existing = db.scalar(
        select(DocumentUploadSession).where(
            DocumentUploadSession.version_id == version.id
        )
    )
    if existing:
        return True
    if (
        not document.space_id
        or version.document_id != document.id
        or job.document_id != document.id
        or job.version_id != version.id
    ):
        return False
    _space_access(db, user_id, document.space_id)
    source = db.scalar(
        select(DocumentUploadSession).where(
            DocumentUploadSession.version_id == source_version_id,
            DocumentUploadSession.document_id == document.id,
            DocumentUploadSession.space_id == document.space_id,
        )
    )
    if not source or not source.canonical_object_key:
        return False
    if source.space_id != document.space_id:
        return False
    canonical_bucket = (source.canonical_bucket or "").strip()
    canonical_key = (source.canonical_object_key or "").strip()
    if not canonical_bucket or not canonical_key:
        return False
    stamp = now()
    retry = DocumentUploadSession(
        id=f"upl_{new_token(12)}",
        owner_user_id=user_id,
        space_id=document.space_id,
        version_id=version.id,
        document_id=document.id,
        job_id=job.id,
        source_session_id=source.id,
        client_upload_id=f"retry:{source.id}:{version.id}",
        filename=source.filename,
        size_bytes=source.size_bytes,
        sha256=source.sha256,
        name=source.name,
        category_id=source.category_id,
        source_relative_path=source.source_relative_path,
        part_size=source.part_size,
        part_count=source.part_count,
        staging_bucket=canonical_bucket,
        staging_object_key="",
        multipart_upload_id=None,
        canonical_bucket=canonical_bucket,
        canonical_object_key=canonical_key,
        status="queued",
        next_attempt_at=stamp,
        expires_at=stamp + timedelta(seconds=_ttl_seconds()),
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(retry)
    db.flush()
    return True


__all__ = [
    "DocumentUploadDispatcher",
    "DocumentUploadError",
    "cleanup_expired_uploads",
    "create_upload_session",
    "complete_session",
    "abort_session",
    "enqueue_stored_retry",
    "get_session",
    "sign_part",
    "upload_policy",
]
