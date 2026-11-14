from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.database import transaction
from app.models.chat import ChatMessage
from app.models.image_generation import ImageGenerationTask
from app.models.video_generation import VideoGenerationTask
from app.repositories import external_object_cleanup as cleanup_repository
from app.services import minio_storage

logger = logging.getLogger(__name__)

RESOURCE_KIND_MINIO = "minio_object"
RESOURCE_KIND_IMAGE_CACHE = "image_cache"
RESOURCE_KIND_VIDEO_SOURCE = "video_source"
RESOURCE_KIND_VIDEO_OUTPUT = "video_output"
RESOURCE_KIND_CHAT_MEDIA = "chat_media"

LEASE_SECONDS = 300
RETRY_BASE_SECONDS = 2.0
RETRY_MAX_SECONDS = 300.0
WORKER_INTERVAL_SECONDS = 30.0
_MISSING_OBJECT_CODES = frozenset(
    {"NoSuchBucket", "NoSuchKey", "NoSuchObject", "ResourceNotFound"}
)
_ALLOWED_RESOURCE_KINDS = frozenset(
    {RESOURCE_KIND_MINIO, RESOURCE_KIND_IMAGE_CACHE, RESOURCE_KIND_CHAT_MEDIA}
)
_IMAGE_CACHE_OBJECT_PREFIX = "image-generation/results/"
_IMAGE_CACHE_KEY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_DEFERRED_SECONDS = WORKER_INTERVAL_SECONDS
_CHAT_MEDIA_JSON_FIELDS = (
    ChatMessage.images_json,
    ChatMessage.parts_json,
    ChatMessage.references_json,
    ChatMessage.session_references_json,
    ChatMessage.writing_references_json,
    ChatMessage.retrieval_trace_json,
)


class UnsafeExternalObjectUri(ValueError):
    """Raised when a cleanup row does not name an owned generated-image object."""


def is_safe_external_object_uri(uri: str, resource_kind: str) -> bool:
    """Allow only configured-bucket generated-image keys for cleanup."""
    normalized = uri.strip() if isinstance(uri, str) else ""
    kind = resource_kind.strip() if isinstance(resource_kind, str) else ""
    if (
        not normalized
        or normalized != uri
        or kind not in _ALLOWED_RESOURCE_KINDS
        or not minio_storage.is_minio_uri(normalized)
        or any(ord(char) < 32 for char in normalized)
    ):
        return False
    config = minio_storage.load_minio_settings()
    if config is None:
        return False
    try:
        bucket, object_key = minio_storage.parse_minio_uri(normalized)
    except (TypeError, ValueError):
        return False
    if bucket != config.bucket or not object_key.startswith(_IMAGE_CACHE_OBJECT_PREFIX):
        return False
    key_parts = object_key[len(_IMAGE_CACHE_OBJECT_PREFIX) :].split("/")
    return len(key_parts) == 2 and all(
        _IMAGE_CACHE_KEY_PART.fullmatch(part) for part in key_parts
    )


def enqueue(
    db: Session,
    uri: str,
    *,
    resource_kind: str = RESOURCE_KIND_MINIO,
):
    normalized_uri = (uri or "").strip() if isinstance(uri, str) else ""
    normalized_kind = (resource_kind or "").strip()
    if not is_safe_external_object_uri(normalized_uri, normalized_kind):
        return None
    return cleanup_repository.enqueue(
        db, normalized_uri, resource_kind=normalized_kind
    )


def _append_ref(
    refs: list[tuple[str, str]], value: object, resource_kind: str
) -> None:
    normalized = value.strip() if isinstance(value, str) else ""
    if normalized and is_safe_external_object_uri(normalized, resource_kind):
        refs.append((normalized, resource_kind))


def _refs_from_json(value: str | None, resource_kind: str) -> list[tuple[str, str]]:
    if not value:
        return []
    try:
        parsed: Any = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    refs: list[tuple[str, str]] = []

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        else:
            _append_ref(refs, item, resource_kind)

    visit(parsed)
    return refs


def session_media_refs(db: Session, session_id: str) -> list[tuple[str, str]]:
    """Collect durable MinIO refs while the session's rows still exist."""
    refs: list[tuple[str, str]] = []
    for image_url, image_cache_uri in db.execute(
        select(ImageGenerationTask.image_url, ImageGenerationTask.image_cache_uri).where(
            ImageGenerationTask.chat_session_id == session_id
        )
    ).all():
        _append_ref(refs, image_url, RESOURCE_KIND_IMAGE_CACHE)
        _append_ref(refs, image_cache_uri, RESOURCE_KIND_IMAGE_CACHE)
    for video_url, source_file_ref in db.execute(
        select(VideoGenerationTask.video_url, VideoGenerationTask.source_file_ref).where(
            VideoGenerationTask.chat_session_id == session_id
        )
    ).all():
        _append_ref(refs, video_url, RESOURCE_KIND_VIDEO_OUTPUT)
        _append_ref(refs, source_file_ref, RESOURCE_KIND_VIDEO_SOURCE)
    for images_json, parts_json in db.execute(
        select(ChatMessage.images_json, ChatMessage.parts_json).where(
            ChatMessage.session_id == session_id
        )
    ).all():
        refs.extend(_refs_from_json(images_json, RESOURCE_KIND_CHAT_MEDIA))
        refs.extend(_refs_from_json(parts_json, RESOURCE_KIND_CHAT_MEDIA))
    return refs


def user_media_refs(db: Session, user_id: str) -> list[tuple[str, str]]:
    """Collect durable refs owned by a user before user cascades run."""
    refs: list[tuple[str, str]] = []
    for image_url, image_cache_uri in db.execute(
        select(ImageGenerationTask.image_url, ImageGenerationTask.image_cache_uri).where(
            ImageGenerationTask.owner_user_id == user_id
        )
    ).all():
        _append_ref(refs, image_url, RESOURCE_KIND_IMAGE_CACHE)
        _append_ref(refs, image_cache_uri, RESOURCE_KIND_IMAGE_CACHE)
    for video_url, source_file_ref in db.execute(
        select(VideoGenerationTask.video_url, VideoGenerationTask.source_file_ref).where(
            VideoGenerationTask.owner_user_id == user_id
        )
    ).all():
        _append_ref(refs, video_url, RESOURCE_KIND_VIDEO_OUTPUT)
        _append_ref(refs, source_file_ref, RESOURCE_KIND_VIDEO_SOURCE)
    for images_json, parts_json in db.execute(
        select(ChatMessage.images_json, ChatMessage.parts_json).where(
            ChatMessage.owner_user_id == user_id
        )
    ).all():
        refs.extend(_refs_from_json(images_json, RESOURCE_KIND_CHAT_MEDIA))
        refs.extend(_refs_from_json(parts_json, RESOURCE_KIND_CHAT_MEDIA))
    return refs


def enqueue_session_media(db: Session, session_id: str):
    return _enqueue_refs(db, session_media_refs(db, session_id))


def enqueue_user_media(db: Session, user_id: str):
    return _enqueue_refs(db, user_media_refs(db, user_id))


def _enqueue_refs(db: Session, refs: list[tuple[str, str]]):
    valid_refs = [
        (uri.strip(), resource_kind.strip())
        for uri, resource_kind in refs
        if is_safe_external_object_uri(uri, resource_kind)
    ]
    return cleanup_repository.enqueue_many(db, valid_refs)


def has_live_reference(db: Session, uri: str) -> bool:
    """Check every persisted media reference before deleting an object."""
    image_reference = db.scalar(
        select(ImageGenerationTask.id).where(
            or_(
                ImageGenerationTask.image_cache_uri == uri,
                ImageGenerationTask.image_url == uri,
            )
        )
    )
    if image_reference is not None:
        return True

    video_fields = [
        getattr(VideoGenerationTask, field_name, None)
        for field_name in ("video_cache_uri", "cache_uri", "video_url", "source_file_ref")
    ]
    video_fields = [field for field in video_fields if field is not None]
    if video_fields and db.scalar(
        select(VideoGenerationTask.id).where(
            or_(*(field == uri for field in video_fields))
        )
    ) is not None:
        return True

    json_conditions = [field.contains(uri) for field in _CHAT_MEDIA_JSON_FIELDS]
    for values in db.execute(
        select(*_CHAT_MEDIA_JSON_FIELDS).where(or_(*json_conditions))
    ).all():
        for value in values:
            for candidate, kind in _refs_from_json(value, RESOURCE_KIND_CHAT_MEDIA):
                if candidate == uri and is_safe_external_object_uri(candidate, kind):
                    return True
    return False


def _validate_external_object_uri(uri: str, resource_kind: str) -> None:
    if not is_safe_external_object_uri(uri, resource_kind):
        raise UnsafeExternalObjectUri("unsafe external object URI")


def _delete_external_object(uri: str, resource_kind: str) -> None:
    _validate_external_object_uri(uri, resource_kind)
    if resource_kind == RESOURCE_KIND_IMAGE_CACHE:
        from app.services import image_generation

        try:
            if image_generation.delete_cached_image(uri):
                return
        except Exception as exc:
            if getattr(exc, "code", None) in _MISSING_OBJECT_CODES:
                return
            raise
        raise RuntimeError("image cache object cleanup failed")
    try:
        minio_storage.delete_object(minio_uri=uri)
    except Exception as exc:
        if getattr(exc, "code", None) in _MISSING_OBJECT_CODES:
            logger.info(
                "external cleanup object already absent kind=%s",
                resource_kind,
            )
            return
        raise


def _claim_for_worker(
    lease_seconds: int, now_at: datetime | None
) -> tuple[str, str, str, str] | None:
    with transaction() as db:
        item = cleanup_repository.claim_next(
            db, lease_seconds=lease_seconds, now_at=now_at
        )
        if item is None or not item.claim_token:
            return None
        return item.id, item.claim_token, item.uri, item.resource_kind


def _has_live_reference_for_worker(uri: str) -> bool:
    with transaction() as db:
        return has_live_reference(db, uri)


def _mark_succeeded_for_worker(outbox_id: str, claim_token: str) -> bool:
    with transaction() as db:
        return cleanup_repository.mark_succeeded(
            db, outbox_id=outbox_id, claim_token=claim_token
        )


def _mark_retry_for_worker(
    outbox_id: str,
    claim_token: str,
    error: str,
) -> bool:
    with transaction() as db:
        return cleanup_repository.mark_retry(
            db,
            outbox_id=outbox_id,
            claim_token=claim_token,
            error=error,
            retry_base_seconds=RETRY_BASE_SECONDS,
            retry_max_seconds=RETRY_MAX_SECONDS,
        )


def _mark_deferred_for_worker(
    outbox_id: str,
    claim_token: str,
    reason: str,
) -> bool:
    with transaction() as db:
        return cleanup_repository.mark_deferred(
            db,
            outbox_id=outbox_id,
            claim_token=claim_token,
            reason=reason,
            delay_seconds=_DEFERRED_SECONDS,
        )


async def process_pending_once(
    *,
    lease_seconds: int = LEASE_SECONDS,
    now_at: datetime | None = None,
    run_sync: Callable[..., Awaitable[Any]] | None = None,
) -> bool:
    """Claim and process one tombstone without blocking the event loop."""
    run_sync = run_sync or asyncio.to_thread
    claimed = await run_sync(_claim_for_worker, lease_seconds, now_at)
    if claimed is None:
        return False
    outbox_id, claim_token, uri, resource_kind = claimed

    try:
        # Keep validation here as well as at enqueue time: old rows may have
        # been inserted before the allowlist existed or by a direct SQL path.
        await run_sync(_validate_external_object_uri, uri, resource_kind)
        if await run_sync(_has_live_reference_for_worker, uri):
            await run_sync(
                _mark_deferred_for_worker,
                outbox_id,
                claim_token,
                "external object still referenced",
            )
            return True
        await run_sync(_delete_external_object, uri, resource_kind)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "external object cleanup failed kind=%s error=%s",
            resource_kind,
            type(exc).__name__,
        )
        await run_sync(
            _mark_retry_for_worker,
            outbox_id,
            claim_token,
            str(exc) or type(exc).__name__,
        )
    else:
        await run_sync(_mark_succeeded_for_worker, outbox_id, claim_token)
    return True
