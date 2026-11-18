from __future__ import annotations

import asyncio
import logging
import json
import re
import time
from datetime import timedelta
from collections.abc import Callable, Mapping
from typing import Any, Literal, TypeVar, TypedDict, cast

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import new_token, now
from app.models.auth import User
from app.models.chat import ChatDispatchDecision, ChatMessage, ChatSession
from app.models.image_generation import ImageGenerationTask
from app.models.video_generation import VideoGenerationTask
from app.repositories import chat as chat_repo
from app.repositories import chat_dispatch
from app.services import image_generation, video_generation
from app.services.generation_prompt import build_contextual_prompt

logger = logging.getLogger(__name__)

MediaIntent = Literal["image_generate", "image_edit", "video_generate"]
MediaTaskKind = Literal["image", "video"]
TaskT = TypeVar("TaskT", ImageGenerationTask, VideoGenerationTask)
DbResultT = TypeVar("DbResultT")


class MediaTaskDispatchResult(TypedDict):
    task_kind: MediaTaskKind
    task: dict[str, Any]
    decision_id: str
    created: bool


class MediaDispatchError(RuntimeError):
    """A provider error that has already been durably reflected locally."""

    def __init__(self, message: str, *, task: dict[str, Any], unknown: bool = False):
        super().__init__(message)
        self.task = task
        self.unknown = unknown


_UNKNOWN_ERROR = "供应商结果未知，请勿重复提交。"
_IMAGE_FAILED_ERROR = "图片生成失败，请重试。"
_VIDEO_FAILED_ERROR = "视频生成失败，请重试。"
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled", "outcome_unknown"})
_HTTP_REJECTION = re.compile(r"HTTP\s+4(?:0[0-9]|1[0-9]|2[0-9]|3[0-9]|4[0-9]|5[0-9])")


def _mapping(value: object) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    return None


def _value(params: Mapping[str, Any], key: str, default: Any = None) -> Any:
    value = params.get(key, default)
    return default if value is None else value


def _task_kind(intent: str) -> MediaTaskKind:
    if intent in ("image_generate", "image_edit"):
        return "image"
    if intent == "video_generate":
        return "video"
    raise ValueError("media dispatch only accepts an image or video intent")


def _operation_for_intent(
    intent: str, params: Mapping[str, Any]
) -> image_generation.ImageOperation:
    if intent == "image_edit":
        return "edit"
    operation = str(params.get("operation") or "generate").strip()
    if operation not in ("generate", "edit"):
        raise ValueError("不支持的图片任务类型")
    if operation == "edit":
        return "edit"
    return "generate"


def _normalize_source(source_image: object) -> dict[str, Any] | None:
    source = _mapping(source_image)
    if not source:
        return None
    normalized: dict[str, Any] = {
        "type": str(source.get("type") or "image"),
        "data": str(source.get("data") or ""),
        "mimeType": str(source.get("mimeType") or source.get("mime_type") or ""),
    }
    return normalized if normalized["data"] and normalized["mimeType"] else None


def _source_image_for_task(
    db: Session, task: ImageGenerationTask
) -> dict[str, Any] | None:
    if (getattr(task, "operation", "") or "generate") != "edit":
        return None
    message = db.scalar(
        select(ChatMessage).where(
            ChatMessage.id == task.user_message_id,
            ChatMessage.session_id == task.chat_session_id,
            ChatMessage.owner_user_id == task.owner_user_id,
        )
    )
    if message is None:
        return None
    images = chat_repo.message_to_dict(message).get("images")
    if not isinstance(images, list) or len(images) != 1:
        return None
    return _normalize_source(images[0])


def _assistant_content(
    kind: MediaTaskKind,
    status: str,
    *,
    operation: str | None = None,
    retried: bool = False,
) -> str:
    if kind == "image":
        label = "图片编辑" if operation == "edit" else "图片生成"
        if status == "succeeded":
            return f"{label}任务已完成。"
        if status == "failed":
            return f"{label}任务失败，请重试。"
        if status == "outcome_unknown":
            return f"{label}结果未知，请勿重复提交。"
        action = f"{label}任务已重新提交" if retried else f"{label}任务已提交"
        from app.services.pi_runtime.persona import load_pi_persona

        return f"{action}，{load_pi_persona().name}正在处理中。"
    if status == "succeeded":
        return "视频生成任务已完成。"
    if status == "failed":
        return "视频生成任务失败，请重试。"
    if status == "outcome_unknown":
        return "视频生成结果未知，请勿重复提交。"
    action = "视频生成任务已重新提交" if retried else "视频生成任务已提交"
    from app.services.pi_runtime.persona import load_pi_persona

    return f"{action}，{load_pi_persona().name}正在处理中。"


def _sync_assistant_message(
    db: Session,
    *,
    assistant_message_id: str,
    session_id: str,
    owner_user_id: str,
    kind: MediaTaskKind,
    status: str,
    operation: str | None = None,
    retried: bool = False,
) -> None:
    message = db.scalar(
        select(ChatMessage).where(
            ChatMessage.id == assistant_message_id,
            ChatMessage.session_id == session_id,
            ChatMessage.owner_user_id == owner_user_id,
        )
    )
    if message is not None:
        content = _assistant_content(
            kind,
            status,
            operation=operation,
            retried=retried,
        )
        if message.content != content:
            message.content = content
            chat_repo.advance_messages_revision_for_session(db, message.session_id)


def _is_unknown_provider_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            image_generation.ImageGenerationInputError,
            video_generation.VideoGenerationInputError,
        ),
    ):
        return False
    if isinstance(
        exc,
        (
            image_generation.ImageGenerationNotConfiguredError,
            video_generation.VideoGenerationNotConfiguredError,
        ),
    ):
        return False
    outcome_unknown = getattr(exc, "outcome_unknown", None)
    if outcome_unknown is not None:
        return bool(outcome_unknown)
    # Provider services expose all transport/decoding failures through their
    # generic ProviderError. A 4xx response is a definite rejection; all other
    # submission failures may have been accepted upstream.
    return _HTTP_REJECTION.search(str(exc)) is None


def _provider_error_message(kind: MediaTaskKind, *, unknown: bool) -> str:
    if unknown:
        return _UNKNOWN_ERROR
    return _IMAGE_FAILED_ERROR if kind == "image" else _VIDEO_FAILED_ERROR


def _image_status(result: image_generation.ProviderTask) -> tuple[str, str | None]:
    status = str(result.status)
    if status == "unknown":
        return "outcome_unknown", _UNKNOWN_ERROR
    if status == "succeeded" and not result.image_url:
        return "outcome_unknown", _UNKNOWN_ERROR
    if status == "failed":
        return "failed", _IMAGE_FAILED_ERROR
    return status, None


def _video_status(result: video_generation.ProviderTask) -> tuple[str, str | None]:
    status = str(result.status)
    if status == "unknown":
        return "outcome_unknown", _UNKNOWN_ERROR
    if status == "succeeded" and not result.video_url:
        return "outcome_unknown", _UNKNOWN_ERROR
    if status == "failed":
        return "failed", _VIDEO_FAILED_ERROR
    return status, None


async def _mirror_succeeded_image(
    task: ImageGenerationTask,
    *,
    provider_image_url: str,
) -> None:
    """Persist a succeeded provider image into the durable object cache.

    Runs after the succeeded row is committed; a failure to mirror only logs
    (the raw provider URL stays valid on the task) and never fails the task.
    """
    if not provider_image_url:
        return
    if not await asyncio.to_thread(image_generation.image_cache_is_available):
        return
    mirrored = await image_generation.mirror_provider_image(
        owner_user_id=task.owner_user_id,
        task_id=task.id,
        image_url=provider_image_url,
    )
    if mirrored is None:
        return

    def persist_cache_uri(update_db: Session) -> bool:
        persisted = update_db.get(ImageGenerationTask, task.id)
        if persisted is None or persisted.status != "succeeded":
            return False
        if persisted.image_cache_uri == mirrored:
            return True
        persisted.image_cache_uri = mirrored
        persisted.updated_at = now()
        return True

    def enqueue_mirrored_uri_for_cleanup() -> None:
        from app.core.database import transaction
        from app.services import external_object_cleanup

        with transaction() as cleanup_db:
            external_object_cleanup.enqueue(
                cleanup_db,
                mirrored,
                resource_kind=external_object_cleanup.RESOURCE_KIND_IMAGE_CACHE,
            )

    try:
        persisted = run_db_transaction_with_retry(persist_cache_uri, attempts=1)
        if not persisted:
            await asyncio.to_thread(enqueue_mirrored_uri_for_cleanup)
    except Exception:
        # The upload is already durable.  If the task disappeared or the
        # metadata transaction failed, retain a cleanup tombstone instead of
        # leaking the object between the two writes.
        try:
            await asyncio.to_thread(enqueue_mirrored_uri_for_cleanup)
        except Exception:
            logger.warning(
                "media.dispatch.image_cache_cleanup_enqueue_failed",
                extra={"task_id": task.id},
                exc_info=True,
            )
        logger.warning(
            "media.dispatch.image_cache_metadata_persist_failed",
            extra={"task_id": task.id},
            exc_info=True,
        )


def _image_operation(task: ImageGenerationTask) -> image_generation.ImageOperation:
    operation = (task.operation or "generate").strip()
    if operation not in ("generate", "edit"):
        raise RuntimeError("本地图片任务类型无效")
    return cast(image_generation.ImageOperation, operation)


async def _submit_image_task(
    *,
    task: ImageGenerationTask,
    session: ChatSession,
    prompt: str,
    source: Mapping[str, Any] | None,
) -> image_generation.ProviderTask:
    operation = _image_operation(task)
    provider_prompt = prompt
    if operation == "generate":
        provider_prompt = build_contextual_prompt(
            chat_repo.active_messages(session.messages),
            prompt,
            generation_label="图片",
            max_length=image_generation.IMAGE_PROMPT_MAX_LENGTH,
            excluded_message_ids=frozenset(
                {task.user_message_id, task.assistant_message_id}
            ),
        )
    return await image_generation.create_provider_task(
        prompt=provider_prompt,
        resolution=task.resolution,
        operation=operation,
        model=task.model,
        source_image=source,
    )


async def _submit_video_task(
    *,
    task: VideoGenerationTask,
    session: ChatSession,
    prompt: str,
    source: Mapping[str, Any] | None,
) -> video_generation.ProviderTask:
    provider_prompt = build_contextual_prompt(
        chat_repo.active_messages(session.messages),
        prompt,
        generation_label="视频",
        max_length=video_generation.VIDEO_PROMPT_MAX_LENGTH,
        excluded_message_ids=frozenset(
            {task.user_message_id, task.assistant_message_id}
        ),
    )
    if source is None:
        return await video_generation.create_provider_task(
            prompt=provider_prompt,
            mode=task.mode,
            duration=task.duration,
        )
    return await video_generation.create_provider_task(
        prompt=provider_prompt,
        mode=task.mode,
        duration=task.duration,
        first_frame=source,
    )


def run_db_transaction_with_retry(
    operation: Callable[[Session], DbResultT],
    *,
    session_factory: Callable[[], Session] | None = None,
    attempts: int = 3,
) -> DbResultT:
    """Commit one idempotent database operation with bounded retries."""
    if attempts < 1:
        raise ValueError("数据库重试次数必须为正数")
    factory = session_factory or SessionLocal
    last_error: Exception | None = None
    for attempt in range(attempts):
        update_db = factory()
        try:
            result = operation(update_db)
            update_db.commit()
            return result
        except Exception as exc:
            update_db.rollback()
            last_error = exc
            if attempt == attempts - 1:
                raise
        finally:
            update_db.close()
    if last_error is None:
        raise RuntimeError("数据库状态更新失败")
    raise last_error


def _run_task_update_with_retry(
    model: type[TaskT],
    task_id: str,
    update: Callable[[Session, TaskT], str],
) -> str:
    """Reload and commit one provider result a bounded number of times."""

    def apply(update_db: Session) -> str:
        task = update_db.get(model, task_id, with_for_update=True)
        if task is None:
            raise RuntimeError("本地媒体任务已丢失")
        return update(update_db, task)

    return run_db_transaction_with_retry(apply)


def _set_decision_after_provider_result(
    db: Session,
    decision_id: str | None,
    *,
    status: str,
    provider_task_id: str | None,
) -> ChatDispatchDecision | None:
    if not decision_id:
        return None
    decision = db.get(ChatDispatchDecision, decision_id)
    if decision is None:
        return None
    decision.status = (
        "outcome_unknown"
        if status == "outcome_unknown"
        else "failed"
        if status == "failed"
        else "dispatched"
    )
    decision.updated_at = now()
    if provider_task_id:
        decision.dispatched_at = now()
    return decision


def _persist_image_provider_result_with_retry(
    *,
    task_id: str,
    decision_id: str | None,
    result: image_generation.ProviderTask,
    retried: bool = False,
) -> str:
    def update(db: Session, task: ImageGenerationTask) -> str:
        if task.status in _TERMINAL_STATUSES:
            return task.status
        local_status, error_message = _image_status(result)
        task.provider_task_id = result.provider_task_id or None
        task.status = local_status
        task.error_message = error_message
        task.image_url = result.image_url
        task.updated_at = now()
        task.completed_at = (
            now() if local_status in ("succeeded", "failed", "canceled") else None
        )
        _set_decision_after_provider_result(
            db,
            decision_id,
            status=local_status,
            provider_task_id=task.provider_task_id,
        )
        _sync_assistant_message(
            db,
            assistant_message_id=task.assistant_message_id,
            session_id=task.chat_session_id,
            owner_user_id=task.owner_user_id,
            kind="image",
            status=local_status,
            operation=task.operation,
            retried=retried,
        )
        return local_status

    return _run_task_update_with_retry(ImageGenerationTask, task_id, update)


def _persist_video_provider_result_with_retry(
    *,
    task_id: str,
    decision_id: str | None,
    result: video_generation.ProviderTask,
    retried: bool = False,
) -> str:
    def update(db: Session, task: VideoGenerationTask) -> str:
        if task.status in _TERMINAL_STATUSES:
            return task.status
        local_status, error_message = _video_status(result)
        task.provider_task_id = result.provider_task_id or None
        task.status = local_status
        task.error_message = error_message
        task.video_url = result.video_url
        if result.source_file_ref:
            task.source_file_ref = result.source_file_ref
        task.updated_at = now()
        task.completed_at = (
            now() if local_status in ("succeeded", "failed", "canceled") else None
        )
        _set_decision_after_provider_result(
            db,
            decision_id,
            status=local_status,
            provider_task_id=task.provider_task_id,
        )
        _sync_assistant_message(
            db,
            assistant_message_id=task.assistant_message_id,
            session_id=task.chat_session_id,
            owner_user_id=task.owner_user_id,
            kind="video",
            status=local_status,
            retried=retried,
        )
        return local_status

    return _run_task_update_with_retry(VideoGenerationTask, task_id, update)


def _persist_submission_failure_with_retry(
    *,
    task_kind: MediaTaskKind,
    task_id: str,
    decision_id: str | None,
    exc: Exception,
    retried: bool = False,
) -> None:
    unknown = _is_unknown_provider_error(exc)
    status = "outcome_unknown" if unknown else "failed"
    error_message = _provider_error_message(task_kind, unknown=unknown)
    source_file_ref = getattr(exc, "source_file_ref", None)
    if task_kind == "image":
        def image_update(db: Session, task: ImageGenerationTask) -> str:
            if task.status in _TERMINAL_STATUSES:
                return task.status
            task.status = status
            task.error_message = error_message
            task.updated_at = now()
            task.completed_at = None
            _set_decision_after_provider_result(
                db,
                decision_id,
                status=status,
                provider_task_id=task.provider_task_id,
            )
            _sync_assistant_message(
                db,
                assistant_message_id=task.assistant_message_id,
                session_id=task.chat_session_id,
                owner_user_id=task.owner_user_id,
                kind="image",
                status=status,
                operation=task.operation,
                retried=retried,
            )
            return status

        _run_task_update_with_retry(ImageGenerationTask, task_id, image_update)
        return

    def video_update(db: Session, task: VideoGenerationTask) -> str:
        if task.status in _TERMINAL_STATUSES:
            return task.status
        if source_file_ref:
            task.source_file_ref = str(source_file_ref)
        task.status = status
        task.error_message = error_message
        task.updated_at = now()
        task.completed_at = None
        _set_decision_after_provider_result(
            db,
            decision_id,
            status=status,
            provider_task_id=task.provider_task_id,
        )
        _sync_assistant_message(
            db,
            assistant_message_id=task.assistant_message_id,
            session_id=task.chat_session_id,
            owner_user_id=task.owner_user_id,
            kind="video",
            status=status,
            retried=retried,
        )
        return status

    _run_task_update_with_retry(VideoGenerationTask, task_id, video_update)


def reconcile_stale_submission_for_read(
    db: Session,
    *,
    task_kind: MediaTaskKind,
    task_id: str,
    owner_user_id: str,
    lease_seconds: int | None = None,
) -> bool:
    """Atomically finish an expired submission that has no provider ID."""
    resolved_lease = lease_seconds or settings.media_dispatch_lease_seconds
    cutoff = now() - timedelta(seconds=max(30, resolved_lease))
    if task_kind == "image":
        task = db.scalar(
            select(ImageGenerationTask)
            .where(
                ImageGenerationTask.id == task_id,
                ImageGenerationTask.owner_user_id == owner_user_id,
                ImageGenerationTask.status == "submitting",
                ImageGenerationTask.provider_task_id.is_(None),
                ImageGenerationTask.updated_at < cutoff,
            )
            .with_for_update()
        )
        if task is None:
            return False
        task.status = "outcome_unknown"
        task.error_message = _UNKNOWN_ERROR
        task.updated_at = now()
        task.completed_at = None
        _set_decision_after_provider_result(
            db,
            task.dispatch_id,
            status="outcome_unknown",
            provider_task_id=None,
        )
        _sync_assistant_message(
            db,
            assistant_message_id=task.assistant_message_id,
            session_id=task.chat_session_id,
            owner_user_id=task.owner_user_id,
            kind="image",
            status="outcome_unknown",
            operation=task.operation,
        )
    else:
        task = db.scalar(
            select(VideoGenerationTask)
            .where(
                VideoGenerationTask.id == task_id,
                VideoGenerationTask.owner_user_id == owner_user_id,
                VideoGenerationTask.status == "submitting",
                VideoGenerationTask.provider_task_id.is_(None),
                VideoGenerationTask.updated_at < cutoff,
            )
            .with_for_update()
        )
        if task is None:
            return False
        task.status = "outcome_unknown"
        task.error_message = _UNKNOWN_ERROR
        task.updated_at = now()
        task.completed_at = None
        _set_decision_after_provider_result(
            db,
            task.dispatch_id,
            status="outcome_unknown",
            provider_task_id=None,
        )
        _sync_assistant_message(
            db,
            assistant_message_id=task.assistant_message_id,
            session_id=task.chat_session_id,
            owner_user_id=task.owner_user_id,
            kind="video",
            status="outcome_unknown",
        )
    db.commit()
    return True


def _mark_stale_decision_if_dispatching(
    db: Session,
    decision_id: str,
    cutoff,
    stamp,
) -> bool:
    result = db.execute(
        update(ChatDispatchDecision)
        .where(
            ChatDispatchDecision.id == decision_id,
            ChatDispatchDecision.status == "dispatching",
            ChatDispatchDecision.updated_at < cutoff,
        )
        .values(status="outcome_unknown", updated_at=stamp)
    )
    return getattr(result, "rowcount", 0) == 1


def _decision_has_live_task(db: Session, decision_id: str) -> bool:
    active_image = db.scalar(
        select(ImageGenerationTask.id).where(
            ImageGenerationTask.dispatch_id == decision_id,
            or_(
                ImageGenerationTask.provider_task_id.is_not(None),
                ImageGenerationTask.status != "submitting",
            ),
        )
    )
    if active_image is not None:
        return True
    active_video = db.scalar(
        select(VideoGenerationTask.id).where(
            VideoGenerationTask.dispatch_id == decision_id,
            or_(
                VideoGenerationTask.provider_task_id.is_not(None),
                VideoGenerationTask.status != "submitting",
            ),
        )
    )
    return active_video is not None


async def resolve_succeeded_image_edit_source(
    task: ImageGenerationTask,
    *,
    image_url: str | None = None,
    image_cache_uri: str | None = None,
) -> dict[str, str] | None:
    """Resolve a succeeded task image from durable cache before its provider URL."""
    if task.status != "succeeded":
        return None
    provider_url = image_url if image_url is not None else task.image_url
    cache_uri = image_cache_uri if image_cache_uri is not None else task.image_cache_uri
    if cache_uri:
        cached = await image_generation.read_cached_image_bytes_async(cache_uri)
        if cached:
            encoded = await asyncio.to_thread(
                image_generation.bound_edit_source_to_schema,
                cached,
            )
            if encoded is not None:
                return encoded
    if not provider_url:
        return None
    if not cache_uri:
        await _mirror_succeeded_image(task, provider_image_url=provider_url)
        with SessionLocal() as refresh_db:
            refreshed = refresh_db.get(ImageGenerationTask, task.id)
            if (
                refreshed is not None
                and refreshed.owner_user_id == task.owner_user_id
                and refreshed.status == "succeeded"
                and refreshed.image_cache_uri
            ):
                cached = await image_generation.read_cached_image_bytes_async(
                    refreshed.image_cache_uri
                )
                if cached:
                    encoded = await asyncio.to_thread(
                        image_generation.bound_edit_source_to_schema,
                        cached,
                    )
                    if encoded is not None:
                        return encoded
    fetched = await image_generation.fetch_edit_source_image(provider_url)
    if fetched is None:
        return None
    return await asyncio.to_thread(image_generation.rebound_to_schema, fetched)


def image_task_to_dict(db: Session, task: ImageGenerationTask) -> dict[str, Any]:
    operation = (getattr(task, "operation", "") or "generate").strip()
    retryable = task.status == "failed" and (
        operation == "generate" or _source_image_for_task(db, task) is not None
    )
    display_url: str | None = None
    if task.status == "succeeded" and task.image_cache_uri:
        display_url = f"/api/image-generation/tasks/{task.id}/image"
    return {
        "id": task.id,
        "session_id": task.chat_session_id,
        "user_message_id": task.user_message_id,
        "assistant_message_id": task.assistant_message_id,
        "operation": operation,
        "model": task.model,
        "aspect_ratio": task.aspect_ratio,
        "resolution": task.resolution,
        "prompt": task.prompt,
        "status": task.status,
        "retryable": retryable,
        "dispatch_id": task.dispatch_id,
        "image_url": display_url if display_url is not None else task.image_url,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def video_task_to_dict(task: VideoGenerationTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "session_id": task.chat_session_id,
        "user_message_id": task.user_message_id,
        "assistant_message_id": task.assistant_message_id,
        "model": task.model,
        "mode": task.mode,
        "duration": task.duration,
        "prompt": task.prompt,
        "status": task.status,
        "retryable": task.status == "failed",
        "dispatch_id": task.dispatch_id,
        "video_url": task.video_url,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def _existing_task(
    db: Session, decision: ChatDispatchDecision, kind: MediaTaskKind
) -> ImageGenerationTask | VideoGenerationTask | None:
    if decision.target_id:
        model = ImageGenerationTask if kind == "image" else VideoGenerationTask
        task = db.scalar(
            select(model).where(
                model.id == decision.target_id,
                model.owner_user_id == decision.owner_user_id,
                model.dispatch_id == decision.id,
            )
        )
        if task is not None:
            return task
    model = ImageGenerationTask if kind == "image" else VideoGenerationTask
    return db.scalar(
        select(model)
        .where(
            model.owner_user_id == decision.owner_user_id,
            model.dispatch_id == decision.id,
        )
        .order_by(model.created_at.asc())
    )


def _result_for_task(
    db: Session,
    task: ImageGenerationTask | VideoGenerationTask,
    decision: ChatDispatchDecision,
    *,
    created: bool,
) -> MediaTaskDispatchResult:
    if isinstance(task, ImageGenerationTask):
        return {
            "task_kind": "image",
            "task": image_task_to_dict(db, task),
            "decision_id": decision.id,
            "created": created,
        }
    return {
        "task_kind": "video",
        "task": video_task_to_dict(task),
        "decision_id": decision.id,
        "created": created,
    }


def get_media_task_result(
    db: Session,
    *,
    decision_id: str,
    owner_user_id: str,
) -> MediaTaskDispatchResult | None:
    """Return the owner-scoped durable task for an existing dispatch decision."""
    decision = chat_dispatch.get_dispatch_decision_by_id(
        db,
        decision_id=decision_id,
        owner_user_id=owner_user_id,
    )
    if decision is None:
        return None
    kinds: tuple[MediaTaskKind, ...]
    if decision.target_type == "image_generation_task":
        kinds = ("image",)
    elif decision.target_type == "video_generation_task":
        kinds = ("video",)
    else:
        kinds = ("image", "video")
    for kind in kinds:
        task = _existing_task(db, decision, kind)
        if task is not None:
            return _result_for_task(db, task, decision, created=False)
    return None


def _validate_decision_binding(
    decision: ChatDispatchDecision,
    *,
    owner_user_id: str,
    session_id: str,
    client_message_id: str,
    intent: str,
    effective_route: str,
    prompt_digest: str,
    attachment_digest: str,
) -> None:
    if decision.owner_user_id != owner_user_id:
        raise chat_dispatch.DispatchNotFoundError("路由决策不存在")
    if decision.client_message_id != client_message_id:
        raise chat_dispatch.DispatchConflictError("路由决策与幂等键不匹配")
    if decision.chat_session_id != session_id:
        raise chat_dispatch.DispatchConflictError("client_message_id 已绑定到其他会话")
    if decision.predicted_intent != intent:
        raise chat_dispatch.DispatchConflictError("client_message_id 已绑定到其他意图")
    if decision.effective_route != effective_route:
        raise chat_dispatch.DispatchConflictError(
            "client_message_id 已绑定到其他执行路径"
        )
    if (decision.prompt_digest or "") != prompt_digest:
        raise chat_dispatch.DispatchConflictError(
            "client_message_id 已绑定到其他提示词"
        )
    if (decision.attachment_digest or "") != attachment_digest:
        raise chat_dispatch.DispatchConflictError("client_message_id 已绑定到其他附件")


def _decision_parameters(
    *,
    kind: MediaTaskKind,
    intent: str,
    params: Mapping[str, Any],
    source_image: dict[str, Any] | None,
) -> dict[str, Any]:
    if kind == "image":
        return {
            "resolution": str(
                _value(
                    params,
                    "resolution",
                    "2k" if intent == "image_edit" else "1k",
                )
            ),
            "source_kind": "explicit_upload" if source_image else "none",
            "source_image_index": 0 if source_image else None,
        }
    return {
        "video_mode": str(_value(params, "mode", "std")),
        "video_duration": int(_value(params, "duration", 5)),
        "source_kind": "explicit_upload" if source_image else "none",
        "source_image_index": 0 if source_image else None,
    }


async def _create_local_task(
    db: Session,
    *,
    decision: ChatDispatchDecision,
    session: ChatSession,
    user: User,
    client_message_id: str,
    assistant_message_id: str,
    intent: str,
    prompt: str,
    params: Mapping[str, Any],
    source_image: dict[str, Any] | None,
) -> ImageGenerationTask | VideoGenerationTask:
    kind = _task_kind(intent)
    user_message_id = client_message_id.strip()
    if not user_message_id:
        raise ValueError("client_message_id is required")
    if len(user_message_id) > 64:
        raise ValueError("client_message_id cannot exceed 64 characters")
    assistant_message_id = assistant_message_id.strip()
    if not assistant_message_id:
        raise ValueError("assistant_message_id is required")
    operation = _operation_for_intent(intent, params) if kind == "image" else None
    mode = str(_value(params, "mode", "std")) if kind == "video" else None
    duration = int(_value(params, "duration", 5)) if kind == "video" else None
    resolution = (
        str(_value(params, "resolution", "2k" if operation == "edit" else "1k"))
        if kind == "image"
        else None
    )
    image_model = (
        (
            settings.dashscope_image_edit_model
            if operation == "edit"
            else settings.dashscope_image_model
        ).strip()
        if kind == "image"
        else None
    )
    aspect_ratio = (
        await asyncio.to_thread(
            image_generation.edit_output_aspect_ratio,
            cast(str, image_model),
            source_image or {},
        )
        if operation == "edit"
        else "1:1"
    )
    existing_user = db.get(ChatMessage, user_message_id)
    existing_assistant = db.get(ChatMessage, assistant_message_id)
    for existing, expected_role in (
        (existing_user, "user"),
        (existing_assistant, "assistant"),
    ):
        if existing is None:
            continue
        if (
            existing.owner_user_id != user.id
            or existing.session_id != session.id
            or existing.role != expected_role
        ):
            raise ValueError("消息幂等键已绑定到其他会话或角色")

    missing_payloads: list[dict[str, Any]] = []
    if existing_user is None:
        missing_payloads.append(
            {
                "id": user_message_id,
                "role": "user",
                "content": prompt,
                "images": [source_image] if source_image else [],
            }
        )
    if existing_assistant is None:
        missing_payloads.append(
            {
                "id": assistant_message_id,
                "role": "assistant",
                "content": _assistant_content(
                    kind,
                    "submitting",
                    operation=operation,
                ),
            }
        )
    created = chat_repo.append_messages(db, session, missing_payloads)
    created_by_role = {message.role: message for message in created}
    user_message = existing_user or created_by_role["user"]
    assistant_message = existing_assistant or created_by_role["assistant"]
    updated_existing_message = False
    if existing_user is not None and user_message.content != prompt:
        updated_existing_message = True
    user_message.content = prompt
    images_json = json.dumps(
        [source_image] if source_image else [],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if existing_user is not None and user_message.images_json != images_json:
        updated_existing_message = True
    user_message.images_json = images_json
    assistant_content = _assistant_content(
        kind,
        "submitting",
        operation=operation,
    )
    if (
        existing_assistant is not None
        and assistant_message.content != assistant_content
    ):
        updated_existing_message = True
    assistant_message.content = assistant_content
    if existing_user is not None and user_message.dispatch_id != decision.id:
        updated_existing_message = True
    if (
        existing_assistant is not None
        and assistant_message.dispatch_id != decision.id
    ):
        updated_existing_message = True
    user_message.dispatch_id = decision.id
    assistant_message.dispatch_id = decision.id
    actual_user_message_id = user_message.id
    actual_assistant_message_id = assistant_message.id
    session.updated_at = now()
    if not created and updated_existing_message:
        chat_repo.advance_messages_revision_for_session(db, session.id)
    stamp = now()
    if kind == "image":
        task: ImageGenerationTask | VideoGenerationTask = ImageGenerationTask(
            id=f"igt_{new_token(10)}",
            owner_user_id=user.id,
            chat_session_id=session.id,
            user_message_id=actual_user_message_id,
            assistant_message_id=actual_assistant_message_id,
            dispatch_id=decision.id,
            provider_task_id=None,
            operation=cast(str, operation),
            model=cast(str, image_model),
            aspect_ratio=aspect_ratio,
            resolution=cast(str, resolution),
            prompt=prompt,
            status="submitting",
            image_url=None,
            error_message=None,
            created_at=stamp,
            updated_at=stamp,
            completed_at=None,
        )
    else:
        task = VideoGenerationTask(
            id=f"vgt_{new_token(10)}",
            owner_user_id=user.id,
            chat_session_id=session.id,
            user_message_id=actual_user_message_id,
            assistant_message_id=actual_assistant_message_id,
            dispatch_id=decision.id,
            provider_task_id=None,
            source_file_ref=None,
            model=(
                settings.dashscope_video_image_model
                if source_image
                else settings.dashscope_video_model
            ),
            mode=cast(str, mode),
            duration=cast(int, duration),
            prompt=prompt,
            status="submitting",
            video_url=None,
            error_message=None,
            created_at=stamp,
            updated_at=stamp,
            completed_at=None,
        )
    db.add(task)
    db.flush()
    decision.target_type = (
        "image_generation_task" if kind == "image" else "video_generation_task"
    )
    decision.target_id = task.id
    decision.status = "dispatching"
    decision.updated_at = stamp
    return task


async def dispatch_media_task(
    db: Session,
    *,
    session: ChatSession,
    user: User,
    client_message_id: str,
    assistant_message_id: str,
    intent: str,
    prompt: str,
    media_params: Mapping[str, Any] | None,
    source_image: Mapping[str, Any] | None,
    decision_id: str | None = None,
) -> MediaTaskDispatchResult:
    """Persist a submitting task before making exactly one provider submission."""
    params = dict(media_params or {})
    normalized_prompt = (prompt or "").strip()
    if not normalized_prompt:
        raise ValueError("提示词不能为空")
    normalized_client_message_id = (client_message_id or "").strip()
    if not normalized_client_message_id:
        raise ValueError("client_message_id is required")
    if len(normalized_client_message_id) > 64:
        raise ValueError("client_message_id cannot exceed 64 characters")
    client_message_id = normalized_client_message_id
    if intent not in ("image_generate", "image_edit", "video_generate"):
        raise ValueError("不支持的媒体意图")
    if session.owner_user_id != user.id:
        raise PermissionError("会话不存在")
    kind = _task_kind(intent)
    source = _normalize_source(source_image)
    if intent == "image_edit" and source is None:
        raise ValueError("图片编辑任务必须包含一张待编辑图片")
    if intent == "image_generate" and source is not None:
        raise ValueError("文生图任务不能包含待编辑图片")
    prompt_hash = chat_dispatch.prompt_digest(normalized_prompt)
    attachment_hash = chat_dispatch.attachment_digest(source)
    target_type = (
        "image_generation_task" if kind == "image" else "video_generation_task"
    )
    effective_route = intent

    if decision_id:
        decision = chat_dispatch.get_dispatch_decision_by_id(
            db,
            decision_id=decision_id,
            owner_user_id=user.id,
            for_update=True,
        )
        if decision is None:
            raise chat_dispatch.DispatchNotFoundError("路由决策不存在")
        _validate_decision_binding(
            decision,
            owner_user_id=user.id,
            session_id=session.id,
            client_message_id=client_message_id,
            intent=intent,
            effective_route=effective_route,
            prompt_digest=prompt_hash,
            attachment_digest=attachment_hash,
        )
        created_decision = False
    else:
        decision, created_decision = chat_dispatch.get_or_create_dispatch_decision(
            db,
            owner_user_id=user.id,
            chat_session_id=session.id,
            client_message_id=client_message_id,
            predicted_intent=intent,
            effective_route=effective_route,
            final_intent=intent,
            route_source=str(params.get("route_source") or "explicit_ui"),
            confidence_band=str(params.get("confidence_band") or "high"),
            reason_code=str(params.get("reason_code") or "media_dispatch"),
            classifier_model=(
                str(params["classifier_model"])
                if params.get("classifier_model")
                else None
            ),
            classifier_version=(
                str(params["classifier_version"])
                if params.get("classifier_version")
                else None
            ),
            decision_json={
                "parameters": _decision_parameters(
                    kind=kind,
                    intent=intent,
                    params=params,
                    source_image=source,
                ),
                "requires_confirmation": bool(
                    params.get("requires_confirmation", False)
                ),
            },
            classifier_usage_json=params.get("classifier_usage"),
            status="dispatching",
            target_type=target_type,
            prompt_digest=prompt_hash,
            attachment_digest=attachment_hash,
        )

    existing = _existing_task(db, decision, kind)
    if existing is not None:
        return _result_for_task(db, existing, decision, created=False)
    if not created_decision and decision.status in (
        "dispatched",
        "failed",
        "outcome_unknown",
        "superseded",
    ):
        raise MediaDispatchError(
            "该请求已经提交，不能重复提交",
            task={"decision_id": decision.id, "status": decision.status},
            unknown=decision.status == "outcome_unknown",
        )
    if not created_decision and decision.status == "dispatching":
        # A committed dispatching decision without a local task means the
        # process died before its transaction completed. Never guess that it
        # is safe to submit again; reconciliation must mark it unknown.
        raise MediaDispatchError(
            "该请求正在提交或结果未知，请勿重复提交",
            task={"decision_id": decision.id, "status": "dispatching"},
            unknown=True,
        )
    decision.status = "dispatching"
    task = await _create_local_task(
        db,
        decision=decision,
        session=session,
        user=user,
        client_message_id=client_message_id,
        assistant_message_id=assistant_message_id,
        intent=intent,
        prompt=normalized_prompt,
        params=params,
        source_image=source,
    )
    # This commit is the durable submission lease. Subsequent requests observe
    # submitting/dispatching and return the local task without calling a
    # provider, even while this request is awaiting the network.
    db.commit()
    submission_started = time.monotonic()
    logger.info(
        "media.dispatch.submitting",
        extra={
            "decision_id": decision.id,
            "task_id": task.id,
            "task_kind": kind,
            "intent": intent,
            "span": "media.provider_submit",
        },
    )

    image_provider_task: image_generation.ProviderTask | None = None
    video_provider_task: video_generation.ProviderTask | None = None
    try:
        if isinstance(task, ImageGenerationTask):
            image_provider_task = await _submit_image_task(
                task=task,
                session=session,
                prompt=normalized_prompt,
                source=source,
            )
        else:
            video_provider_task = await _submit_video_task(
                task=task,
                session=session,
                prompt=normalized_prompt,
                source=source,
            )
    except Exception as exc:
        try:
            _persist_submission_failure_with_retry(
                task_kind=kind,
                task_id=task.id,
                decision_id=decision.id,
                exc=exc,
            )
        except Exception:
            logger.exception(
                "media.dispatch.failure_persist_failed",
                extra={"decision_id": decision.id, "task_id": task.id},
            )
        unknown = _is_unknown_provider_error(exc)
        logger.warning(
            "media.dispatch.failed",
            extra={
                "decision_id": decision.id,
                "task_id": task.id,
                "task_kind": kind,
                "intent": intent,
                "outcome_unknown": unknown,
                "error_type": type(exc).__name__,
                "duration_ms": round(
                    (time.monotonic() - submission_started) * 1000,
                    1,
                ),
                "span": "media.provider_submit",
            },
        )
        raise

    completed_persisted: ImageGenerationTask | VideoGenerationTask
    completed_decision: ChatDispatchDecision
    if isinstance(task, ImageGenerationTask):
        assert image_provider_task is not None
        completed_status = _persist_image_provider_result_with_retry(
            task_id=task.id,
            decision_id=decision.id,
            result=image_provider_task,
        )
        with SessionLocal() as completed_db:
            refreshed_image = completed_db.get(ImageGenerationTask, task.id)
            if refreshed_image is None:
                raise RuntimeError("本地媒体任务已丢失")
            completed_persisted = refreshed_image
            completed_decision = (
                completed_db.get(ChatDispatchDecision, decision.id) or decision
            )
        if completed_status == "succeeded":
            await _mirror_succeeded_image(
                completed_persisted,
                provider_image_url=completed_persisted.image_url or "",
            )
    else:
        assert video_provider_task is not None
        completed_status = _persist_video_provider_result_with_retry(
            task_id=task.id,
            decision_id=decision.id,
            result=video_provider_task,
        )
        with SessionLocal() as completed_db:
            refreshed_video = completed_db.get(VideoGenerationTask, task.id)
            if refreshed_video is None:
                raise RuntimeError("本地媒体任务已丢失")
            completed_persisted = refreshed_video
            completed_decision = (
                completed_db.get(ChatDispatchDecision, decision.id) or decision
            )

    logger.info(
        "media.dispatch.completed",
        extra={
            "decision_id": decision.id,
            "task_id": completed_persisted.id,
            "task_kind": kind,
            "intent": intent,
            "status": completed_status,
            "provider_task_id_present": bool(completed_persisted.provider_task_id),
            "duration_ms": round(
                (time.monotonic() - submission_started) * 1000,
                1,
            ),
            "span": "media.provider_submit",
        },
    )
    with SessionLocal() as return_db:
        refreshed = return_db.get(type(completed_persisted), completed_persisted.id)
        if refreshed is None:
            raise RuntimeError("本地媒体任务已丢失")
        return _result_for_task(
            return_db,
            refreshed,
            completed_decision,
            created=created_decision,
        )


def reconcile_stale_submissions(
    db: Session,
    *,
    lease_seconds: int | None = None,
) -> dict[str, int]:
    """Fail closed after a process dies during an unconfirmed submission."""
    resolved_lease = lease_seconds or settings.media_dispatch_lease_seconds
    cutoff = now() - timedelta(seconds=max(30, resolved_lease))
    image_tasks = list(
        db.scalars(
            select(ImageGenerationTask).where(
                ImageGenerationTask.status == "submitting",
                ImageGenerationTask.provider_task_id.is_(None),
                ImageGenerationTask.updated_at < cutoff,
            )
        )
    )
    video_tasks = list(
        db.scalars(
            select(VideoGenerationTask).where(
                VideoGenerationTask.status == "submitting",
                VideoGenerationTask.provider_task_id.is_(None),
                VideoGenerationTask.updated_at < cutoff,
            )
        )
    )
    touched_decisions: set[str] = set()
    image_count = 0
    for task in image_tasks:
        stamp = now()
        changed_result = db.execute(
            update(ImageGenerationTask)
            .where(
                ImageGenerationTask.id == task.id,
                ImageGenerationTask.status == "submitting",
                ImageGenerationTask.provider_task_id.is_(None),
                ImageGenerationTask.updated_at < cutoff,
            )
            .values(
                status="outcome_unknown",
                error_message=_UNKNOWN_ERROR,
                updated_at=stamp,
                completed_at=None,
            )
        )
        changed = getattr(changed_result, "rowcount", 0) == 1
        if not changed:
            continue
        image_count += 1
        task.status = "outcome_unknown"
        task.error_message = _UNKNOWN_ERROR
        task.updated_at = stamp
        task.completed_at = None
        _sync_assistant_message(
            db,
            assistant_message_id=task.assistant_message_id,
            session_id=task.chat_session_id,
            owner_user_id=task.owner_user_id,
            kind="image",
            status="outcome_unknown",
            operation=task.operation,
        )
        if task.dispatch_id and _mark_stale_decision_if_dispatching(
            db, task.dispatch_id, cutoff, stamp
        ):
            touched_decisions.add(task.dispatch_id)

    video_count = 0
    for task in video_tasks:
        stamp = now()
        changed_result = db.execute(
            update(VideoGenerationTask)
            .where(
                VideoGenerationTask.id == task.id,
                VideoGenerationTask.status == "submitting",
                VideoGenerationTask.provider_task_id.is_(None),
                VideoGenerationTask.updated_at < cutoff,
            )
            .values(
                status="outcome_unknown",
                error_message=_UNKNOWN_ERROR,
                updated_at=stamp,
                completed_at=None,
            )
        )
        changed = getattr(changed_result, "rowcount", 0) == 1
        if not changed:
            continue
        video_count += 1
        task.status = "outcome_unknown"
        task.error_message = _UNKNOWN_ERROR
        task.updated_at = stamp
        task.completed_at = None
        _sync_assistant_message(
            db,
            assistant_message_id=task.assistant_message_id,
            session_id=task.chat_session_id,
            owner_user_id=task.owner_user_id,
            kind="video",
            status="outcome_unknown",
        )
        if task.dispatch_id and _mark_stale_decision_if_dispatching(
            db, task.dispatch_id, cutoff, stamp
        ):
            touched_decisions.add(task.dispatch_id)

    orphan_decisions = list(
        db.scalars(
            select(ChatDispatchDecision).where(
                ChatDispatchDecision.status == "dispatching",
                ChatDispatchDecision.updated_at < cutoff,
            )
        )
    )
    for decision in orphan_decisions:
        if _decision_has_live_task(db, decision.id):
            continue
        stamp = now()
        changed_result = db.execute(
            update(ChatDispatchDecision)
            .where(
                ChatDispatchDecision.id == decision.id,
                ChatDispatchDecision.status == "dispatching",
                ChatDispatchDecision.updated_at < cutoff,
            )
            .values(status="outcome_unknown", updated_at=stamp)
        )
        changed = getattr(changed_result, "rowcount", 0) == 1
        if changed:
            decision.status = "outcome_unknown"
            decision.updated_at = stamp
            touched_decisions.add(decision.id)
    db.flush()
    return {
        "image_tasks": image_count,
        "video_tasks": video_count,
        "decisions": len(touched_decisions),
    }


async def _retry_image_task(
    db: Session,
    task: ImageGenerationTask,
    *,
    session: ChatSession,
    user: User,
) -> MediaTaskDispatchResult:
    operation = _image_operation(task)
    source = _source_image_for_task(db, task)
    if operation == "edit":
        task.aspect_ratio = await asyncio.to_thread(
            image_generation.edit_output_aspect_ratio, task.model, source or {}
        )
    decision = db.get(ChatDispatchDecision, task.dispatch_id) if task.dispatch_id else None
    if decision is None:
        intent: MediaIntent = (
            "image_edit" if operation == "edit" else "image_generate"
        )
        retry_key = f"legacy-retry-{task.id}-{new_token(6)}"
        decision, _ = chat_dispatch.get_or_create_dispatch_decision(
            db,
            owner_user_id=user.id,
            chat_session_id=session.id,
            client_message_id=retry_key,
            predicted_intent=intent,
            effective_route=intent,
            final_intent=intent,
            route_source="explicit_ui",
            confidence_band="high",
            reason_code="legacy_retry",
            status="dispatching",
            target_type="image_generation_task",
            target_id=task.id,
            prompt_digest=chat_dispatch.prompt_digest(task.prompt),
            attachment_digest=chat_dispatch.attachment_digest(source),
        )
        task.dispatch_id = decision.id
    task.status = "submitting"
    task.provider_task_id = None
    task.error_message = None
    task.updated_at = now()
    decision.status = "dispatching"
    decision.updated_at = now()
    db.commit()

    try:
        provider_task = await _submit_image_task(
            task=task,
            session=session,
            prompt=task.prompt,
            source=source,
        )
    except Exception as exc:
        try:
            _persist_submission_failure_with_retry(
                task_kind="image",
                task_id=task.id,
                decision_id=decision.id,
                exc=exc,
                retried=True,
            )
        except Exception:
            logger.exception(
                "media.retry.failure_persist_failed",
                extra={"decision_id": decision.id, "task_id": task.id},
            )
        raise

    completed_status = _persist_image_provider_result_with_retry(
        task_id=task.id,
        decision_id=decision.id,
        result=provider_task,
        retried=True,
    )
    if completed_status == "succeeded":
        with SessionLocal() as mirror_db:
            refreshed = mirror_db.get(ImageGenerationTask, task.id)
            if refreshed is not None:
                await _mirror_succeeded_image(
                    refreshed,
                    provider_image_url=refreshed.image_url or "",
                )
    with SessionLocal() as return_db:
        refreshed = return_db.get(ImageGenerationTask, task.id)
        if refreshed is None:
            raise RuntimeError("本地媒体任务已丢失")
        refreshed_decision = return_db.get(ChatDispatchDecision, decision.id) or decision
        return _result_for_task(
            return_db,
            refreshed,
            refreshed_decision,
            created=False,
        )


async def _retry_video_task(
    db: Session,
    task: VideoGenerationTask,
    *,
    session: ChatSession,
    user: User,
) -> MediaTaskDispatchResult:
    decision = db.get(ChatDispatchDecision, task.dispatch_id) if task.dispatch_id else None
    if decision is None:
        retry_key = f"legacy-retry-{task.id}-{new_token(6)}"
        decision, _ = chat_dispatch.get_or_create_dispatch_decision(
            db,
            owner_user_id=user.id,
            chat_session_id=session.id,
            client_message_id=retry_key,
            predicted_intent="video_generate",
            effective_route="video_generate",
            final_intent="video_generate",
            route_source="explicit_ui",
            confidence_band="high",
            reason_code="legacy_retry",
            status="dispatching",
            target_type="video_generation_task",
            target_id=task.id,
            prompt_digest=chat_dispatch.prompt_digest(task.prompt),
            attachment_digest=chat_dispatch.attachment_digest(task.source_file_ref),
        )
        task.dispatch_id = decision.id
    task.status = "submitting"
    task.provider_task_id = None
    task.error_message = None
    task.updated_at = now()
    decision.status = "dispatching"
    decision.updated_at = now()
    source_file_ref = task.source_file_ref
    provider_prompt = build_contextual_prompt(
        chat_repo.active_messages(session.messages),
        task.prompt,
        generation_label="视频",
        max_length=video_generation.VIDEO_PROMPT_MAX_LENGTH,
        excluded_message_ids=frozenset(
            {task.user_message_id, task.assistant_message_id}
        ),
    )
    db.commit()

    try:
        if source_file_ref:
            provider_task = await video_generation.create_provider_task(
                prompt=provider_prompt,
                mode=task.mode,
                duration=task.duration,
                source_file_ref=source_file_ref,
            )
        else:
            provider_task = await video_generation.create_provider_task(
                prompt=provider_prompt,
                mode=task.mode,
                duration=task.duration,
            )
    except Exception as exc:
        try:
            _persist_submission_failure_with_retry(
                task_kind="video",
                task_id=task.id,
                decision_id=decision.id,
                exc=exc,
                retried=True,
            )
        except Exception:
            logger.exception(
                "media.retry.failure_persist_failed",
                extra={"decision_id": decision.id, "task_id": task.id},
            )
        raise

    _persist_video_provider_result_with_retry(
        task_id=task.id,
        decision_id=decision.id,
        result=provider_task,
        retried=True,
    )
    with SessionLocal() as return_db:
        refreshed = return_db.get(VideoGenerationTask, task.id)
        if refreshed is None:
            raise RuntimeError("本地媒体任务已丢失")
        refreshed_decision = return_db.get(ChatDispatchDecision, decision.id) or decision
        return _result_for_task(
            return_db,
            refreshed,
            refreshed_decision,
            created=False,
        )


async def retry_media_task(
    *, task_kind: MediaTaskKind, task_id: str, user: User
) -> MediaTaskDispatchResult:
    """Retry only a definite local failure under a fresh explicit lease."""
    db = SessionLocal()
    try:
        if task_kind == "image":
            task = db.scalar(
                select(ImageGenerationTask)
                .where(
                    ImageGenerationTask.id == task_id,
                    ImageGenerationTask.owner_user_id == user.id,
                )
                .with_for_update()
            )
            if task is None:
                raise LookupError("任务不存在")
            if task.status != "failed":
                raise ValueError("仅确定失败任务可以重试")
            session = db.get(ChatSession, task.chat_session_id)
            if session is None or session.owner_user_id != user.id:
                raise LookupError("会话不存在")
            return await _retry_image_task(db, task, session=session, user=user)

        task = db.scalar(
            select(VideoGenerationTask)
            .where(
                VideoGenerationTask.id == task_id,
                VideoGenerationTask.owner_user_id == user.id,
            )
            .with_for_update()
        )
        if task is None:
            raise LookupError("任务不存在")
        if task.status != "failed":
            raise ValueError("仅确定失败任务可以重试")
        session = db.get(ChatSession, task.chat_session_id)
        if session is None or session.owner_user_id != user.id:
            raise LookupError("会话不存在")
        return await _retry_video_task(db, task, session=session, user=user)
    finally:
        db.close()
