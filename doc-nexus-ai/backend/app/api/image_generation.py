from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.api.deps import current_user, current_user_from_bearer_or_cookie
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import new_token, now
from app.models.auth import User
from app.models.chat import ChatDispatchDecision, ChatMessage
from app.models.image_generation import ImageGenerationTask
from app.repositories import chat as chat_repo
from app.repositories import chat_dispatch
from app.schemas.chat import ChatImageContent
from app.services import generation_retry_guard
from app.services import image_generation as image_service
from app.services import media_task_dispatch

ImageGenerationResolution = Literal["1k", "2k"]
ImageGenerationOperation = Literal["generate", "edit"]
ImageGenerationStatus = Literal[
    "pending",
    "running",
    "submitting",
    "outcome_unknown",
    "succeeded",
    "failed",
    "canceled",
]
_TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "canceled", "outcome_unknown"}
)
_GENERIC_PROVIDER_ERROR = "图片生成失败，请重试。"
_EXPIRED_PROVIDER_ERROR = "任务已过期或不存在"
_UNKNOWN_PROVIDER_ERROR = "供应商结果未知，请勿重复提交。"

_IMAGE_STATUS_MAP: dict[str, ImageGenerationStatus] = {
    status: status
    for status in (
        "pending",
        "running",
        "submitting",
        "outcome_unknown",
        "succeeded",
        "failed",
        "canceled",
    )
}
_IMAGE_OPERATION_MAP: dict[str, ImageGenerationOperation] = {
    "generate": "generate",
    "edit": "edit",
}


class ImageGenerationCreateRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=64)
    operation: ImageGenerationOperation = "generate"
    prompt: str = Field(min_length=1, max_length=image_service.IMAGE_PROMPT_MAX_LENGTH)
    resolution: ImageGenerationResolution = "1k"
    source_image: ChatImageContent | None = None

    @model_validator(mode="after")
    def _validate_operation_source(self) -> ImageGenerationCreateRequest:
        if self.operation == "generate" and self.source_image is not None:
            raise ValueError("文生图任务不能包含待编辑图片")
        if self.operation == "edit" and self.source_image is None:
            raise ValueError("图片编辑任务必须包含一张待编辑图片")
        return self


class ImageGenerationTaskResponse(BaseModel):
    id: str
    session_id: str
    user_message_id: str
    assistant_message_id: str
    operation: ImageGenerationOperation
    model: str
    aspect_ratio: Literal["1:1", "16:9", "9:16", "source"]
    resolution: ImageGenerationResolution
    prompt: str
    status: ImageGenerationStatus
    retryable: bool
    dispatch_id: str | None = None
    image_url: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class ImageEditSourceResponse(BaseModel):
    source_image: ChatImageContent


router = APIRouter(prefix="/image-generation", tags=["image-generation"])
logger = logging.getLogger(__name__)


def _provider_status(
    status: image_service.ImageProviderStatus,
) -> ImageGenerationStatus:
    if status == "unknown":
        return "failed"
    return status


def _task_status(value: object) -> ImageGenerationStatus:
    status = _IMAGE_STATUS_MAP.get(str(value))
    if status is None:
        raise RuntimeError("图片任务状态无效")
    return status


def _task_operation(value: object) -> ImageGenerationOperation:
    operation = _IMAGE_OPERATION_MAP.get(str(value))
    if operation is None:
        raise RuntimeError("图片任务类型无效")
    return operation


def _source_image_for_task(db, task: ImageGenerationTask) -> dict[str, str] | None:
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
    image = images[0]
    return image if isinstance(image, dict) else None


def _task_response(db, task: ImageGenerationTask) -> ImageGenerationTaskResponse:
    return ImageGenerationTaskResponse.model_validate(
        media_task_dispatch.image_task_to_dict(db, task)
    )


def _owned_task(
    db,
    task_id: str,
    user_id: str,
    *,
    for_update: bool = False,
) -> ImageGenerationTask | None:
    statement = select(ImageGenerationTask).where(
        ImageGenerationTask.id == task_id,
        ImageGenerationTask.owner_user_id == user_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _task_message_content(
    status: ImageGenerationStatus,
    operation: ImageGenerationOperation = "generate",
    *,
    retried: bool = False,
) -> str:
    from app.services.pi_runtime.persona import load_pi_persona

    name = load_pi_persona().name
    label = "图片编辑" if operation == "edit" else "图片生成"
    if status == "succeeded":
        return f"{label}任务已完成。"
    if status == "failed":
        return f"{label}任务失败，请重试。"
    if status == "outcome_unknown":
        return f"{label}结果未知，请勿重复提交。"
    if retried:
        return f"{label}任务已重新提交，{name}正在处理中。"
    return f"{label}任务已提交，{name}正在处理中。"


def _sync_assistant_message(
    db, task: ImageGenerationTask, *, retried: bool = False
) -> None:
    message = db.scalar(
        select(ChatMessage).where(
            ChatMessage.id == task.assistant_message_id,
            ChatMessage.session_id == task.chat_session_id,
            ChatMessage.owner_user_id == task.owner_user_id,
        )
    )
    if message is not None:
        status = _task_status(task.status)
        operation = _task_operation(
            getattr(task, "operation", None) or "generate"
        )
        content = _task_message_content(
            status,
            operation,
            retried=retried,
        )
        if message.content != content:
            message.content = content
            chat_repo.advance_messages_revision_for_session(db, message.session_id)


def _provider_error_message(
    result: image_service.ProviderTask, status: ImageGenerationStatus
) -> str | None:
    if result.status == "unknown":
        return _EXPIRED_PROVIDER_ERROR
    if status == "outcome_unknown":
        return _UNKNOWN_PROVIDER_ERROR
    if status == "failed":
        return _GENERIC_PROVIDER_ERROR
    return None


def _apply_provider_result(
    db,
    task: ImageGenerationTask,
    result: image_service.ProviderTask,
) -> None:
    status = _provider_status(result.status)
    error_message = _provider_error_message(result, status)
    if status == "succeeded" and not result.image_url:
        status = "outcome_unknown"
        error_message = _UNKNOWN_PROVIDER_ERROR
    task.provider_task_id = result.provider_task_id or task.provider_task_id
    task.status = status
    task.image_url = result.image_url
    task.image_data = None
    task.image_context_consumed_at = None
    task.error_message = error_message
    task.updated_at = now()
    task.completed_at = now() if status in _TERMINAL_STATUSES else None
    decision = (
        db.get(ChatDispatchDecision, task.dispatch_id) if task.dispatch_id else None
    )
    if decision is not None:
        decision.status = (
            "outcome_unknown"
            if status == "outcome_unknown"
            else "failed"
            if status == "failed"
            else "dispatched"
        )
        decision.updated_at = now()
        if task.provider_task_id:
            decision.dispatched_at = now()
    _sync_assistant_message(db, task)


def _persist_provider_result_with_retry(
    *,
    task_id: str,
    user_id: str,
    provider_task_id: str,
    provider_task: image_service.ProviderTask,
) -> bool:
    def apply(db) -> bool:
        task = _owned_task(db, task_id, user_id, for_update=True)
        if task is None:
            raise HTTPException(status_code=404, detail="图片任务不存在")
        if (
            task.provider_task_id != provider_task_id
            or task.status in _TERMINAL_STATUSES
        ):
            return False
        previous_status = task.status
        _apply_provider_result(db, task, provider_task)
        return task.status == "succeeded" and previous_status != "succeeded"

    return media_task_dispatch.run_db_transaction_with_retry(apply)


def _persist_provider_error_with_retry(
    *,
    task_id: str,
    user_id: str,
    provider_task_id: str,
    unknown: bool,
) -> None:
    def apply(db) -> None:
        task = _owned_task(db, task_id, user_id, for_update=True)
        if task is None:
            raise HTTPException(status_code=404, detail="图片任务不存在")
        if (
            task.provider_task_id != provider_task_id
            or task.status in _TERMINAL_STATUSES
        ):
            return
        task.status = "outcome_unknown" if unknown else "failed"
        task.error_message = (
            _UNKNOWN_PROVIDER_ERROR if unknown else _GENERIC_PROVIDER_ERROR
        )
        task.updated_at = now()
        decision = (
            db.get(ChatDispatchDecision, task.dispatch_id)
            if task.dispatch_id
            else None
        )
        if decision is not None:
            decision.status = "outcome_unknown" if unknown else "failed"
            decision.updated_at = now()
        _sync_assistant_message(db, task)

    media_task_dispatch.run_db_transaction_with_retry(apply)


def _provider_error(exc: Exception) -> HTTPException:
    if isinstance(exc, image_service.ImageGenerationNotConfiguredError):
        return HTTPException(status_code=503, detail="图片生成功能未配置")
    if isinstance(exc, image_service.ImageGenerationInputError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


def _persist_image_cache_uri_best_effort(
    *, task_id: str, user_id: str, image_cache_uri: str
) -> None:
    def persist(db) -> None:
        persisted = _owned_task(db, task_id, user_id)
        if (
            persisted is None
            or persisted.status != "succeeded"
            or persisted.image_cache_uri == image_cache_uri
        ):
            return
        persisted.image_cache_uri = image_cache_uri
        persisted.updated_at = now()

    try:
        media_task_dispatch.run_db_transaction_with_retry(
            persist,
            session_factory=SessionLocal,
            attempts=1,
        )
    except Exception:
        logger.warning(
            "image_generation.cache_metadata_persist_failed",
            extra={"task_id": task_id},
            exc_info=True,
        )


def _legacy_client_message_id() -> str:
    return f"legacy-image-{new_token(8)}"[:64]


@router.post("/tasks", response_model=ImageGenerationTaskResponse, status_code=201)
async def create_task(
    payload: ImageGenerationCreateRequest,
    response: Response,
    user: User = Depends(current_user),
) -> ImageGenerationTaskResponse:
    if payload.operation == "edit" and settings.linkmind_enabled:
        raise HTTPException(
            status_code=503,
            detail="图片编辑在当前部署模式下不可用",
        )
    client_message_id = payload.client_message_id or _legacy_client_message_id()
    if payload.client_message_id is None:
        response.headers["Deprecation"] = (
            "client_message_id is required for new requests"
        )
        response.headers["X-Client-Message-ID-Compatibility"] = "explicit-manual-only"
    source_image = payload.source_image.model_dump() if payload.source_image else None
    assistant_message_id = f"chm_{new_token(10)}"
    with SessionLocal() as db:
        session = chat_repo.get_owned_session(db, payload.session_id, user.id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        try:
            result = await media_task_dispatch.dispatch_media_task(
                db,
                session=session,
                user=user,
                client_message_id=client_message_id,
                assistant_message_id=assistant_message_id,
                intent=(
                    "image_edit" if payload.operation == "edit" else "image_generate"
                ),
                prompt=payload.prompt,
                media_params={
                    "operation": payload.operation,
                    "resolution": payload.resolution,
                    "route_source": "explicit_ui",
                    "reason_code": "explicit_media_api",
                },
                source_image=source_image,
            )
        except chat_dispatch.DispatchConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except media_task_dispatch.MediaDispatchError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (
            image_service.ImageGenerationNotConfiguredError,
            image_service.ImageGenerationInputError,
            image_service.ImageGenerationProviderError,
        ) as exc:
            raise _provider_error(exc) from exc
        if not result["created"]:
            response.status_code = 200
        return ImageGenerationTaskResponse.model_validate(result["task"])


@router.get("/tasks", response_model=list[ImageGenerationTaskResponse])
def list_tasks(
    session_id: str | None = None, user: User = Depends(current_user)
) -> list[ImageGenerationTaskResponse]:
    with SessionLocal() as db:
        if session_id and not chat_repo.get_owned_session(db, session_id, user.id):
            raise HTTPException(status_code=404, detail="会话不存在")
        statement = select(ImageGenerationTask).where(
            ImageGenerationTask.owner_user_id == user.id
        )
        if session_id:
            statement = statement.where(
                ImageGenerationTask.chat_session_id == session_id
            )
        statement = statement.order_by(ImageGenerationTask.created_at.asc())
        return [_task_response(db, task) for task in db.scalars(statement)]


@router.get("/tasks/{task_id}", response_model=ImageGenerationTaskResponse)
async def get_task(
    task_id: str, user: User = Depends(current_user)
) -> ImageGenerationTaskResponse:
    with SessionLocal() as db:
        task = _owned_task(db, task_id, user.id)
        if not task:
            raise HTTPException(status_code=404, detail="图片任务不存在")
        if task.status == "submitting" and task.provider_task_id is None:
            media_task_dispatch.reconcile_stale_submission_for_read(
                db,
                task_kind="image",
                task_id=task.id,
                owner_user_id=user.id,
            )
            task = _owned_task(db, task_id, user.id)
            if task is None:
                raise HTTPException(status_code=404, detail="图片任务不存在")
        provider_task_id = task.provider_task_id
        current_status = task.status
        if current_status in _TERMINAL_STATUSES or not provider_task_id:
            return _task_response(db, task)

    try:
        provider_task = await image_service.poll_provider_task(provider_task_id)
    except image_service.ImageGenerationNotConfiguredError as exc:
        raise _provider_error(exc) from exc
    except image_service.ImageGenerationProviderError as exc:
        unknown = media_task_dispatch._is_unknown_provider_error(exc)
        _persist_provider_error_with_retry(
            task_id=task_id,
            user_id=user.id,
            provider_task_id=provider_task_id,
            unknown=unknown,
        )
        with SessionLocal() as db:
            task = _owned_task(db, task_id, user.id)
            if not task:
                raise HTTPException(status_code=404, detail="图片任务不存在")
            return _task_response(db, task)

    became_succeeded = _persist_provider_result_with_retry(
        task_id=task_id,
        user_id=user.id,
        provider_task_id=provider_task_id,
        provider_task=provider_task,
    )

    if became_succeeded:
        with SessionLocal() as db:
            task = _owned_task(db, task_id, user.id)
            if task is not None and task.status == "succeeded":
                await media_task_dispatch._mirror_succeeded_image(
                    task,
                    provider_image_url=task.image_url or "",
                )

    with SessionLocal() as db:
        task = _owned_task(db, task_id, user.id)
        if not task:
            raise HTTPException(status_code=404, detail="图片任务不存在")
        return _task_response(db, task)


@router.get(
    "/tasks/{task_id}/edit-source",
    response_model=ImageEditSourceResponse,
)
async def get_edit_source(
    task_id: str,
    user: User = Depends(current_user),
) -> ImageEditSourceResponse:
    if settings.linkmind_enabled:
        raise HTTPException(
            status_code=503,
            detail="图片编辑在当前部署模式下不可用",
        )
    with SessionLocal() as db:
        task = _owned_task(db, task_id, user.id)
        if not task:
            raise HTTPException(status_code=404, detail="图片任务不存在")
        image_url = task.image_url if task.status == "succeeded" else None
        image_cache_uri = task.image_cache_uri if task.status == "succeeded" else None
        task_snapshot = task
    source_image = await _edit_source_image_for_task(
        task_snapshot,
        image_url=image_url,
        image_cache_uri=image_cache_uri,
    )
    if source_image is None:
        raise HTTPException(
            status_code=409,
            detail="编辑结果图片已不可用，请重新上传原图",
        )
    return ImageEditSourceResponse(
        source_image=ChatImageContent.model_validate(source_image)
    )


async def _edit_source_image_for_task(
    task: ImageGenerationTask,
    *,
    image_url: str | None,
    image_cache_uri: str | None,
) -> dict[str, str] | None:
    return await media_task_dispatch.resolve_succeeded_image_edit_source(
        task,
        image_url=image_url,
        image_cache_uri=image_cache_uri,
    )


_IMAGE_SERVE_CACHE_MAX_AGE = 300


@router.get("/tasks/{task_id}/image", response_class=Response)
async def get_task_image(
    task_id: str,
    user: User = Depends(current_user_from_bearer_or_cookie),
) -> Response:
    """Serve a generated/edit result image bytes to the owning user.

    Images are stored in the object cache to outlive short-lived provider
    URLs; this endpoint lets the browser load them same-origin (cookie auth)
    without exposing MinIO or depending on provider CDN availability.
    """
    with SessionLocal() as db:
        task = _owned_task(db, task_id, user.id)
        if not task:
            raise HTTPException(status_code=404, detail="图片任务不存在")
        if task.status != "succeeded":
            raise HTTPException(status_code=409, detail="图片任务尚未完成")
        image_cache_uri = task.image_cache_uri
        image_url = task.image_url

    content = (
        await image_service.read_cached_image_bytes_async(image_cache_uri)
        if image_cache_uri
        else None
    )
    cache_available = await asyncio.to_thread(image_service.image_cache_is_available)
    if content is None and image_url and cache_available:
        # Self-heal: a live provider URL can be re-mirrored and served.
        mirrored = await image_service.mirror_provider_image(
            owner_user_id=user.id,
            task_id=task_id,
            image_url=image_url,
        )
        if mirrored is not None:
            _persist_image_cache_uri_best_effort(
                task_id=task_id,
                user_id=user.id,
                image_cache_uri=mirrored,
            )
            content = await image_service.read_cached_image_bytes_async(mirrored)
    if content is None:
        raise HTTPException(
            status_code=409,
            detail="图片已不可用，请重新生成",
        )
    mime_type = await asyncio.to_thread(image_service._image_content_type, content)
    return Response(
        content=content,
        media_type=mime_type,
        headers={
            "Cache-Control": f"private, max-age={_IMAGE_SERVE_CACHE_MAX_AGE}",
            "Content-Disposition": "inline",
        },
    )


@router.post("/tasks/{task_id}/retry", response_model=ImageGenerationTaskResponse)
async def retry_task(
    task_id: str, user: User = Depends(current_user)
) -> ImageGenerationTaskResponse:
    if not generation_retry_guard.claim("image", task_id):
        raise HTTPException(status_code=409, detail="任务正在重试，请勿重复提交")
    try:
        try:
            result = await media_task_dispatch.retry_media_task(
                task_kind="image", task_id=task_id, user=user
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="图片任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (
            image_service.ImageGenerationNotConfiguredError,
            image_service.ImageGenerationInputError,
            image_service.ImageGenerationProviderError,
        ) as exc:
            raise _provider_error(exc) from exc
        return ImageGenerationTaskResponse.model_validate(result["task"])
    finally:
        generation_retry_guard.release("image", task_id)
