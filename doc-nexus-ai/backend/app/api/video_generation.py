from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import SessionLocal, transaction
from app.core.security import iso, new_token, now
from app.models.auth import User
from app.models.chat import ChatDispatchDecision, ChatMessage
from app.models.video_generation import VideoGenerationTask
from app.schemas.chat import ChatImageContent
from app.repositories import chat as chat_repo
from app.repositories import chat_dispatch
from app.services import generation_retry_guard
from app.services import media_task_dispatch
from app.services import video_generation as video_service

VideoGenerationMode = Literal["std", "pro", "4k"]
VideoGenerationStatus = Literal[
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
_GENERIC_PROVIDER_ERROR = "视频生成失败，请重试。"
_EXPIRED_PROVIDER_ERROR = "任务已过期或不存在"
_UNKNOWN_PROVIDER_ERROR = "供应商结果未知，请勿重复提交。"

_VIDEO_STATUS_MAP: dict[str, VideoGenerationStatus] = {
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


class VideoGenerationCreateRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=video_service.VIDEO_PROMPT_MAX_LENGTH)
    mode: VideoGenerationMode = "std"
    duration: int = Field(default=5, ge=3, le=15)
    first_frame: ChatImageContent | None = None


class VideoGenerationTaskResponse(BaseModel):
    id: str
    session_id: str
    user_message_id: str
    assistant_message_id: str
    model: str
    mode: VideoGenerationMode
    duration: int
    prompt: str
    status: VideoGenerationStatus
    retryable: bool
    dispatch_id: str | None = None
    video_url: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


router = APIRouter(prefix="/video-generation", tags=["video-generation"])


def _provider_status(status: video_service.VideoProviderStatus) -> VideoGenerationStatus:
    if status == "unknown":
        return "failed"
    return cast(VideoGenerationStatus, status)


def _task_status(value: object) -> VideoGenerationStatus:
    status = _VIDEO_STATUS_MAP.get(str(value))
    if status is None:
        raise RuntimeError("视频任务状态无效")
    return status


def _task_response(task: VideoGenerationTask) -> VideoGenerationTaskResponse:
    return VideoGenerationTaskResponse.model_validate(
        media_task_dispatch.video_task_to_dict(task)
    )


def _owned_task(
    db,
    task_id: str,
    user_id: str,
    *,
    for_update: bool = False,
) -> VideoGenerationTask | None:
    statement = select(VideoGenerationTask).where(
        VideoGenerationTask.id == task_id,
        VideoGenerationTask.owner_user_id == user_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _provider_error_message(
    result: video_service.ProviderTask, status: VideoGenerationStatus
) -> str | None:
    if result.status == "unknown":
        return _EXPIRED_PROVIDER_ERROR
    if status == "outcome_unknown":
        return _UNKNOWN_PROVIDER_ERROR
    if status == "failed":
        return _GENERIC_PROVIDER_ERROR
    return None


def _task_message_content(status: VideoGenerationStatus, *, retried: bool = False) -> str:
    from app.services.pi_runtime.persona import load_pi_persona

    name = load_pi_persona().name
    if status == "succeeded":
        return "视频生成任务已完成。"
    if status == "failed":
        return "视频生成任务失败，请重试。"
    if status == "outcome_unknown":
        return "视频生成结果未知，请勿重复提交。"
    if retried:
        return f"视频生成任务已重新提交，{name}正在处理中。"
    return f"视频生成任务已提交，{name}正在处理中。"


def _sync_assistant_message(
    db, task: VideoGenerationTask, *, retried: bool = False
) -> None:
    message = db.scalar(
        select(ChatMessage).where(
            ChatMessage.id == task.assistant_message_id,
            ChatMessage.session_id == task.chat_session_id,
            ChatMessage.owner_user_id == task.owner_user_id,
        )
    )
    if message is not None:
        content = _task_message_content(
            _task_status(task.status), retried=retried
        )
        if message.content != content:
            message.content = content
            chat_repo.advance_messages_revision_for_session(db, message.session_id)


async def _cleanup_completed_source_file(
    task_id: str, user_id: str, source_file_ref: str | None
) -> None:
    if not source_file_ref:
        return
    if not await video_service.delete_provider_file(source_file_ref):
        return
    with transaction() as db:
        task = _owned_task(db, task_id, user_id)
        if (
            task is not None
            and task.status == "succeeded"
            and task.source_file_ref == source_file_ref
        ):
            task.source_file_ref = None


def _apply_provider_result(
    db,
    task: VideoGenerationTask,
    result: video_service.ProviderTask,
) -> str | None:
    status = _provider_status(result.status)
    error_message = _provider_error_message(result, status)
    if status == "succeeded" and not result.video_url:
        status = "outcome_unknown"
        error_message = _UNKNOWN_PROVIDER_ERROR
    task.provider_task_id = result.provider_task_id or task.provider_task_id
    task.status = status
    task.video_url = result.video_url
    if result.source_file_ref:
        task.source_file_ref = result.source_file_ref
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
    return task.source_file_ref if task.status == "succeeded" else None


def _persist_provider_result_with_retry(
    *,
    task_id: str,
    user_id: str,
    provider_task_id: str,
    provider_task: video_service.ProviderTask,
) -> str | None:
    def apply(db) -> str | None:
        task = _owned_task(db, task_id, user_id, for_update=True)
        if task is None:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        if (
            task.provider_task_id != provider_task_id
            or task.status in _TERMINAL_STATUSES
        ):
            return None
        return _apply_provider_result(db, task, provider_task)

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
            raise HTTPException(status_code=404, detail="视频任务不存在")
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
    if isinstance(exc, video_service.VideoGenerationNotConfiguredError):
        return HTTPException(status_code=503, detail="视频生成功能未配置")
    if isinstance(exc, video_service.VideoGenerationInputError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


def _legacy_client_message_id() -> str:
    return f"legacy-video-{new_token(8)}"[:64]


@router.post("/tasks", response_model=VideoGenerationTaskResponse, status_code=201)
async def create_task(
    payload: VideoGenerationCreateRequest,
    response: Response,
    user: User = Depends(current_user),
) -> VideoGenerationTaskResponse:
    client_message_id = payload.client_message_id or _legacy_client_message_id()
    if payload.client_message_id is None:
        response.headers["Deprecation"] = "client_message_id is required for new requests"
        response.headers["X-Client-Message-ID-Compatibility"] = "explicit-manual-only"
    source_image: dict[str, object] | None = (
        payload.first_frame.model_dump() if payload.first_frame is not None else None
    )
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
                intent="video_generate",
                prompt=payload.prompt,
                media_params={
                    "mode": payload.mode,
                    "duration": payload.duration,
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
            video_service.VideoGenerationNotConfiguredError,
            video_service.VideoGenerationInputError,
            video_service.VideoGenerationProviderError,
        ) as exc:
            raise _provider_error(exc) from exc
        if not result["created"]:
            response.status_code = 200
        return VideoGenerationTaskResponse.model_validate(result["task"])


@router.get("/tasks", response_model=list[VideoGenerationTaskResponse])
def list_tasks(
    session_id: str | None = None, user: User = Depends(current_user)
) -> list[VideoGenerationTaskResponse]:
    with SessionLocal() as db:
        if session_id and not chat_repo.get_owned_session(db, session_id, user.id):
            raise HTTPException(status_code=404, detail="会话不存在")
        statement = select(VideoGenerationTask).where(
            VideoGenerationTask.owner_user_id == user.id
        )
        if session_id:
            statement = statement.where(VideoGenerationTask.chat_session_id == session_id)
        statement = statement.order_by(VideoGenerationTask.created_at.asc())
        return [_task_response(task) for task in db.scalars(statement)]


@router.get("/tasks/{task_id}", response_model=VideoGenerationTaskResponse)
async def get_task(
    task_id: str, user: User = Depends(current_user)
) -> VideoGenerationTaskResponse:
    with SessionLocal() as db:
        task = _owned_task(db, task_id, user.id)
        if not task:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        if task.status == "submitting" and task.provider_task_id is None:
            media_task_dispatch.reconcile_stale_submission_for_read(
                db,
                task_kind="video",
                task_id=task.id,
                owner_user_id=user.id,
            )
            task = _owned_task(db, task_id, user.id)
            if task is None:
                raise HTTPException(status_code=404, detail="视频任务不存在")
        provider_task_id = task.provider_task_id
        current_status = task.status
        if current_status in _TERMINAL_STATUSES or not provider_task_id:
            response = _task_response(task)
            cleanup_file_ref = task.source_file_ref if current_status == "succeeded" else None
        else:
            response = None
            cleanup_file_ref = None
    if response is not None:
        if cleanup_file_ref:
            await _cleanup_completed_source_file(task_id, user.id, cleanup_file_ref)
        return response

    if provider_task_id is None:
        raise RuntimeError("视频任务缺少供应商任务 ID")
    try:
        provider_task = await video_service.poll_provider_task(provider_task_id)
    except video_service.VideoGenerationNotConfiguredError as exc:
        raise _provider_error(exc) from exc
    except video_service.VideoGenerationProviderError as exc:
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
                raise HTTPException(status_code=404, detail="视频任务不存在")
            return _task_response(task)

    cleanup_file_ref = _persist_provider_result_with_retry(
        task_id=task_id,
        user_id=user.id,
        provider_task_id=provider_task_id,
        provider_task=provider_task,
    )
    with SessionLocal() as db:
        task = _owned_task(db, task_id, user.id)
        if not task:
            raise HTTPException(status_code=404, detail="视频任务不存在")
        response = _task_response(task)
    if cleanup_file_ref:
        await _cleanup_completed_source_file(task_id, user.id, cleanup_file_ref)
    return response


@router.post("/tasks/{task_id}/retry", response_model=VideoGenerationTaskResponse)
async def retry_task(
    task_id: str, user: User = Depends(current_user)
) -> VideoGenerationTaskResponse:
    if not generation_retry_guard.claim("video", task_id):
        raise HTTPException(status_code=409, detail="任务正在重试，请勿重复提交")
    try:
        try:
            result = await media_task_dispatch.retry_media_task(
                task_kind="video", task_id=task_id, user=user
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="视频任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (
            video_service.VideoGenerationNotConfiguredError,
            video_service.VideoGenerationInputError,
            video_service.VideoGenerationProviderError,
        ) as exc:
            raise _provider_error(exc) from exc
        return VideoGenerationTaskResponse.model_validate(result["task"])
    finally:
        generation_retry_guard.release("video", task_id)
