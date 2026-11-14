from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from io import BytesIO
from tempfile import TemporaryDirectory
from pathlib import Path, PurePosixPath
from typing import Annotated, cast
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import transaction
from app.core.user_messages import looks_chinese, public_error_message
from app.models.access import DocumentCategory, KnowledgeSpace, SpaceMember
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession
from app.models.documents import Document, DocumentJob, DocumentVersion
from app.models.document_uploads import DocumentUploadSession
from app.models.document_pages import DocumentPage
from app.repositories import audit, documents
from app.repositories import chat as chat_repo
from app.schemas.documents import (
    ChatSaveableResultsResponse,
    DocumentCategoryUpdateRequest,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentPreviewResponse,
    DocumentRenameRequest,
    DocumentResponse,
    DocumentSearchItem,
    DocumentSearchResponse,
    DocumentUploadResponse,
    DocumentVersionResponse,
    MoveDocumentRequest,
    PersistTemporaryDocumentRequest,
    SaveChatMessageAsDocumentRequest,
    SaveChatMessageAsDocumentResponse,
    TaskSnapshotResponse,
)
from app.services import chat_saveable_results, image_documents, minio_storage
from app.services.authorization import PermissionService
from app.services.chat_docx_delivery import _document_title
from app.services.markdown_docx import markdown_to_docx_bytes
from app.services.document_category_classifier import (
    classify_by_rules,
    classify_with_llm,
    load_space_categories,
    resolve_llm_endpoint,
)
from app.services.doc_preview import (
    DocumentConversionError,
    LibreOfficeUnavailable,
    office_preview_cached,
    render_office_preview_pdf,
)
from app.services.document_processor import (
    decode_text_bytes,
    derive_category,
    extract_docx_text,
    extract_doc_text,
    extract_spreadsheet_text,
    extracted_sidecar_path,
    normalize_source_relative_path,
    stable_knowledge_id,
    validate_file,
    write_extracted_sidecar,
)
from app.services.knowledge_pipeline import process_document as run_knowledge_pipeline
from app.services.model_routing import resolve_vision_endpoint
from app.services.pdf_ocr import PdfOcrError, extract_pdf_pages, render_pdf_page

logger = logging.getLogger(__name__)

router = APIRouter(tags=["documents"])

_SAFE_FAILURE_MESSAGES = {
    "parse_failed": "文档解析失败，请重试。",
    "scan_failed": "文档安全检查失败，请重试。",
    "quality_failed": "文档质量检查失败，请重试。",
    "index_failed": "文档索引失败，请重试。",
    "ingest_failed": "文档处理失败，请重试。",
    "pipeline_failed": "文档知识入库失败，请稍后重试。",
    "extract_failed": "无法从文件中提取文本，请检查文件格式后重试。",
    "ocr_engine_unavailable": "扫描 PDF 需要 OCR 引擎，当前环境暂不可用。",
    "ocr_disabled": "扫描 PDF 的 OCR 已关闭，无法建立可检索文本。",
    "ocr_page_limit_exceeded": "PDF 页数超过 OCR 安全上限。",
    "ocr_pixel_limit_exceeded": "PDF 页面尺寸超过 OCR 安全上限。",
    "ocr_page_timeout": "PDF 页面 OCR 超时，请稍后重试。",
    "ocr_no_text": "PDF 页面未识别到可用文本。",
    "pdf_render_failed": "PDF 页面渲染失败，请重试。",
    "vision_endpoint_unavailable": "视觉辅助端点不可用，PDF 页面保留 OCR 结果或警告。",
    "vision_call_failed": "视觉辅助暂时不可用，PDF 页面保留 OCR 结果或警告。",
    "vision_budget_exceeded": "PDF 视觉辅助已达到页数、Token 或费用上限。",
    "vision_output_invalid": "视觉辅助结果无法校验，PDF 页面保留 OCR 结果或警告。",
    "image_invalid": "图片内容损坏或与文件格式不符，请重新上传。",
    "image_format_unsupported": "该图片编码暂不支持，请另存为 PNG 或 JPG 后上传。",
    "image_too_large": "图片大小或像素超过安全上限，请缩小后重新上传。",
    "image_pixel_limit_exceeded": "图片像素超过安全上限，请缩小或拆分后重新上传。",
    "image_page_limit_exceeded": "图片页数或帧数超过安全上限，请拆分后重新上传。",
    "image_vision_no_content": "原图已保存，视觉模型未提取到可检索内容，可查看原图或补充清晰版本。",
    "image_vision_endpoint_unavailable": "原图已保存，当前没有符合资料权限要求的可用视觉模型，请联系管理员后重新解析。",
    "image_vision_failed": "原图已保存，视觉解析未完成，请稍后重新解析。",
    "image_vision_call_failed": "原图已保存，视觉模型暂时不可用，请稍后重新解析。",
    "image_vision_timeout": "原图已保存，视觉模型识别超时，请稍后重新解析。",
    "image_vision_budget_exceeded": "原图已保存，图片识别已达到调用、Token 或费用上限，请检查预算或拆分资料。",
    "image_vision_output_invalid": "原图已保存，模型未返回完整有效的识别内容，请重新解析或补充清晰版本。",
    "image_vision_unclear": "部分文字或图形无法清晰辨认，请查看原图中的具体疑点。",
    "image_vision_partial": "部分图片内容未完成识别，请核对原图；仅已识别内容可供检索。",
    "upload_failed": "文件上传失败，请重试。",
    "file_missing": "原文件已丢失，无法重试，请重新上传。",
    "missing_task_id": "未能获得处理任务编号，请重试。",
    "process_timeout": "文档处理超时或中断，请重试。",
    "process_failed": "文档处理失败，请重试。",
    "cancelled": "任务已取消。",
}
_DEFAULT_FAILURE_MESSAGE = "文档处理失败，请重试或联系管理员。"


async def _read_upload_bytes(file: UploadFile) -> bytes:
    """Read at most one byte beyond the limit so oversized uploads fail early."""
    max_bytes = settings.max_upload_mb * 1024 * 1024
    return await file.read(max_bytes + 1)


def public_failure_message(code: str | None, stored: str | None) -> str:
    """Map stored processor errors to page-safe Chinese guidance."""
    fallback = _SAFE_FAILURE_MESSAGES.get(code or "", _DEFAULT_FAILURE_MESSAGE)
    candidate = public_error_message(stored, fallback=fallback)
    if candidate != fallback and not looks_chinese(candidate):
        return fallback
    return candidate


_TEXT_PREVIEW_EXTENSIONS = frozenset({".md", ".markdown", ".txt", ".csv"})
_SPREADSHEET_PREVIEW_EXTENSIONS = frozenset({".xlsx", ".xls"})
_MAX_PREVIEW_CHARS = 200_000


def _version_file_extension(version: DocumentVersion) -> str | None:
    file_path = (version.file_path or "").strip()
    if not file_path:
        return None
    if minio_storage.is_minio_uri(file_path):
        try:
            _, object_key = minio_storage.parse_minio_uri(file_path)
            suffix = Path(object_key).suffix.lower()
        except ValueError:
            return None
    else:
        suffix = Path(file_path).suffix.lower()
    return suffix or None


def version_response(
    db, version: DocumentVersion | None
) -> DocumentVersionResponse | None:
    if not version:
        return None
    failure_message = None
    if version.processing_stage == "failed":
        failure_message = public_failure_message(
            version.failure_code, version.failure_message
        )
    return DocumentVersionResponse(
        id=version.id,
        version_number=version.version_number,
        processing_stage=version.processing_stage,
        queryable=version.processing_stage == "ready" and version.queryable,
        retryable=documents.is_retryable(db, version),
        failure_code=version.failure_code
        if version.processing_stage == "failed"
        else None,
        failure_message=failure_message,
        chunk_count=version.chunk_count if version.processing_stage == "ready" else 0,
        summary=(version.summary or None)
        if version.processing_stage == "ready"
        else None,
        file_extension=_version_file_extension(version),
        source_relative_path=version.source_relative_path or None,
        extraction_method=version.extraction_method,
        page_count=version.page_count,
        ocr_page_count=version.ocr_page_count,
        vision_page_count=getattr(version, "vision_page_count", 0),
        extraction_warning_code=(
            None if image_documents.is_image_version(version)
            and version.extraction_warning_code == "image_vision_uncertain"
            else version.extraction_warning_code
        ),
        is_current_published=version.is_current_published,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


def document_response(
    db,
    document: Document,
    *,
    created_by_display_name: str | None = None,
) -> DocumentResponse:
    if created_by_display_name is None and document.created_by_user_id:
        created_by_display_name = documents.creator_display_names(
            db, [document.created_by_user_id]
        ).get(document.created_by_user_id)
    return DocumentResponse(
        id=document.id,
        space_id=document.space_id,
        category_id=document.category_id,
        name=document.name,
        status=document.status,
        is_temporary=document.is_temporary,
        chat_session_id=document.chat_session_id,
        expires_at=document.expires_at,
        temporary_cleanup_at=document.temporary_cleanup_at,
        created_by_user_id=document.created_by_user_id,
        created_by_display_name=created_by_display_name,
        latest_version=version_response(db, documents.latest_version(db, document.id)),
        current_published_version=version_response(
            db, documents.current_published_version(db, document.id)
        ),
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def document_detail_response(db, document: Document) -> DocumentDetailResponse:
    response = document_response(db, document)
    versions = []
    for item in documents.versions_for_document(db, document.id):
        item_response = version_response(db, item)
        if item_response is not None:
            versions.append(item_response)
    return DocumentDetailResponse(
        **response.model_dump(),
        versions=versions,
    )


def processing_task(
    db, job: DocumentJob, user: User | None = None
) -> TaskSnapshotResponse:
    return TaskSnapshotResponse(
        **documents.task_snapshot(
            db,
            job,
            user_id=user.id if user else None,
            is_admin=user.role == "admin" if user else False,
        )
    )


def _storage_space_id(document: Document) -> str:
    """Return a stable namespace for storage and local vector collections."""
    return document.space_id or f"temporary_{document.created_by_user_id}"


def _require_document_access(
    db: Session, user: User, document: Document, *, minimum_role: str
) -> None:
    """Authorize normal documents by space and private temporary documents by owner."""
    if document.space_id:
        if minimum_role == "contributor":
            PermissionService.require_space_member_access(
                db, user, document.space_id, minimum_role=minimum_role
            )
        else:
            PermissionService.require_space_access(
                db, user, document.space_id, minimum_role=minimum_role,
                allow_archived=minimum_role == "reader",
            )
        return
    if document.is_temporary and document.created_by_user_id == user.id:
        return
    raise HTTPException(status_code=404, detail="文档不存在")


def _document_storage_refs(document: Document) -> list[str]:
    """Return canonical and dual-written storage references for all versions."""
    refs: list[str] = []
    minio_cfg = minio_storage.load_minio_settings()
    for version in document.versions:
        file_path = (version.file_path or "").strip()
        if not file_path:
            continue
        refs.append(file_path)
        if minio_storage.is_minio_uri(file_path) or not minio_cfg:
            continue
        suffix = Path(file_path).suffix
        if not suffix:
            continue
        object_key = minio_storage.object_key_for_version(
            space_id=_storage_space_id(document),
            document_id=document.id,
            version_id=version.id,
            filename=f"document{suffix}",
        )
        refs.append(minio_storage.build_minio_uri(minio_cfg.bucket, object_key))
    return refs


def _require_active_document(document: Document) -> None:
    if document.status != "active":
        raise HTTPException(status_code=404, detail="文档不存在")


def _purge_stored_files(paths: list[str]) -> list[str]:
    """Best-effort cleanup; return references that still need another attempt."""
    seen: set[str] = set()
    failures: list[str] = []
    for file_path in paths:
        if not file_path or file_path in seen:
            continue
        seen.add(file_path)
        try:
            if minio_storage.is_minio_uri(file_path):
                if not _delete_minio_object(file_path):
                    failures.append(file_path)
            else:
                path = Path(file_path)
                if path.is_file():
                    path.unlink()
            sidecar = extracted_sidecar_path(file_path)
            if sidecar and sidecar.is_file():
                sidecar.unlink()
        except Exception:
            failures.append(file_path)
            logger.exception("failed to purge stored file %s", file_path)
    return failures


def _delete_minio_object(file_path: str) -> bool:
    if not minio_storage.is_minio_uri(file_path):
        return True
    try:
        bucket, key = minio_storage.parse_minio_uri(file_path)
        cfg = minio_storage.load_minio_settings()
        if not cfg:
            return False
        client = minio_storage._client(cfg)  # noqa: SLF001
        client.remove_object(bucket, key)
        return True
    except Exception:
        logger.exception("failed to delete minio object %s", file_path)
        return False


def _cleanup_local_document_vectors(document: Document) -> None:
    """Delete local vector-store rows for indexed versions; operation is idempotent."""
    local_versions = [
        version
        for version in document.versions
        if (
            version.queryable
            or version.chunk_count > 0
            or version.processing_stage in {"chunking", "indexing", "ready"}
        )
    ]
    if not local_versions:
        return
    from app.services.pgvector_store import PgVectorStore

    store = PgVectorStore()
    category = derive_category(_storage_space_id(document))
    for version in local_versions:
        store.delete_by_file(
            category,
            str(stable_knowledge_id(version.id)),
            strict=True,
        )


def _purge_temporary_document(document_id: str) -> str:
    """Soft-delete one temporary attachment and purge vectors/files.

    Returns ``cleaned`` or ``skipped``. Raises on storage/vector failures so
    callers can retry later while leaving ``temporary_cleanup_at`` empty.
    """
    from sqlalchemy.orm import selectinload

    with transaction() as db:
        document = db.scalar(
            select(Document)
            .options(selectinload(Document.versions))
            .where(
                Document.id == document_id,
                Document.is_temporary.is_(True),
                Document.temporary_cleanup_at.is_(None),
            )
        )
        if not document:
            return "skipped"
        storage_refs = _document_storage_refs(document)
        if document.status != "deleted":
            documents.delete_document(db, document)

    _cleanup_local_document_vectors(document)
    failures = _purge_stored_files(storage_refs)
    if failures:
        raise RuntimeError(f"临时附件存储清理失败：{', '.join(failures[:3])}")

    completed_at = datetime.now(timezone.utc)
    with transaction() as db:
        current = db.get(Document, document_id)
        if (
            not current
            or not current.is_temporary
            or current.temporary_cleanup_at is not None
        ):
            return "skipped"
        current.status = "deleted"
        current.temporary_cleanup_at = completed_at
        current.updated_at = completed_at
    return "cleaned"


def _cleanup_temporary_document_ids(candidate_ids: list[str]) -> dict[str, int]:
    stats = {"scanned": len(candidate_ids), "cleaned": 0, "failed": 0, "skipped": 0}
    for document_id in candidate_ids:
        try:
            result = _purge_temporary_document(document_id)
            stats[result] += 1
        except Exception:
            stats["failed"] += 1
            logger.exception("temporary document cleanup failed for %s", document_id)
    if stats["cleaned"] or stats["failed"]:
        logger.info("temporary document cleanup: %s", stats)
    return stats


def cleanup_expired_temporary_documents(
    *, now_at: datetime | None = None, limit: int = 100
) -> dict[str, int]:
    """Delete expired chat attachments and leave failed rows retryable."""
    cutoff = now_at or datetime.now(timezone.utc)
    with transaction() as db:
        candidate_ids = [
            document.id
            for document in documents.list_expired_temporary_documents(
                db, expires_before=cutoff, limit=limit
            )
        ]
    return _cleanup_temporary_document_ids(candidate_ids)


def cleanup_temporary_documents_for_chat_session(
    chat_session_id: str,
) -> dict[str, int]:
    """Delete temporary attachments bound to a chat session (e.g. on session delete)."""
    session_id = (chat_session_id or "").strip()
    if not session_id:
        return {"scanned": 0, "cleaned": 0, "failed": 0, "skipped": 0}
    with transaction() as db:
        candidate_ids = [
            document.id
            for document in documents.list_temporary_documents_for_session(
                db, chat_session_id=session_id
            )
        ]
    return _cleanup_temporary_document_ids(candidate_ids)


def _load_version_bytes(version: DocumentVersion) -> tuple[bytes, str]:
    """Return (content, suffix) for a stored version from local disk or MinIO."""
    file_path = (version.file_path or "").strip()
    if not file_path:
        raise FileNotFoundError("文件路径为空")
    if minio_storage.is_minio_uri(file_path):
        data = minio_storage.download_bytes(minio_uri=file_path)
        suffix = Path(minio_storage.parse_minio_uri(file_path)[1]).suffix
        return data, suffix
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(file_path)
    return path.read_bytes(), path.suffix


def _select_document_version(
    db: Session, document: Document, version_id: str | None
) -> DocumentVersion | None:
    if version_id:
        version = db.get(DocumentVersion, version_id)
        if not version or version.document_id != document.id:
            raise HTTPException(status_code=404, detail="文档版本不存在")
        return version
    for candidate in (
        documents.latest_version(db, document.id),
        documents.current_published_version(db, document.id),
        *documents.versions_for_document(db, document.id),
    ):
        if candidate and (candidate.file_path or "").strip():
            return candidate
    return None


def _truncate_preview(content: str) -> tuple[str, bool]:
    if len(content) <= _MAX_PREVIEW_CHARS:
        return content, False
    return content[:_MAX_PREVIEW_CHARS], True


def _attachment_response(*, file_path: str, filename: str) -> Response:
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "X-Filename": quote(filename),
    }
    if not minio_storage.is_minio_uri(file_path):
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(file_path)
        return FileResponse(
            path, media_type="application/octet-stream", headers=headers
        )

    response = minio_storage.open_object(minio_uri=file_path)

    def close() -> None:
        response.close()
        response.release_conn()

    def chunks():
        try:
            yield from response.stream(4 * 1024 * 1024)
        finally:
            close()

    if response.headers.get("Content-Length"):
        headers["Content-Length"] = response.headers["Content-Length"]
    return StreamingResponse(
        chunks(),
        media_type="application/octet-stream",
        headers=headers,
        background=BackgroundTask(close),
    )


async def _submit_and_schedule(
    db: Session,
    *,
    document: Document,
    version: DocumentVersion,
    filename: str,
    content: bytes,
    created_by_user_id: str,
    background_tasks: BackgroundTasks,
) -> DocumentJob:
    """Upload to MinIO when enabled, then process via the local pipeline."""
    category = derive_category(_storage_space_id(document))
    minio_uri: str | None = None
    try:
        if minio_storage.is_minio_enabled():
            object_key = minio_storage.object_key_for_version(
                space_id=_storage_space_id(document),
                document_id=document.id,
                version_id=version.id,
                filename=filename,
            )
            minio_uri = await asyncio.to_thread(
                minio_storage.upload_bytes,
                content=content,
                object_key=object_key,
            )
            if version.file_path and not minio_storage.is_minio_uri(version.file_path):
                pass
            else:
                version.file_path = minio_uri
    except Exception as e:
        logger.exception("MinIO upload failed for %s", document.id)
        documents.update_version_stage(
            db,
            version,
            "failed",
            failure_code="upload_failed",
            failure_message=str(e),
        )
        return documents.create_job(
            db,
            document=document,
            version=version,
            created_by_user_id=created_by_user_id,
        )

    # ── Local Pi pipeline ──────────────────────────────────────────────
    job = documents.create_job(
        db,
        document=document,
        version=version,
        created_by_user_id=created_by_user_id,
    )
    documents.update_version_stage(db, version, "scanning")
    background_tasks.add_task(
        _process_local_pipeline,
        content=content,
        filename=filename,
        category=category,
        file_id=str(stable_knowledge_id(version.id)),
        document_id=document.id,
        version_id=version.id,
        job_id=job.id,
        user_id=created_by_user_id,
    )
    return job


@router.get(
    "/knowledge-spaces/{space_id}/documents", response_model=DocumentListResponse
)
def list_space_documents(
    space_id: str,
    category_id: str | None = None,
    status: str | None = Query(
        default=None,
        pattern=r"^(queued|scanning|parsing|quality_check|chunking|indexing|processing|ready|failed)$",
    ),
    q: str | None = Query(default=None, max_length=255),
    cursor: str | None = None,
    offset: int | None = Query(default=None, ge=0),
    limit: int | None = Query(default=None, ge=1, le=100),
    queryable: bool | None = Query(default=None),
    include_counts: bool = Query(default=False),
    user: User = Depends(current_user),
) -> DocumentListResponse:
    with transaction() as db:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="reader", allow_archived=True
        )
        try:
            items, next_cursor = documents.list_documents(
                db,
                space_id=space_id,
                category_id=category_id,
                status=status,
                query_text=q,
                cursor=cursor,
                queryable=queryable,
                offset=offset,
                limit=limit,
            )
        except ValueError:
            raise HTTPException(status_code=422, detail="分页参数无效") from None
        counts = None
        if include_counts:
            all_count, by_category = documents.count_documents_by_category(
                db, space_id=space_id
            )
            counts = {"all": all_count, "by_category": by_category}
        total = None
        if offset is not None or limit is not None:
            if (
                counts is not None
                and not category_id
                and status is None
                and not q
                and queryable is not True
            ):
                total = counts["all"]
            else:
                total = documents.count_documents(
                    db,
                    space_id=space_id,
                    category_id=category_id,
                    status=status,
                    query_text=q,
                    queryable=queryable,
                )
        creator_names = documents.creator_display_names(
            db, [item.created_by_user_id for item in items]
        )
        return DocumentListResponse(
            items=[
                document_response(
                    db,
                    item,
                    created_by_display_name=creator_names.get(item.created_by_user_id),
                )
                for item in items
            ],
            next_cursor=next_cursor,
            total=total,
            counts=counts,
            can_manage_documents=PermissionService.has_space_member_role(
                db, user, space_id, minimum_role="contributor"
            ),
        )


def _saved_chat_document(
    db: Session, *, space_id: str, source_relative_path: str,
) -> Document | None:
    """只复用目标文件夹中仍有效、来源相同的普通资料。"""
    return db.scalar(
        select(Document)
        .where(
            Document.space_id == space_id,
            Document.status == "active",
            Document.is_temporary.is_(False),
            Document.versions.any(
                DocumentVersion.source_relative_path == source_relative_path
            ),
        )
        .order_by(Document.created_at, Document.id)
        .limit(1)
    )


@router.get(
    "/chat/sessions/{session_id}/saveable-results",
    response_model=ChatSaveableResultsResponse,
)
def list_chat_saveable_results(
    session_id: str,
    message_id: str | None = Query(default=None, min_length=1, max_length=128),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(current_user),
) -> dict:
    with transaction() as db:
        return chat_saveable_results.list_results(
            db, session_id, user, message_id=message_id, offset=offset, limit=limit,
        )


@router.post(
    "/chat/messages/{message_id}:save-as-document",
    response_model=SaveChatMessageAsDocumentResponse,
)
async def save_chat_message_as_document(
    message_id: str,
    payload: SaveChatMessageAsDocumentRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
) -> SaveChatMessageAsDocumentResponse:
    with transaction() as db:
        message = db.scalar(
            select(ChatMessage).where(
                ChatMessage.id == message_id,
                ChatMessage.owner_user_id == user.id,
            )
        )
        session = (
            chat_repo.get_owned_session(db, message.session_id, user.id)
            if message is not None
            else None
        )
        if message is None or session is None:
            raise HTTPException(status_code=404, detail="聊天消息不存在")
        if message.role != "assistant":
            raise HTTPException(status_code=422, detail="只能将助手回复存为资料")
        result = (
            chat_saveable_results.require_result(db, message, user, payload.result_id)
            if payload.result_id is not None else None
        )
        reply = str(chat_repo.message_to_dict(message)["content"] or "").strip()
        if result is None and not reply:
            raise HTTPException(status_code=422, detail="消息没有可保存的正文")
        space = db.get(KnowledgeSpace, payload.space_id)
        if space is None or space.status != "active":
            raise HTTPException(status_code=422, detail="目标文件夹不存在或已归档")
        PermissionService.require_space_member_access(
            db, user, space.id, minimum_role="contributor"
        )
        if result is None:
            source_relative_path = f"chat/{session.id}/{message.id}.docx"
            existing = _saved_chat_document(
                db, space_id=space.id, source_relative_path=source_relative_path,
            )
            if existing is not None:
                return SaveChatMessageAsDocumentResponse(
                    document=document_response(db, existing), reused=True
                )
            preceding_request = db.scalar(
                select(ChatMessage.content)
                .where(
                    ChatMessage.session_id == session.id,
                    ChatMessage.owner_user_id == user.id,
                    ChatMessage.role == "user",
                    ChatMessage.created_at <= message.created_at,
                )
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(1)
            )
            title = _document_title(reply, session.title, preceding_request or "")
        else:
            title = Path(result.name).stem

    if result is None:
        raw = await asyncio.to_thread(markdown_to_docx_bytes, reply, title=title)
        filename = f"{message_id}.docx"
    else:
        raw = await asyncio.to_thread(chat_saveable_results.read_result_bytes, result, user)
        source_relative_path = result.source_path(user.id, raw)
        filename = result.name
    # 上传校验要求文件名与来源路径末段一致，原件保持原始格式及字节。
    upload = UploadFile(file=BytesIO(raw), filename=filename)
    try:
        saved = await _upload_document(
            space_id=payload.space_id,
            background_tasks=background_tasks,
            file=upload,
            name=title,
            category_id=None,
            source_relative_path=source_relative_path,
            temporary=False,
            user=user,
            save_message_id=message_id,
            save_result_id=payload.result_id,
            save_result_version=result.version if result is not None else None,
        )
    finally:
        await upload.close()
    return cast(SaveChatMessageAsDocumentResponse, saved)


def _lock_chat_save_context(
    db: Session, *, message: ChatMessage, space_id: str, user: User,
    result_id: str | None,
    result_version: str | None,
) -> None:
    """持锁重读源会话、目标和成员；归档/撤权/删除不能穿过提交窗口。"""
    chat_saveable_results.owned_session(db, message.session_id, user, lock=True)
    target = db.scalar(select(KnowledgeSpace).where(
        KnowledgeSpace.id == space_id,
    ).with_for_update().execution_options(populate_existing=True))
    if target is None or target.status != "active":
        raise HTTPException(status_code=422, detail="目标文件夹不存在或已归档")
    db.scalars(select(SpaceMember).where(
        SpaceMember.space_id == space_id, SpaceMember.user_id == user.id,
    ).with_for_update().execution_options(populate_existing=True)).all()
    PermissionService.require_space_member_access(
        db, user, space_id, minimum_role="contributor",
    )
    if result_id is not None:
        current = chat_saveable_results.require_result(db, message, user, result_id, lock=True)
        if current.version != result_version:
            raise HTTPException(status_code=409, detail="成果已变化，请刷新后重试")


async def _upload_document(
    *,
    space_id: str | None,
    background_tasks: BackgroundTasks,
    file: UploadFile,
    name: str | None,
    category_id: str | None,
    source_relative_path: str | None,
    temporary: bool,
    user: User,
    chat_session_id: str | None = None,
    save_message_id: str | None = None,
    save_result_id: str | None = None,
    save_result_version: str | None = None,
) -> DocumentUploadResponse | SaveChatMessageAsDocumentResponse:
    if not space_id and not temporary:
        raise HTTPException(status_code=422, detail="普通文档必须指定知识空间")
    if temporary and not space_id and not chat_session_id:
        raise HTTPException(status_code=422, detail="临时附件必须绑定聊天会话")
    if not temporary and chat_session_id:
        raise HTTPException(status_code=422, detail="普通文档不能绑定聊天会话")
    if chat_session_id and space_id:
        raise HTTPException(status_code=422, detail="会话临时附件不能写入知识空间")

    filename = (file.filename or "document").strip()
    ext = Path(filename).suffix.lower()
    if temporary and ext in image_documents.IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail="图片入库请上传到资料库；聊天看图请使用图片发送入口。",
        )
    try:
        normalized_source_path = normalize_source_relative_path(
            source_relative_path,
            filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raw = await _read_upload_bytes(file)
    err = validate_file(
        filename, len(raw), allow_presentation_templates=temporary
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    if "%" in filename:
        raise HTTPException(status_code=400, detail="文件名不能包含 %")
    doc_name = (name or "").strip() or Path(filename).stem
    category_id = (category_id or "").strip() or None
    if ext in image_documents.IMAGE_EXTENSIONS:
        try:
            await asyncio.to_thread(
                image_documents.validate_image_document, raw, filename,
            )
        except image_documents.ImageDocumentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    temporary_expires_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=settings.temporary_attachment_ttl_seconds)
        if temporary and settings.temporary_attachment_ttl_seconds > 0
        else None
    )

    created_document: Document | None = None
    local_file_path: Path | None = None
    stored_refs: list[str] = []
    try:
        with transaction() as db:
            if save_message_id is not None:
                # 无值变更的 UPDATE 在 SQLite/PG 均持有写锁，直到资料提交；
                # 等锁不能阻塞事件循环，否则同进程的持锁上传无法完成。
                # 与删除/同步聊天一致，先锁会话再锁消息，避免 PG 反向锁顺序。
                await asyncio.to_thread(
                    db.execute,
                    update(ChatSession).where(
                        ChatSession.owner_user_id == user.id,
                        ChatSession.id.in_(select(ChatMessage.session_id).where(
                            ChatMessage.id == save_message_id,
                            ChatMessage.owner_user_id == user.id,
                        )),
                    ).values(messages_revision=ChatSession.messages_revision)
                    .execution_options(synchronize_session=False),
                )
                locked = await asyncio.to_thread(
                    db.execute,
                    update(ChatMessage)
                    .where(
                        ChatMessage.id == save_message_id,
                        ChatMessage.owner_user_id == user.id,
                    )
                    .values(content=ChatMessage.content)
                    .execution_options(synchronize_session=False),
                )
                message = db.get(ChatMessage, save_message_id)
                if (
                    locked.rowcount != 1
                    or message is None
                    or message.role != "assistant"
                ):
                    raise HTTPException(status_code=404, detail="聊天消息不存在")
                assert space_id is not None
                await asyncio.to_thread(
                    _lock_chat_save_context, db, message=message,
                    space_id=space_id, user=user, result_id=save_result_id,
                    result_version=save_result_version,
                )
            if space_id:
                PermissionService.require_space_member_access(
                    db, user, space_id, minimum_role="contributor"
                )
                if save_message_id is not None:
                    existing = _saved_chat_document(
                        db, space_id=space_id,
                        source_relative_path=normalized_source_path,
                    )
                    if existing is not None:
                        return SaveChatMessageAsDocumentResponse(
                            document=document_response(db, existing), reused=True,
                        )

                categories = load_space_categories(db, space_id)
                if category_id and not any(
                    category.id == category_id for category in categories
                ):
                    raise HTTPException(status_code=404, detail="文档分类不存在")
                if category_id is None:
                    category_id = classify_by_rules(
                        categories,
                        filename=filename,
                        document_name=doc_name,
                        source_relative_path=normalized_source_path,
                    )
            elif category_id:
                raise HTTPException(status_code=422, detail="临时附件不能指定分类")

            if temporary and chat_session_id:
                session = chat_repo.get_owned_session(db, chat_session_id, user.id)
                if not session:
                    raise HTTPException(status_code=404, detail="聊天会话不存在")

            created_document, version = documents.create_document(
                db,
                space_id=space_id,
                category_id=category_id,
                name=doc_name,
                created_by_user_id=user.id,
                file_path=None,
                source_relative_path=normalized_source_path,
                ext_task_id=None,
                is_temporary=temporary,
                chat_session_id=chat_session_id if temporary and not space_id else None,
                expires_at=temporary_expires_at,
            )

            upload_dir = settings.upload_dir / (space_id or f"temporary_{user.id}")
            upload_dir.mkdir(parents=True, exist_ok=True)
            local_file_path = upload_dir / f"{created_document.id}{ext}"
            await asyncio.to_thread(lambda: local_file_path.write_bytes(raw))
            version.file_path = str(local_file_path)

            job = await _submit_and_schedule(
                db,
                document=created_document,
                version=version,
                filename=filename,
                content=raw,
                created_by_user_id=user.id,
                background_tasks=background_tasks,
            )
            stored_refs = _document_storage_refs(created_document)
            if save_message_id is not None:
                assert message is not None and space_id is not None
                await asyncio.to_thread(
                    _lock_chat_save_context, db, message=message,
                    space_id=space_id, user=user, result_id=save_result_id,
                    result_version=save_result_version,
                )
                audit.record(
                    db, actor=user.id, event_type="chat_message_saved_as_document",
                    target=created_document.id, result="success",
                )
                return SaveChatMessageAsDocumentResponse(
                    document=document_response(db, created_document), reused=False,
                )

            return DocumentUploadResponse(
                document=document_response(db, created_document),
                task=processing_task(db, job, user),
            )
    except Exception:
        cleanup_refs = [*stored_refs]
        if local_file_path is not None:
            cleanup_refs.append(str(local_file_path))
        failures = _purge_stored_files(cleanup_refs)
        if failures:
            logger.error("upload cleanup left files behind: %s", failures)
        raise


@router.post(
    "/knowledge-spaces/{space_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=201,
)
async def upload_document(
    space_id: str,
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(description="文档文件")],
    name: Annotated[str | None, Form(description="文档名称，留空使用文件名")] = None,
    category_id: Annotated[str | None, Form(description="分类ID")] = None,
    source_relative_path: Annotated[
        str | None,
        Form(description="文件在上传目录中的相对路径", max_length=1024),
    ] = None,
    temporary: Annotated[
        bool,
        Form(description="是否作为临时附件，过期后由清理任务删除"),
    ] = False,
    user: User = Depends(current_user),
) -> DocumentUploadResponse:
    return cast(DocumentUploadResponse, await _upload_document(
        space_id=space_id,
        background_tasks=background_tasks,
        file=file,
        name=name,
        category_id=category_id,
        source_relative_path=source_relative_path,
        temporary=temporary,
        user=user,
    ))


@router.post(
    "/chat/attachments",
    response_model=DocumentUploadResponse,
    status_code=201,
)
async def upload_chat_attachment(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(description="聊天临时附件")],
    chat_session_id: Annotated[
        str, Form(description="绑定的聊天会话 ID", min_length=1, max_length=64)
    ],
    name: Annotated[str | None, Form(description="附件名称，留空使用文件名")] = None,
    source_relative_path: Annotated[
        str | None,
        Form(description="文件在上传目录中的相对路径", max_length=1024),
    ] = None,
    user: User = Depends(current_user),
) -> DocumentUploadResponse:
    return cast(DocumentUploadResponse, await _upload_document(
        space_id=None,
        background_tasks=background_tasks,
        file=file,
        name=name,
        category_id=None,
        source_relative_path=source_relative_path,
        temporary=True,
        chat_session_id=chat_session_id.strip(),
        user=user,
    ))


@router.get("/chat/attachments", response_model=DocumentListResponse)
def list_chat_attachments(
    chat_session_id: Annotated[
        str, Query(description="聊天会话 ID", min_length=1, max_length=64)
    ],
    user: User = Depends(current_user),
) -> DocumentListResponse:
    """List temporary chat attachments bound to one owned session."""
    session_id = chat_session_id.strip()
    with transaction() as db:
        session = chat_repo.get_owned_session(db, session_id, user.id)
        if not session:
            raise HTTPException(status_code=404, detail="聊天会话不存在")
        rows = documents.list_active_temporary_documents(
            db, user_id=user.id, chat_session_id=session_id
        )
        return DocumentListResponse(
            items=[document_response(db, row) for row in rows],
            next_cursor=None,
            can_manage_documents=True,
        )


@router.get("/jobs/{job_id}", response_model=TaskSnapshotResponse)
def get_document_job(
    job_id: str, user: User = Depends(current_user)
) -> TaskSnapshotResponse:
    with transaction() as db:
        job = documents.get_job(db, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        document = db.get(Document, job.document_id)
        if not document:
            raise HTTPException(status_code=404, detail="任务不存在")
        _require_active_document(document)
        _require_document_access(db, user, document, minimum_role="reader")
        return processing_task(db, job, user)


@router.post("/jobs/{job_id}:cancel", response_model=TaskSnapshotResponse)
def cancel_document_job(
    job_id: str, user: User = Depends(current_user)
) -> TaskSnapshotResponse:
    with transaction() as db:
        job = documents.get_job(db, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        document = db.get(Document, job.document_id)
        version = db.get(DocumentVersion, job.version_id)
        if not document or not version:
            raise HTTPException(status_code=404, detail="任务不存在")
        _require_active_document(document)
        _require_document_access(db, user, document, minimum_role="contributor")
        documents.reconcile_job_with_version(db, job, version)
        if not documents.job_is_cancellable(job, version):
            raise HTTPException(status_code=409, detail="任务已结束，无法取消")
        documents.cancel_job(db, job, version)
        audit.record(db, user.id, "document_job_cancelled", job.id, "success")
        return processing_task(db, job, user)


@router.get("/documents/search", response_model=DocumentSearchResponse)
def search_documents(
    q: str = Query(..., min_length=1, max_length=255),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    queryable: bool | None = Query(default=None),
    user: User = Depends(current_user),
) -> DocumentSearchResponse:
    """Fuzzy-search documents by name across every space the user can read."""
    with transaction() as db:
        items, total = documents.search_documents_across_spaces(
            db,
            user_id=user.id,
            is_admin=user.role == "admin",
            query_text=q,
            queryable=queryable,
            limit=limit,
            offset=offset,
        )
        creator_names = documents.creator_display_names(
            db, [item.created_by_user_id for item, _space in items]
        )
        return DocumentSearchResponse(
            items=[
                DocumentSearchItem(
                    id=document.id,
                    name=document.name,
                    space_id=space.id,
                    space_name=space.name,
                    category_id=document.category_id,
                    status=document.status,
                    created_by_display_name=creator_names.get(
                        document.created_by_user_id
                    ),
                    updated_at=document.updated_at,
                )
                for document, space in items
            ],
            total=total,
            limit=limit,
        )


@router.get("/documents/{document_id}/preview", response_model=DocumentPreviewResponse)
def preview_document(
    document_id: str,
    version_id: str | None = Query(default=None),
    user: User = Depends(current_user),
) -> DocumentPreviewResponse:
    """Return a bounded, safe text preview; binary files fall back to extracted text or summary."""
    with transaction() as db:
        document = documents.get_document(db, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        _require_active_document(document)
        _require_document_access(db, user, document, minimum_role="reader")
        version = _select_document_version(db, document, version_id)
        if not version:
            return DocumentPreviewResponse(
                document_id=document.id,
                kind="unavailable",
            )

        extension = _version_file_extension(version) or ""
        if extension in image_documents.IMAGE_EXTENSIONS:
            page_count = version.page_count
            if not page_count or version.processing_stage in documents.PROCESSING_STAGES:
                try:
                    raw, _ = _load_version_bytes(version)
                    page_count = image_documents.validate_image_document(
                        raw, f"source{extension}",
                    )
                except FileNotFoundError:
                    raise HTTPException(status_code=404, detail="原文件已丢失") from None
                except image_documents.ImageDocumentError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from None
            pages = db.scalars(
                select(DocumentPage)
                .where(DocumentPage.document_version_id == version.id)
                .order_by(DocumentPage.page_number)
            ).all()
            extracted = "\n\n".join(page.text for page in pages)
            content, truncated = _truncate_preview(image_documents.image_source_text(version, extracted, db=db))
            return DocumentPreviewResponse(
                document_id=document.id,
                version_id=version.id,
                kind="image",
                content=content or None,
                truncated=truncated,
                page_count=page_count,
            )
        if extension == ".pdf":
            return DocumentPreviewResponse(
                document_id=document.id,
                version_id=version.id,
                kind="pdf",
            )

        if extension in {".doc", ".docx"}:
            try:
                raw, _ = _load_version_bytes(version)
            except FileNotFoundError:
                raw = None
            except Exception as e:
                logger.exception("document preview failed for %s", document_id)
                raise HTTPException(status_code=502, detail=f"读取文件失败：{e}") from e
            if raw is None:
                return DocumentPreviewResponse(
                    document_id=document.id,
                    version_id=version.id,
                    kind="unavailable",
                )
            # Faithful preview: render the real layout and embedded images to
            # PDF and let the browser's native viewer display it. The extracted
            # text below is only a fallback when the renderer is unavailable
            # or a specific file cannot be converted.
            try:
                if office_preview_cached(raw) or render_office_preview_pdf(
                    raw, f"preview{extension}"
                ):
                    return DocumentPreviewResponse(
                        document_id=document.id,
                        version_id=version.id,
                        kind="pdf",
                    )
            except (LibreOfficeUnavailable, DocumentConversionError) as e:
                logger.warning(
                    "office pdf preview unavailable for %s (%s): %s",
                    document_id,
                    extension,
                    e,
                )
            try:
                extracted = (
                    extract_doc_text(raw)
                    if extension == ".doc"
                    else extract_docx_text(raw)
                )
            except Exception:
                logger.exception("office text preview failed for %s", document_id)
                extracted = ""
            if extracted.strip():
                content, truncated = _truncate_preview(extracted)
                return DocumentPreviewResponse(
                    document_id=document.id,
                    version_id=version.id,
                    kind="text",
                    content=content,
                    truncated=truncated,
                )
            # Real Word files can be image-only or contain no extractable text;
            # fall through to summary/unavailable rather than emitting binary noise.
            return DocumentPreviewResponse(
                document_id=document.id,
                version_id=version.id,
                kind="unavailable",
            )

        if extension in _TEXT_PREVIEW_EXTENSIONS:
            try:
                raw, _ = _load_version_bytes(version)
            except FileNotFoundError:
                raw = None
            except Exception as e:
                logger.exception("document preview failed for %s", document_id)
                raise HTTPException(status_code=502, detail=f"读取文件失败：{e}") from e
            if raw is not None:
                content, truncated = _truncate_preview(decode_text_bytes(raw))
                return DocumentPreviewResponse(
                    document_id=document.id,
                    version_id=version.id,
                    kind="markdown" if extension in {".md", ".markdown"} else "text",
                    content=content,
                    truncated=truncated,
                )

        if extension in _SPREADSHEET_PREVIEW_EXTENSIONS:
            try:
                raw, _ = _load_version_bytes(version)
            except FileNotFoundError:
                raw = None
            except Exception as e:
                logger.exception("document preview failed for %s", document_id)
                raise HTTPException(status_code=502, detail=f"读取文件失败：{e}") from e
            if raw is not None:
                try:
                    extracted = extract_spreadsheet_text(raw, extension)
                except Exception:
                    logger.exception("spreadsheet preview failed for %s", document_id)
                    extracted = ""
                if extracted.strip():
                    content, truncated = _truncate_preview(extracted)
                    return DocumentPreviewResponse(
                        document_id=document.id,
                        version_id=version.id,
                        kind="markdown",
                        content=content,
                        truncated=truncated,
                    )

        sidecar = extracted_sidecar_path(version.file_path)
        if (
            sidecar
            and sidecar.is_file()
            and extension not in _SPREADSHEET_PREVIEW_EXTENSIONS
        ):
            try:
                content, truncated = _truncate_preview(
                    sidecar.read_text(encoding="utf-8", errors="replace")
                )
                return DocumentPreviewResponse(
                    document_id=document.id,
                    version_id=version.id,
                    kind="text",
                    content=content,
                    truncated=truncated,
                )
            except OSError:
                logger.exception("failed to read extracted preview for %s", document_id)

        if version.summary:
            content, truncated = _truncate_preview(version.summary)
            return DocumentPreviewResponse(
                document_id=document.id,
                version_id=version.id,
                kind="summary",
                content=content,
                truncated=truncated,
            )
        return DocumentPreviewResponse(
            document_id=document.id,
            version_id=version.id,
            kind="unavailable",
        )


def _image_preview_response(raw: bytes, extension: str, page_number: int) -> Response:
    try:
        image = image_documents.render_image_page(
            raw, page_number, filename=f"source{extension}",
        )
    except image_documents.ImageDocumentError as exc:
        status = 404 if exc.code == "image_page_not_found" else 422
        raise HTTPException(status_code=status, detail=str(exc)) from None
    return Response(
        content=image,
        media_type="image/png",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/documents/{document_id}/preview/file")
def preview_document_file(
    document_id: str,
    version_id: str | None = Query(default=None),
    page_number: int = Query(default=1, ge=1),
    user: User = Depends(current_user),
) -> Response:
    """按版本权限返回 PDF 或安全规范化的图片，不暴露存储地址。"""
    with transaction() as db:
        document = documents.get_document(db, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        _require_active_document(document)
        _require_document_access(db, user, document, minimum_role="reader")
        version = _select_document_version(db, document, version_id)
        if not version:
            raise HTTPException(status_code=404, detail="无可预览的文件")
        extension = _version_file_extension(version) or ""
        if extension not in image_documents.IMAGE_EXTENSIONS | {".pdf", ".doc", ".docx"}:
            raise HTTPException(status_code=404, detail="该格式不支持原件预览")
        try:
            raw, _ = _load_version_bytes(version)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="原文件已丢失") from None
        except Exception as e:
            logger.exception("preview file failed for %s", document_id)
            raise HTTPException(status_code=502, detail=f"读取文件失败：{e}") from e
        if extension in image_documents.IMAGE_EXTENSIONS:
            return _image_preview_response(raw, extension, page_number)
        if extension == ".pdf":
            content = raw
        else:
            try:
                content = render_office_preview_pdf(raw, f"preview{extension}")
            except (LibreOfficeUnavailable, DocumentConversionError):
                logger.exception(
                    "office pdf preview unavailable for %s (%s)", document_id, extension
                )
                raise HTTPException(
                    status_code=422,
                    detail="文档暂无法在线预览，请下载原文件查看。",
                ) from None
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": "inline"},
        )


@router.get("/documents/{document_id}/versions/{version_id}/pages/{page_number}/image")
def document_page_image(
    document_id: str,
    version_id: str,
    page_number: int,
    user: User = Depends(current_user),
) -> Response:
    """按版本权限返回 PDF 页或图片帧，不暴露存储地址。"""
    if page_number < 1:
        raise HTTPException(status_code=404, detail="页面不存在")
    with transaction() as db:
        document = documents.get_document(db, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        _require_active_document(document)
        _require_document_access(db, user, document, minimum_role="reader")
        version = db.get(DocumentVersion, version_id)
        if not version or version.document_id != document.id:
            raise HTTPException(status_code=404, detail="文档版本不存在")
        extension = _version_file_extension(version) or ""
        if extension not in image_documents.IMAGE_EXTENSIONS | {".pdf"}:
            raise HTTPException(status_code=409, detail="该版本不支持页面图片")
        page = db.scalar(
            select(DocumentPage).where(
                DocumentPage.document_version_id == version.id,
                DocumentPage.page_number == page_number,
            )
        )
        if not page and extension not in image_documents.IMAGE_EXTENSIONS:
            raise HTTPException(status_code=404, detail="页面不存在")
        version_file_path = version.file_path
    try:
        content, _ = _load_version_bytes(
            DocumentVersion(
                id=version.id, document_id=document.id, file_path=version_file_path
            )
        )
        if extension in image_documents.IMAGE_EXTENSIONS:
            return _image_preview_response(content, extension, page_number)
        image = render_pdf_page(content, page_number, dpi=144)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="原文件已丢失") from None
    except HTTPException:
        raise
    except Exception as exc:
        if isinstance(exc, PdfOcrError) and exc.code == "ocr_pixel_limit_exceeded":
            raise HTTPException(
                status_code=413, detail="页面尺寸超过安全上限"
            ) from None
        logger.exception(
            "PDF page render failed for %s/%s/%s", document_id, version_id, page_number
        )
        raise HTTPException(
            status_code=502, detail="PDF 页面渲染失败，请重试"
        ) from None
    return Response(
        content=image,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: str, user: User = Depends(current_user)
) -> DocumentDetailResponse:
    with transaction() as db:
        document = documents.get_document(db, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        _require_active_document(document)
        _require_document_access(db, user, document, minimum_role="reader")
        return document_detail_response(db, document)


@router.patch("/documents/{document_id}", response_model=DocumentResponse)
def update_document_category(
    document_id: str,
    payload: DocumentCategoryUpdateRequest,
    user: User = Depends(current_user),
) -> DocumentResponse:
    """Set or clear a document's category within its own knowledge space."""
    with transaction() as db:
        document = documents.get_document(db, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        _require_active_document(document)
        _require_document_access(db, user, document, minimum_role="contributor")

        category_id = (payload.category_id or "").strip() or None
        if category_id:
            if not document.space_id:
                raise HTTPException(status_code=422, detail="临时附件不能指定分类")
            category_exists = db.scalar(
                select(DocumentCategory.id).where(
                    DocumentCategory.id == category_id,
                    DocumentCategory.space_id == document.space_id,
                )
            )
            if not category_exists:
                raise HTTPException(status_code=404, detail="文档分类不存在")

        document.category_id = category_id
        document.updated_at = datetime.now(timezone.utc)
        audit.record(db, user.id, "document_category_updated", document.id, "success")
        return document_response(db, document)


@router.post("/documents/{document_id}:rename", response_model=DocumentResponse)
def rename_document(
    document_id: str,
    payload: DocumentRenameRequest,
    user: User = Depends(current_user),
) -> DocumentResponse:
    """修改显示名称，保留分类、版本与原件存储引用。"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="文档名称不能为空")
    with transaction() as db:
        document = documents.get_document(db, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        _require_active_document(document)
        _require_document_access(db, user, document, minimum_role="contributor")
        if document.name != name:
            document.name = name
            document.updated_at = datetime.now(timezone.utc)
            audit.record(db, user.id, "document_renamed", document.id, "success")
        return document_response(db, document)


def _resolve_persist_category_id(
    db: Session,
    *,
    space_id: str,
    document: Document,
    version: DocumentVersion,
    requested_category_id: str | None,
) -> str | None:
    category_id = (requested_category_id or "").strip() or None
    if category_id:
        category_exists = db.scalar(
            select(DocumentCategory.id).where(
                DocumentCategory.id == category_id,
                DocumentCategory.space_id == space_id,
            )
        )
        if not category_exists:
            raise HTTPException(status_code=404, detail="文档分类不存在")
        return category_id

    categories = load_space_categories(db, space_id)
    source_relative_path = version.source_relative_path or ""
    filename = (
        PurePosixPath(source_relative_path).name
        if source_relative_path
        else document.name
    )
    return classify_by_rules(
        categories,
        filename=filename,
        document_name=document.name,
        source_relative_path=source_relative_path,
    )


def _relocate_persisted_version_file(
    *,
    document: Document,
    version: DocumentVersion,
    content: bytes,
    filename: str,
) -> None:
    """Write version bytes under the target space namespace."""
    if not document.space_id:
        raise ValueError("persisted document must belong to a knowledge space")
    ext = Path(filename).suffix
    if not ext and version.file_path:
        ext = Path(version.file_path).suffix
    upload_name = filename if "." in filename else f"{document.name}{ext}"

    if minio_storage.is_minio_enabled():
        object_key = minio_storage.object_key_for_version(
            space_id=document.space_id,
            document_id=document.id,
            version_id=version.id,
            filename=upload_name,
        )
        minio_uri = minio_storage.upload_bytes(content=content, object_key=object_key)
        version.file_path = minio_uri
    else:
        upload_dir = settings.upload_dir / document.space_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        local_path = upload_dir / f"{document.id}{ext}"
        local_path.write_bytes(content)
        version.file_path = str(local_path)
    version.updated_at = datetime.now(timezone.utc)


async def _reindex_persisted_document(
    *,
    content: bytes,
    filename: str,
    space_id: str,
    document_id: str,
    version_id: str,
    is_current_published: bool = True,
) -> bool:
    category = derive_category(space_id)
    with transaction() as db:
        document = db.get(Document, document_id)
        owner_user_id = document.created_by_user_id if document else ""
    completed = await asyncio.to_thread(
        _process_local_pipeline,
        content=content,
        filename=filename,
        category=category,
        file_id=str(stable_knowledge_id(version_id)),
        document_id=document_id,
        version_id=version_id,
        job_id="",
        user_id=owner_user_id,
        is_current_published=is_current_published,
    )
    return completed is True


@router.post(
    "/documents/{document_id}:persist-to-space",
    response_model=DocumentResponse,
)
async def persist_temporary_document_to_space(
    document_id: str,
    payload: PersistTemporaryDocumentRequest,
    user: User = Depends(current_user),
) -> DocumentResponse:
    """Promote a processed temporary chat attachment into a knowledge space."""
    from sqlalchemy.orm import selectinload

    space_id = payload.space_id.strip()
    if not space_id:
        raise HTTPException(status_code=422, detail="未指定资料空间")

    with transaction() as db:
        document = db.scalar(
            select(Document)
            .options(selectinload(Document.versions))
            .where(Document.id == document_id)
        )
        if not document or document.status != "active":
            raise HTTPException(status_code=404, detail="文档不存在")
        if not document.is_temporary or document.space_id:
            raise HTTPException(status_code=422, detail="仅临时文稿可存入资料空间")
        if document.created_by_user_id != user.id:
            raise HTTPException(status_code=404, detail="文档不存在")

        version = documents.current_published_version(
            db, document.id
        ) or documents.latest_version(db, document.id)
        if not version or not version.queryable or version.processing_stage != "ready":
            raise HTTPException(
                status_code=422, detail="文档尚未解析完成，暂不能存入资料空间"
            )

        PermissionService.require_space_member_access(
            db, user, space_id, minimum_role="contributor"
        )
        category_id = _resolve_persist_category_id(
            db,
            space_id=space_id,
            document=document,
            version=version,
            requested_category_id=payload.category_id,
        )

        try:
            content, suffix = _load_version_bytes(version)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="原文件已丢失") from None
        except Exception as e:
            logger.exception(
                "persist temporary document read failed for %s", document_id
            )
            raise HTTPException(status_code=502, detail=f"读取文件失败：{e}") from e

        source_relative_path = version.source_relative_path or ""
        source_filename = (
            PurePosixPath(source_relative_path).name if source_relative_path else ""
        )
        filename = source_filename or f"{document.name}{suffix or ''}"

    _cleanup_local_document_vectors(document)

    old_refs: list[str] = []
    with transaction() as db:
        document = db.scalar(
            select(Document)
            .options(selectinload(Document.versions))
            .where(Document.id == document_id)
        )
        if not document or document.status != "active":
            raise HTTPException(status_code=404, detail="文档不存在")
        version = documents.current_published_version(
            db, document.id
        ) or documents.latest_version(db, document.id)
        if not version:
            raise HTTPException(status_code=404, detail="文档版本不存在")

        old_refs = _document_storage_refs(document)
        documents.persist_temporary_document(
            db,
            document,
            space_id=space_id,
            category_id=category_id,
        )
        _relocate_persisted_version_file(
            document=document,
            version=version,
            content=content,
            filename=filename,
        )
        audit.record(
            db,
            user.id,
            "document_persisted_to_space",
            document.id,
            "success",
        )
        response = document_response(db, document)

    _purge_stored_files(old_refs)
    await _reindex_persisted_document(
        content=content,
        filename=filename,
        space_id=space_id,
        document_id=document_id,
        version_id=version.id,
    )

    with transaction() as db:
        document = documents.get_document(db, document_id)
        if not document:
            return response
        return document_response(db, document)


@router.post("/documents/{document_id}:move-to-space", response_model=DocumentResponse)
async def move_document_to_space(
    document_id: str,
    payload: MoveDocumentRequest,
    user: User = Depends(current_user),
) -> DocumentResponse:
    """移动已入库文档，保留版本身份并重建目标文件夹的索引。"""
    from sqlalchemy.orm import selectinload

    space_id = payload.space_id.strip()
    with transaction() as db:
        source_document = db.scalar(
            select(Document).options(selectinload(Document.versions))
            .where(Document.id == document_id)
        )
        if not source_document:
            raise HTTPException(status_code=404, detail="文档不存在")
        _require_active_document(source_document)
        _require_document_access(db, user, source_document, minimum_role="contributor")
        if source_document.is_temporary or not source_document.space_id:
            raise HTTPException(status_code=422, detail="临时附件请使用存入资料库")
        if source_document.space_id == space_id:
            raise HTTPException(status_code=422, detail="文档已在目标文件夹中")
        target = db.get(KnowledgeSpace, space_id)
        if not target or target.status != "active":
            raise HTTPException(status_code=422, detail="目标文件夹不存在或已归档")
        PermissionService.require_space_member_access(
            db, user, space_id, minimum_role="contributor"
        )
        published = documents.current_published_version(db, document_id)
        if not published or any(
            version.processing_stage in documents.PROCESSING_STAGES
            for version in source_document.versions
        ):
            raise HTTPException(status_code=422, detail="文档尚未处理完成，暂不能移动")
        category_id = _resolve_persist_category_id(
            db, space_id=space_id, document=source_document, version=published,
            requested_category_id=payload.category_id,
        )
        published_id = published.id
        indexed_ids = [
            version.id for version in source_document.versions
            if version.processing_stage == "ready"
        ]
        old_refs = _document_storage_refs(source_document)
        source_updated_at = source_document.updated_at
        source_space_id = source_document.space_id

    new_refs: list[str] = []
    try:
        with transaction() as db:
            document = db.scalar(
                select(Document).options(selectinload(Document.versions))
                .where(Document.id == document_id)
            )
            if not document:
                raise HTTPException(status_code=404, detail="文档不存在")
            _require_active_document(document)
            _require_document_access(db, user, document, minimum_role="contributor")
            PermissionService.require_space_member_access(
                db, user, space_id, minimum_role="contributor"
            )
            if (
                document.space_id != source_space_id
                or document.updated_at != source_updated_at
                or any(
                    version.processing_stage in documents.PROCESSING_STAGES
                    for version in document.versions
                )
                or not documents.move_document_to_space(
                    db, document, space_id=space_id, category_id=category_id
                )
            ):
                raise HTTPException(status_code=409, detail="文档状态已变化，请刷新后重试")
            # 空间切换与源图谱失效原子提交，不能暴露已移动却仍可读的旧实体。
            other_version_ids = documents.invalidate_moved_document_graph(
                db, source_space_id=source_space_id,
                version_ids=[version.id for version in source_document.versions],
            )
            # 先复制所有原件，再提交路径；失败时只清理新副本，保留源文件。
            for version in document.versions:
                if not version.file_path:
                    if version.id in indexed_ids:
                        raise FileNotFoundError(version.id)
                    continue
                old_path = version.file_path
                content, suffix = _load_version_bytes(version)
                filename = PurePosixPath(version.source_relative_path or "").name
                filename = filename or f"{document.name}{suffix}"
                if minio_storage.is_minio_enabled():
                    _relocate_persisted_version_file(
                        document=document, version=version, content=content,
                        filename=filename,
                    )
                else:
                    # 版本级路径避免历史版本和重试版本互相覆盖。
                    path = settings.upload_dir / space_id / document_id / f"{version.id}{suffix}"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    new_refs.append(str(path))
                    path.write_bytes(content)
                    version.file_path = str(path)
                    version.updated_at = datetime.now(timezone.utc)
                new_refs.append(version.file_path)
                # 重试队列与版本原件必须指向同一目标；本地原件走 file_path。
                canonical_bucket, canonical_key = (
                    minio_storage.parse_minio_uri(version.file_path)
                    if minio_storage.is_minio_uri(version.file_path)
                    else (None, None)
                )
                db.execute(
                    update(DocumentUploadSession)
                    .where(
                        DocumentUploadSession.document_id == document.id,
                        DocumentUploadSession.version_id == version.id,
                    )
                    .values(
                        canonical_bucket=canonical_bucket,
                        canonical_object_key=canonical_key,
                    )
                )
                old_sidecar = extracted_sidecar_path(old_path)
                new_sidecar = extracted_sidecar_path(version.file_path)
                if old_sidecar and new_sidecar and old_sidecar.is_file():
                    new_sidecar.parent.mkdir(parents=True, exist_ok=True)
                    new_sidecar.write_bytes(old_sidecar.read_bytes())
            audit.record(db, user.id, "document_moved_to_space", document_id, "success")
    except Exception as exc:
        _purge_stored_files(new_refs)
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, FileNotFoundError):
            raise HTTPException(status_code=404, detail="原文件已丢失，文档未移动") from exc
        logger.exception("document move transaction failed for %s", document_id)
        raise HTTPException(status_code=502, detail="文档移动失败，请稍后重试") from exc

    if other_version_ids:
        logger.warning(
            "源实体已失效，需在源文件夹对其它文档重试处理以重建："
            "space=%s version_ids=%s moved_document=%s",
            source_space_id, other_version_ids, document_id,
        )

    try:
        # 使用移动前的独立快照清除源索引，避免错删目标命名空间。
        _cleanup_local_document_vectors(source_document)
        _purge_stored_files(old_refs)
        # 原发布版本最后完成；其 queued 状态同时阻止第二次移动插入重建过程。
        for version_id in sorted(indexed_ids, key=lambda value: value == published_id):
            with transaction() as db:
                version = db.get(DocumentVersion, version_id)
                if version is None:
                    raise RuntimeError("document version disappeared during move")
                content, suffix = _load_version_bytes(version)
                filename = PurePosixPath(version.source_relative_path or "").name
                filename = filename or f"{source_document.name}{suffix}"
            completed = await _reindex_persisted_document(
                content=content, filename=filename, space_id=space_id,
                document_id=document_id, version_id=version_id,
                is_current_published=version_id == published_id,
            )
            if not completed:
                raise RuntimeError("document move indexing failed")
    except Exception as exc:
        for version_id in indexed_ids:
            _mark_version_failed(
                version_id=version_id, job_id=None, failure_code="index_failed",
                failure_message="文档已移动，但目标文件夹索引重建失败，请重试处理。",
            )
        logger.exception("document move indexing failed for %s", document_id)
        raise HTTPException(
            status_code=502, detail="文档已移动，但索引重建失败，请在目标文件夹重试处理"
        ) from exc
    with transaction() as db:
        document = documents.get_document(db, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        return document_response(db, document)


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: str,
    version_id: str | None = Query(default=None),
    user: User = Depends(current_user),
) -> Response:
    """Download original uploaded bytes (latest version with a file, or a specific version)."""
    with transaction() as db:
        document = documents.get_document(db, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        _require_active_document(document)
        _require_document_access(db, user, document, minimum_role="reader")

        version = _select_document_version(db, document, version_id)
        if not version:
            raise HTTPException(status_code=404, detail="无可下载的文件")

        try:
            file_path = version.file_path or ""
            suffix = _version_file_extension(version) or ""
            return _attachment_response(
                file_path=file_path, filename=f"{document.name}{suffix}"
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="原文件已丢失") from None
        except Exception as e:
            logger.exception("document download failed for %s", document_id)
            raise HTTPException(status_code=502, detail=f"读取文件失败：{e}") from e


@router.post("/documents/{document_id}:archive", response_model=DocumentResponse)
def archive_document(
    document_id: str, user: User = Depends(current_user)
) -> DocumentResponse:
    with transaction() as db:
        document = documents.get_document(db, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        if document.status in ("archived", "deleted"):
            raise HTTPException(status_code=404, detail="文档不存在")
        _require_document_access(db, user, document, minimum_role="contributor")
        archived = documents.archive_document(document)
        audit.record(db, user.id, "document_archived", document.id, "success")
        return document_response(db, archived)


@router.delete("/documents/{document_id}", response_model=DocumentResponse)
def delete_document(
    document_id: str, user: User = Depends(current_user)
) -> DocumentResponse:
    from sqlalchemy.orm import selectinload

    with transaction() as db:
        document = db.scalar(
            select(Document)
            .options(selectinload(Document.versions))
            .where(Document.id == document_id)
        )
        if not document or document.status == "deleted":
            raise HTTPException(status_code=404, detail="文档不存在")
        _require_document_access(db, user, document, minimum_role="contributor")
        storage_refs = _document_storage_refs(document)
        paths = documents.delete_document(db, document)
        audit.record(db, user.id, "document_deleted", document.id, "success")
        response = document_response(db, document)
        is_temporary = document.is_temporary
    _cleanup_local_document_vectors(document)
    _purge_stored_files([*storage_refs, *paths])
    if is_temporary:
        completed_at = datetime.now(timezone.utc)
        with transaction() as db:
            current = db.get(Document, document_id)
            if (
                current
                and current.is_temporary
                and current.temporary_cleanup_at is None
            ):
                current.temporary_cleanup_at = completed_at
                current.updated_at = completed_at
    return response


class BatchDeleteRequest(BaseModel):
    document_ids: list[str]


@router.delete(
    "/knowledge-spaces/{space_id}/documents/batch",
    status_code=200,
)
def batch_delete_documents(
    space_id: str,
    payload: BatchDeleteRequest,
    user: User = Depends(current_user),
) -> dict:
    if not payload.document_ids:
        raise HTTPException(status_code=400, detail="未选择任何文档")
    with transaction() as db:
        PermissionService.require_space_member_access(
            db, user, space_id, minimum_role="contributor"
        )
        targets = list(
            db.scalars(
                select(Document).where(
                    Document.space_id == space_id,
                    Document.id.in_(payload.document_ids),
                    Document.status != "deleted",
                )
            ).all()
        )
        storage_refs = [
            ref for document in targets for ref in _document_storage_refs(document)
        ]
        count, paths = documents.batch_delete_documents(
            db, space_id, payload.document_ids
        )
        audit.record(db, user.id, "documents_batch_deleted", space_id, "success")
    _purge_stored_files([*storage_refs, *paths])
    return {"deleted": count}


def _process_retry(
    document_id: str,
    version_id: str,
    filename: str,
    content: bytes,
) -> None:
    """Background task: re-submit a retry version for processing."""
    with transaction() as db:
        document = db.get(Document, document_id)
        version = db.get(DocumentVersion, version_id)
        if not document or not version:
            return
        category = derive_category(_storage_space_id(document))
        owner_user_id = document.created_by_user_id

    _process_local_pipeline(
        content=content,
        filename=filename,
        category=category,
        file_id=str(stable_knowledge_id(version_id)),
        document_id=document_id,
        version_id=version_id,
        job_id="",
        user_id=owner_user_id,
    )


def _enqueue_multipart_retry(
    db: Session,
    *,
    document: Document,
    version: DocumentVersion,
    source_version_id: str,
    user_id: str,
) -> DocumentJob | None:
    from app.services.document_uploads import enqueue_stored_retry

    source = db.scalar(
        select(DocumentUploadSession.id).where(
            DocumentUploadSession.version_id == source_version_id,
            DocumentUploadSession.canonical_object_key.is_not(None),
        )
    )
    if source is None:
        return None
    job = documents.create_job(
        db, document=document, version=version, created_by_user_id=user_id
    )
    if not enqueue_stored_retry(
        db,
        document=document,
        version=version,
        job=job,
        source_version_id=source_version_id,
        user_id=user_id,
    ):
        raise HTTPException(status_code=409, detail="原文件当前无法重新处理")
    return job


class BatchRetryRequest(BaseModel):
    document_ids: list[str]


@router.post("/documents:batch-retry", status_code=200)
async def batch_retry_documents(
    payload: BatchRetryRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
) -> dict:
    """Retry the latest failed version for each selected document."""
    if not payload.document_ids:
        raise HTTPException(status_code=400, detail="未选择任何文档")

    retried = 0
    skipped = 0
    errors: list[str] = []
    # Collect retry jobs to process after commit.
    retry_jobs: list[tuple[Document, DocumentVersion, str, bytes]] = []

    with transaction() as db:
        targets = list(
            db.scalars(
                select(Document).where(
                    Document.id.in_(payload.document_ids),
                    Document.status == "active",
                )
            ).all()
        )
        if not targets:
            raise HTTPException(status_code=404, detail="未找到可重试的文档")

        for document in targets:
            _require_document_access(db, user, document, minimum_role="contributor")
            failed_version = db.scalar(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.processing_stage == "failed",
                    DocumentVersion.retryable.is_(True),
                )
                .order_by(DocumentVersion.version_number.desc())
                .limit(1)
            )
            if failed_version is None or not documents.is_retryable(db, failed_version):
                skipped += 1
                continue

            try:
                retry_version = documents.retry_failed_version(
                    db, document, failed_version
                )
                durable_job = _enqueue_multipart_retry(
                    db,
                    document=document,
                    version=retry_version,
                    source_version_id=failed_version.id,
                    user_id=user.id,
                )
                if durable_job is not None:
                    retried += 1
                    continue
                file_path = (retry_version.file_path or "").strip()
                raw: bytes | None = None
                source_filename = (
                    PurePosixPath(retry_version.source_relative_path).name
                    if retry_version.source_relative_path
                    else ""
                )
                filename = source_filename or document.name

                if file_path and minio_storage.is_minio_uri(file_path):
                    raw = await asyncio.to_thread(
                        minio_storage.download_bytes, minio_uri=file_path
                    )
                    if not raw:
                        raise RuntimeError("MinIO 文件为空")
                    if not source_filename:
                        filename = f"{document.name}{Path(minio_storage.parse_minio_uri(file_path)[1]).suffix}"
                else:
                    path = Path(file_path) if file_path else None
                    if path and path.is_file():
                        raw = await asyncio.to_thread(path.read_bytes)
                        if not source_filename:
                            filename = f"{document.name}{path.suffix}"

                if raw is None:
                    documents.update_version_stage(
                        db,
                        retry_version,
                        "failed",
                        failure_code="file_missing",
                        failure_message="原文件已丢失，无法重试",
                    )
                    documents.create_job(
                        db,
                        document=document,
                        version=retry_version,
                        created_by_user_id=user.id,
                    )
                    errors.append(f"{document.name}：原文件已丢失")
                    continue

                documents.create_job(
                    db,
                    document=document,
                    version=retry_version,
                    created_by_user_id=user.id,
                )
                retry_jobs.append((document, retry_version, filename, raw))
                retried += 1
            except Exception as exc:
                logger.exception("batch retry failed for document %s", document.id)
                errors.append(f"{document.name}：{exc}")

        audit.record(db, user.id, "documents_batch_retry", "", "success")

    # Schedule background processing after commit (db session is closed).
    for document, version, filename, content in retry_jobs:
        background_tasks.add_task(
            _process_retry,
            document.id,
            version.id,
            filename,
            content,
        )

    return {"retried": retried, "skipped": skipped, "errors": errors}


@router.post(
    "/documents/{document_id}/versions/{version_id}:retry",
    response_model=TaskSnapshotResponse,
)
async def retry_document_version(
    document_id: str,
    version_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
) -> TaskSnapshotResponse:
    with transaction() as db:
        document = documents.get_document(db, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        _require_active_document(document)
        _require_document_access(db, user, document, minimum_role="contributor")
        version = db.get(DocumentVersion, version_id)
        if not version or version.document_id != document.id:
            raise HTTPException(status_code=404, detail="文档版本不存在")
        if not documents.is_retryable(db, version):
            raise HTTPException(status_code=409, detail="该版本当前不可重试")

        retry_version = documents.retry_failed_version(db, document, version)
        durable_job = _enqueue_multipart_retry(
            db,
            document=document,
            version=retry_version,
            source_version_id=version.id,
            user_id=user.id,
        )
        if durable_job is not None:
            return processing_task(db, durable_job, user)
        file_path = (retry_version.file_path or "").strip()
        raw: bytes | None = None
        filename = document.name
        source_filename = (
            PurePosixPath(retry_version.source_relative_path).name
            if retry_version.source_relative_path
            else ""
        )
        filename = source_filename or document.name

        if file_path and minio_storage.is_minio_uri(file_path):
            try:
                raw = await asyncio.to_thread(
                    minio_storage.download_bytes, minio_uri=file_path
                )
                if not source_filename:
                    filename = f"{document.name}{Path(minio_storage.parse_minio_uri(file_path)[1]).suffix}"
            except Exception as e:
                logger.exception("retry minio download failed for %s", version_id)
                documents.update_version_stage(
                    db,
                    retry_version,
                    "failed",
                    failure_code="file_missing",
                    failure_message=f"MinIO 原文件不可用：{e}",
                )
                job = documents.create_job(
                    db,
                    document=document,
                    version=retry_version,
                    created_by_user_id=user.id,
                )
                return processing_task(db, job, user)
        else:
            path = Path(file_path) if file_path else None
            if path and path.is_file():
                raw = await asyncio.to_thread(path.read_bytes)
                if not source_filename:
                    filename = f"{document.name}{path.suffix}"

        if raw is None:
            documents.update_version_stage(
                db,
                retry_version,
                "failed",
                failure_code="file_missing",
                failure_message="原文件已丢失，无法重试",
            )
            job = documents.create_job(
                db, document=document, version=retry_version, created_by_user_id=user.id
            )
            return processing_task(db, job, user)

        job = await _submit_and_schedule(
            db,
            document=document,
            version=retry_version,
            filename=filename,
            content=raw,
            created_by_user_id=user.id,
            background_tasks=background_tasks,
        )
        return processing_task(db, job, user)


def _persist_in_flight_stage(version_id: str, stage: str) -> None:
    """Best-effort write of an intermediate processing stage."""
    with transaction() as db:
        version = db.get(DocumentVersion, version_id)
        if not version or version.processing_stage not in documents.PROCESSING_STAGES:
            return
        document = db.get(Document, version.document_id)
        if not document or document.status != "active":
            return
        if version.processing_stage == stage:
            return
        # Do not move backwards (e.g. indexing → parsing) if polls arrive out of order.
        current_rank = {
            "queued": 0,
            "scanning": 1,
            "processing": 1,
            "parsing": 2,
            "extracting_text": 2,
            "rendering_pages": 3,
            "running_ocr": 4,
            "chunking": 5,
            "quality_check": 6,
            "indexing": 7,
        }
        if current_rank.get(stage, 0) < current_rank.get(version.processing_stage, 0):
            return
        documents.update_version_stage(db, version, stage)


def _classify_processed_document(
    *,
    document_id: str,
    version_id: str,
    filename: str,
    text: str,
) -> str | None:
    """Classify an unassigned space document without affecting pipeline success."""
    with transaction() as db:
        version = db.get(DocumentVersion, version_id)
        document = db.get(Document, document_id)
        if (
            not version
            or not document
            or document.status != "active"
            or document.category_id
            or not document.space_id
        ):
            return None

        categories = load_space_categories(db, document.space_id)
        rule_category_id = classify_by_rules(
            categories,
            filename=filename,
            document_name=document.name,
            source_relative_path=version.source_relative_path,
        )
        if rule_category_id:
            return rule_category_id
        endpoint = resolve_llm_endpoint(db)
        user_id = document.created_by_user_id
        document_name = document.name
        source_relative_path = version.source_relative_path

    return classify_with_llm(
        categories,
        endpoint=endpoint,
        filename=filename,
        document_name=document_name,
        source_relative_path=source_relative_path,
        text=text,
        user_id=user_id,
    )


def process_stored_document(
    *,
    document_id: str,
    version_id: str,
    job_id: str,
    user_id: str,
    source_path: Path | None = None,
    lease_check: Callable[[], bool] | None = None,
    publication_check: Callable[[Session], bool] | None = None,
) -> None:
    """Process an immutable object reference.

    PPTX parsing uses the source path; existing extractors for other file
    types operate on byte content.
    """
    if lease_check is not None and not lease_check():
        return
    with transaction() as db:
        document = db.get(Document, document_id)
        version = db.get(DocumentVersion, version_id)
        job = db.get(DocumentJob, job_id)
        if (
            not document
            or document.status != "active"
            or not version
            or version.document_id != document_id
            or version.processing_stage not in documents.PROCESSING_STAGES
            or not job
            or job.status == "cancelled"
        ):
            return
        storage_ref = version.file_path or ""
        filename = PurePosixPath(version.source_relative_path or storage_ref).name
        category = derive_category(_storage_space_id(document))

    def process(path: Path) -> None:
        _process_local_pipeline(
            source_path=path,
            filename=filename,
            category=category,
            file_id=str(stable_knowledge_id(version_id)),
            document_id=document_id,
            version_id=version_id,
            job_id=job_id,
            user_id=user_id,
            lease_check=lease_check,
            publication_check=publication_check,
        )

    try:
        if source_path is not None:
            process(source_path)
        elif minio_storage.is_minio_uri(storage_ref):
            with TemporaryDirectory(prefix="nexus-document-") as directory:
                path = Path(directory) / f"source{Path(filename).suffix}"
                minio_storage.download_to_path(minio_uri=storage_ref, path=path)
                process(path)
        else:
            process(Path(storage_ref))
    except Exception:
        if lease_check is not None and not lease_check():
            return
        logger.exception("stored document processing failed for %s", document_id)
        _mark_version_failed(
            version_id=version_id,
            job_id=job_id,
            failure_code="file_missing",
            failure_message="读取原文件失败，请重试。",
        )


def _extract_image_document(
    content: bytes, *, filename: str, document_id: str, version_id: str,
    user_id: str, lease_check: Callable[[], bool] | None,
):
    from app.repositories.document_pages import cache_image_extraction, load_image_extraction
    from app.services.budget import finalize_vision_budget

    source_sha256 = hashlib.sha256(content).hexdigest()
    parser_version = str(image_documents.IMAGE_PARSER_VERSION)
    with transaction() as db:
        version = db.get(DocumentVersion, version_id)
        document = db.get(Document, document_id)
        actor = db.get(User, user_id)
        if not version or not document or not actor or document.status != "active":
            raise RuntimeError("图片资料已不可处理")
        _require_document_access(db, actor, document, minimum_role="contributor")
        cached = load_image_extraction(
            db, version=version, source_sha256=source_sha256,
            parser_version=parser_version,
        )
        endpoint = resolve_vision_endpoint(db, data_level="internal")
        space_ids = [document.space_id] if document.space_id else []

    finalized: set[str] = set()

    def checkpoint(extraction) -> None:
        if lease_check is not None and not lease_check():
            raise RuntimeError("图片处理租约已失效")
        with transaction() as db:
            version = db.scalar(
                select(DocumentVersion).where(DocumentVersion.id == version_id)
                .with_for_update()
            )
            document = db.get(Document, document_id)
            if (
                not version or not document or document.status != "active"
                or version.processing_stage not in documents.PROCESSING_STAGES
            ):
                raise RuntimeError("图片资料已停止处理")
            actor = db.get(User, user_id)
            if not actor:
                raise RuntimeError("图片资料访问权限已变化")
            _require_document_access(db, actor, document, minimum_role="contributor")
            cache_image_extraction(
                db, version=version, extraction=extraction,
                source_sha256=source_sha256, parser_version=parser_version,
            )
        # 识别内容与真实费用提交之后才释放预算占用。
        for reservation_id in extraction.vision_reservation_ids:
            if reservation_id in finalized:
                continue
            finalize_vision_budget(reservation_id)
            finalized.add(reservation_id)

    result = image_documents.extract_image_pages(
        content, filename=filename, vision_endpoint=endpoint,
        user_id=user_id, operation_id=version_id, space_ids=space_ids,
        cached=cached, checkpoint=checkpoint,
    )
    checkpoint(result)
    return result


def _process_local_pipeline(
    *,
    content: bytes = b"",
    source_path: Path | None = None,
    filename: str,
    category: str,
    file_id: str,
    document_id: str,
    version_id: str,
    job_id: str,
    user_id: str = "",
    lease_check: Callable[[], bool] | None = None,
    publication_check: Callable[[Session], bool] | None = None,
    is_current_published: bool = True,
) -> bool | None:
    """Background task: extract text, chunk, embed, and store in the local vector store."""
    from app.core.security import now as _now

    # 1. Extract text from file content
    ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "").strip()
    is_image = f".{ext}" in image_documents.IMAGE_EXTENSIONS
    text = ""
    pdf_extraction = None
    pdf_page_blocks = None
    extraction_error_code: str | None = None
    try:
        _persist_in_flight_stage(version_id, "extracting_text")
        if source_path is not None and ext not in ("pptx", "potx"):
            content = source_path.read_bytes()
        if source_path is None and not content:
            raise ValueError("缺少文档内容")
        if is_image:
            pdf_extraction = _extract_image_document(
                content, filename=filename, document_id=document_id,
                version_id=version_id, user_id=user_id, lease_check=lease_check,
            )
            text = pdf_extraction.text
        elif ext in (
            "txt",
            "md",
            "markdown",
            "csv",
            "json",
            "jsonl",
            "log",
            "py",
            "js",
            "ts",
            "java",
            "go",
            "rs",
            "xml",
            "html",
            "htm",
            "yaml",
            "yml",
            "toml",
        ):
            text = decode_text_bytes(content)
        elif ext in ("docx", "doc"):
            if ext == "doc":
                from app.services.document_processor import extract_doc_text

                text = extract_doc_text(content)
            else:
                from app.services.document_processor import extract_docx_text

                text = extract_docx_text(content)
        elif ext in ("pptx", "potx"):
            from app.services.document_processor import extract_pptx_text

            text = extract_pptx_text(
                source_path if source_path is not None else content
            )
        elif ext == "ppt":
            from app.services.doc_preview import convert_presentation_to_pptx
            from app.services.document_processor import extract_pptx_text

            text = extract_pptx_text(convert_presentation_to_pptx(content, filename))
        elif ext in ("pdf",):
            _persist_in_flight_stage(version_id, "extracting_text")
            vision_endpoint = None
            if settings.vision_fallback_enabled:
                with transaction() as db:
                    vision_endpoint = resolve_vision_endpoint(
                        db, data_level="restricted"
                    )
            pdf_extraction = extract_pdf_pages(
                content,
                vision_endpoint=vision_endpoint,
                user_id=user_id,
                operation_id=version_id,
                vision_enabled=settings.vision_fallback_enabled,
            )
            if any(
                page.warning_code or page.extraction_method in {"ocr", "vision"}
                for page in pdf_extraction.pages
            ):
                _persist_in_flight_stage(version_id, "rendering_pages")
            if pdf_extraction.ocr_page_count:
                _persist_in_flight_stage(version_id, "running_ocr")
            text = pdf_extraction.text
        elif ext in ("xlsx", "xls"):
            text = extract_spreadsheet_text(content, ext)
        else:
            # Try UTF-8 decode as fallback for unknown formats
            text = content.decode("utf-8", errors="replace")
    except image_documents.ImageDocumentError as e:
        extraction_error_code = e.code
        logger.warning("Image extraction rejected for %s: %s", version_id, e.code)
    except PdfOcrError as e:
        extraction_error_code = e.code
        logger.error("PDF extraction failed for %s (%s): %s", filename, ext, e.code)
    except Exception as e:
        if is_image:
            extraction_error_code = "image_vision_failed"
        logger.error("Text extraction failed for %s (%s): %s", filename, ext, e)

    if pdf_extraction is not None:
        if not pdf_extraction.text.strip() and pdf_extraction.warning_code:
            extraction_error_code = pdf_extraction.warning_code
        from app.repositories.document_pages import replace_version_pages

        page_rows = []
        with transaction() as db:
            version = db.get(DocumentVersion, version_id)
            if version:
                if is_image:
                    page_rows = list(db.scalars(
                        select(DocumentPage).where(DocumentPage.document_version_id == version.id)
                        .order_by(DocumentPage.page_number)
                    ))
                else:
                    page_rows = replace_version_pages(
                        db, version=version, extraction=pdf_extraction,
                    )
                from app.models.document_pages import DocumentPageBlock

                page_ids = [page.id for page in page_rows]
                pdf_page_blocks = list(
                    db.scalars(
                        select(DocumentPageBlock).where(
                            DocumentPageBlock.page_id.in_(page_ids)
                        )
                    ).all()
                )

        # The document usage row is committed above.  Only then may the
        # settled provider holds be cleared; a failed transaction keeps its
        # conservative short-lived holds for retry/expiry.
        if not is_image and page_rows and pdf_extraction.vision_reservation_ids:
            from app.services.budget import finalize_vision_budget

            for reservation_id in pdf_extraction.vision_reservation_ids:
                try:
                    finalize_vision_budget(reservation_id)
                except Exception:
                    logger.warning(
                        "PDF vision reservation finalization failed reservation_id=%s",
                        reservation_id,
                        exc_info=True,
                    )

    if lease_check is not None and not lease_check():
        return
    if not text.strip():
        if is_image and extraction_error_code and extraction_error_code.startswith("vision_"):
            extraction_error_code = f"image_{extraction_error_code}"
        _mark_version_failed(
            version_id=version_id,
            job_id=job_id,
            failure_code=extraction_error_code or "extract_failed",
            failure_message=(
                _SAFE_FAILURE_MESSAGES.get(extraction_error_code or "", _SAFE_FAILURE_MESSAGES["image_vision_failed"])
                if is_image else f"无法从 {ext} 文件中提取文本"
            ),
        )
        return

    # 2. Run knowledge pipeline (chunk → embed → store)
    def _on_progress(progress):
        if lease_check is not None and not lease_check():
            raise RuntimeError("Document processing lease lost")
        # Only complete_version may publish readiness after the full pipeline.
        if progress.stage in documents.PROCESSING_STAGES:
            _persist_in_flight_stage(version_id, progress.stage)

    try:
        result = run_knowledge_pipeline(
            text=text,
            category=category,
            file_id=file_id,
            on_progress=_on_progress,
            page_spans=pdf_extraction.page_spans
            if pdf_extraction is not None
            else None,
            page_blocks=pdf_page_blocks,
        )
    except Exception as e:
        if lease_check is not None and not lease_check():
            return
        logger.exception("Local knowledge pipeline failed for %s", document_id)
        _mark_version_failed(
            version_id=version_id,
            job_id=job_id,
            failure_code="pipeline_failed",
            failure_message=str(e)[:500],
        )
        return

    # 3. Classify only after extraction and indexing have succeeded. This is
    # best-effort: the classifier never changes the pipeline result.
    try:
        classification_id = _classify_processed_document(
            document_id=document_id,
            version_id=version_id,
            filename=filename,
            text=text,
        )
    except Exception:
        logger.exception("document category classification failed for %s", document_id)
        classification_id = None
    chunk_count = result.get("chunk_count", 0)
    if lease_check is not None and not lease_check():
        return
    with transaction() as db:
        version = db.get(DocumentVersion, version_id)
        if not version or version.processing_stage not in documents.PROCESSING_STAGES:
            return
        document = db.get(Document, version.document_id)
        if not document or document.status != "active":
            return
        if publication_check is not None and not publication_check(db):
            return
        if classification_id and document.category_id is None:
            category_exists = db.scalar(
                select(DocumentCategory.id).where(
                    DocumentCategory.id == classification_id,
                    DocumentCategory.space_id == document.space_id,
                )
            )
            if category_exists:
                document.category_id = classification_id
                document.updated_at = _now()
        documents.complete_version(
            db, version, chunk_count=chunk_count, summary=None,
            is_current_published=is_current_published,
        )
        write_extracted_sidecar(version.file_path, text, version_id=version.id)
        # 视觉解读保留为有疑点的来源证据，不写入无字段级置信边界的知识图谱。
        if not is_image:
            try:
                _compile_knowledge_graph(db, version, text, {})
            except Exception:
                logger.exception("knowledge compilation failed for version %s", version_id)

        job = db.get(DocumentJob, job_id)
        if job:
            job.status = "completed"
            job.updated_at = _now()

    logger.info(
        "Local pipeline complete: document=%s chunks=%d", document_id, chunk_count
    )
    return True


def _mark_version_failed(
    *,
    version_id: str,
    job_id: str | None,
    failure_code: str,
    failure_message: str,
) -> None:
    with transaction() as db:
        version = db.get(DocumentVersion, version_id)
        if not version or version.processing_stage not in documents.PROCESSING_STAGES:
            return
        document = db.get(Document, version.document_id)
        job = db.get(DocumentJob, job_id) if job_id else None
        if job is None:
            job = documents.get_job_for_version(db, version_id)
        if not document or document.status != "active":
            if job:
                from app.core.security import now as _now

                job.status = "cancelled"
                job.updated_at = _now()
            return
        documents.update_version_stage(
            db,
            version,
            "failed",
            failure_code=failure_code,
            failure_message=failure_message,
        )
        if job:
            from app.core.security import now as _now

            job.status = "failed"
            job.updated_at = _now()


def recover_stuck_processing() -> dict[str, int]:
    """Mark interrupted local processing jobs as retryable failures on startup."""

    stats = {"failed": 0, "resumed": 0, "reconciled": 0, "skipped": 0}
    with transaction() as db:
        in_flight = documents.list_in_flight_versions(db)
        durable_versions = set(
            db.scalars(
                select(DocumentUploadSession.version_id).where(
                    DocumentUploadSession.version_id.is_not(None),
                    DocumentUploadSession.status.in_(
                        ("queued", "verifying", "processing")
                    ),
                )
            )
        )
        plan = [
            (version.id, job.id if job else None)
            for version, job in in_flight
            if version.id not in durable_versions
        ]
        stats["skipped"] = len(in_flight) - len(plan)

    for version_id, job_id in plan:
        _mark_version_failed(
            version_id=version_id,
            job_id=job_id,
            failure_code="process_timeout",
            failure_message="文档处理在服务重启时中断，请重试。",
        )
        stats["failed"] += 1

    if stats["failed"]:
        logger.info("document processing recovery: %s", stats)
    return stats


def _compile_knowledge_graph(
    db: Session,
    version: DocumentVersion,
    extracted_text: str,
    snapshot: dict[str, object],
) -> None:
    """Compile the knowledge graph after local processing completes."""
    if not extracted_text.strip():
        return

    chunk_ids: list[str] = []
    from app.knowledge_engine import KnowledgeCompiler

    compiler = KnowledgeCompiler(db)
    result = compiler.compile(version, extracted_text, chunk_ids)

    if result.errors:
        logger.warning(
            "Knowledge compilation for %s had errors: %s",
            version.id,
            result.errors,
        )
    else:
        logger.info(
            "Knowledge compilation for %s: %d new, %d merged, %d supplemented, %d relations",
            version.id,
            result.new_entities,
            result.merged_entities,
            result.supplemented_entities,
            result.new_relations,
        )
