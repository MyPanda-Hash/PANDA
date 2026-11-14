from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.services.video_file_adapter import (
    VideoFileAdapter,
    VideoFileAdapterError,
    VideoFileAdapterName,
    adapter_for_ref,
    build_video_file_adapter,
)

logger = logging.getLogger(__name__)

VIDEO_SYNTHESIS_PATH = "/api/v1/services/aigc/video-generation/video-synthesis"
TASK_PATH = "/api/v1/tasks"
_VIDEO_MODES = frozenset({"std", "pro", "4k"})
_FIRST_FRAME_MIME_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_MAX_FIRST_FRAME_BYTES = 8 * 1024 * 1024
_MIN_DURATION = 3
_MAX_DURATION = 15
VIDEO_PROMPT_MAX_LENGTH = 2500
_GENERIC_PROVIDER_ERROR = "视频生成失败，请重试。"
_EXPIRED_PROVIDER_ERROR = "任务已过期或不存在"

VideoProviderStatus = Literal[
    "pending", "running", "succeeded", "failed", "canceled", "unknown"
]


class VideoGenerationNotConfiguredError(RuntimeError):
    """The server has no usable DashScope video configuration."""


class VideoGenerationInputError(ValueError):
    """A video generation request violates the provider contract."""


class VideoGenerationProviderError(RuntimeError):
    """DashScope returned an unusable or unsuccessful response.

    ``outcome_unknown`` distinguishes a transport/response failure after the
    provider may have accepted the request from a definite rejection.  A
    first-frame upload reference is carried through unknown failures so the
    durable task can retain it for reconciliation.
    """

    def __init__(
        self,
        message: str,
        *,
        outcome_unknown: bool = False,
        source_file_ref: str | None = None,
    ) -> None:
        super().__init__(message)
        self.outcome_unknown = outcome_unknown
        self.source_file_ref = source_file_ref

@dataclass(frozen=True)
class ProviderTask:
    provider_task_id: str
    status: VideoProviderStatus
    video_url: str | None = None
    error_message: str | None = None
    source_file_ref: str | None = None


def _validate_input(prompt: str, mode: str, duration: int) -> str:
    normalized_prompt = (prompt or "").strip()
    if not normalized_prompt:
        raise VideoGenerationInputError("视频提示词不能为空")
    if len(normalized_prompt) > VIDEO_PROMPT_MAX_LENGTH:
        raise VideoGenerationInputError("视频提示词不能超过 2500 个字符")
    if mode not in _VIDEO_MODES:
        raise VideoGenerationInputError("视频清晰度模式无效")
    if not isinstance(duration, int) or isinstance(duration, bool):
        raise VideoGenerationInputError("视频时长必须是整数秒")
    if not _MIN_DURATION <= duration <= _MAX_DURATION:
        raise VideoGenerationInputError("视频时长必须在 3 到 15 秒之间")
    return normalized_prompt


def build_video_payload(
    prompt: str,
    mode: str,
    duration: int,
    *,
    first_frame_url: str | None = None,
) -> dict[str, Any]:
    normalized_prompt = _validate_input(prompt, mode, duration)
    if first_frame_url:
        return {
            "model": settings.dashscope_video_image_model,
            "input": {
                "prompt": normalized_prompt,
                "media": [{"type": "first_frame", "url": first_frame_url}],
            },
            "parameters": {
                "mode": mode,
                "duration": duration,
                "audio": False,
                "watermark": False,
            },
        }
    return {
        "model": settings.dashscope_video_model,
        "input": {"prompt": normalized_prompt},
        "parameters": {
            "mode": mode,
            "aspect_ratio": "16:9",
            "duration": duration,
            "audio": False,
            "watermark": False,
        },
    }


def _base_url() -> str:
    workspace_id = settings.dashscope_video_workspace_id.strip()
    if not workspace_id:
        raise VideoGenerationNotConfiguredError("视频生成业务空间未配置")
    if not workspace_id.replace("-", "").replace("_", "").isalnum():
        raise VideoGenerationNotConfiguredError("视频生成业务空间配置无效")
    return f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com"


def provider_task_url(provider_task_id: str) -> str:
    normalized = (provider_task_id or "").strip()
    if not normalized:
        raise VideoGenerationProviderError("视频任务 ID 为空")
    return f"{_base_url()}{TASK_PATH}/{quote(normalized, safe='')}"


def _headers(*, async_submission: bool) -> dict[str, str]:
    api_key = settings.dashscope_api_key.strip()
    if not api_key:
        raise VideoGenerationNotConfiguredError("DASHSCOPE_API_KEY 未配置")
    headers = {"Authorization": f"Bearer {api_key}"}
    if async_submission:
        headers.update(
            {
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
            }
        )
    return headers


def _safe_provider_message(
    output: dict[str, Any], status: VideoProviderStatus
) -> str | None:
    if status == "failed":
        if output.get("message") or output.get("error_message"):
            logger.warning("DashScope video task failed with a provider message")
        return _GENERIC_PROVIDER_ERROR
    if status == "unknown":
        return _EXPIRED_PROVIDER_ERROR
    return None


def _normalize_status(value: object) -> VideoProviderStatus:
    return {
        "PENDING": "pending",
        "RUNNING": "running",
        "SUCCEEDED": "succeeded",
        "FAILED": "failed",
        "CANCELED": "canceled",
    }.get(str(value or "").strip().upper(), "unknown")  # type: ignore[return-value]


def _parse_provider_task(body: object) -> ProviderTask:
    if not isinstance(body, dict) or not isinstance(body.get("output"), dict):
        raise VideoGenerationProviderError("视频服务返回结果格式异常")
    output = body["output"]
    provider_task_id = str(output.get("task_id") or "").strip()
    if not provider_task_id:
        raise VideoGenerationProviderError("视频服务未返回任务 ID")
    status = _normalize_status(output.get("task_status"))
    video_url = output.get("video_url")
    return ProviderTask(
        provider_task_id=provider_task_id,
        status=status,
        video_url=video_url.strip()
        if isinstance(video_url, str) and video_url.strip()
        else None,
        error_message=_safe_provider_message(output, status),
    )
def _decode_first_frame(
    first_frame: Mapping[str, object],
) -> tuple[bytes, str, str]:
    data = first_frame.get("data")
    mime_type = first_frame.get("mimeType")
    suffix = _FIRST_FRAME_MIME_SUFFIXES.get(str(mime_type or ""))
    if not isinstance(data, str) or not data.strip() or suffix is None:
        raise VideoGenerationInputError("首帧图片格式无效")
    try:
        content = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VideoGenerationInputError("首帧图片内容无效") from exc
    if not content or len(content) > _MAX_FIRST_FRAME_BYTES:
        raise VideoGenerationInputError("首帧图片不能超过 8 MB")
    return content, str(mime_type), f"first-frame{suffix}"


def _configured_file_adapter() -> VideoFileAdapter:
    try:
        return build_video_file_adapter(
            name=cast(VideoFileAdapterName, settings.video_file_adapter),
            base_url=_base_url(),
            api_key=settings.dashscope_api_key,
            timeout_seconds=settings.dashscope_video_timeout_seconds,
        )
    except VideoFileAdapterError as exc:
        raise VideoGenerationNotConfiguredError(str(exc)) from exc


def _adapter_for_ref(file_ref: str) -> VideoFileAdapter:
    configured = _configured_file_adapter()
    try:
        return adapter_for_ref(
            file_ref=file_ref,
            configured=configured,
            base_url=_base_url(),
            api_key=settings.dashscope_api_key,
            timeout_seconds=settings.dashscope_video_timeout_seconds,
        )
    except VideoFileAdapterError as exc:
        raise VideoGenerationProviderError(str(exc)) from exc


async def upload_provider_file(
    *, content: bytes, filename: str, mime_type: str
) -> str:
    try:
        return await _configured_file_adapter().upload_first_frame(
            content=content,
            filename=filename,
            mime_type=mime_type,
        )
    except VideoGenerationNotConfiguredError:
        raise
    except VideoFileAdapterError as exc:
        raise VideoGenerationProviderError(str(exc)) from exc


async def get_provider_file_url(file_ref: str) -> str:
    try:
        return await _adapter_for_ref(file_ref).resolve_url(file_ref)
    except VideoGenerationNotConfiguredError:
        raise
    except VideoFileAdapterError as exc:
        raise VideoGenerationProviderError(str(exc)) from exc


async def delete_provider_file(file_ref: str) -> bool:
    try:
        return await _adapter_for_ref(file_ref).delete(file_ref)
    except (VideoGenerationNotConfiguredError, VideoGenerationProviderError):
        return False
    except VideoFileAdapterError:
        return False


async def _cleanup_uploaded_file(file_ref: str | None) -> None:
    if file_ref:
        await delete_provider_file(file_ref)


async def create_provider_task(
    *,
    prompt: str,
    mode: str,
    duration: int,
    first_frame: Mapping[str, object] | None = None,
    source_file_ref: str | None = None,
) -> ProviderTask:
    if first_frame is not None and source_file_ref:
        raise VideoGenerationInputError("首帧图片和文件引用不能同时提供")
    uploaded_file_ref: str | None = None
    resolved_source_file_ref = source_file_ref.strip() if source_file_ref else None
    try:
        first_frame_url: str | None = None
        if first_frame is not None:
            content, mime_type, filename = _decode_first_frame(first_frame)
            uploaded_file_ref = await upload_provider_file(
                content=content,
                filename=filename,
                mime_type=mime_type,
            )
            resolved_source_file_ref = uploaded_file_ref
        if resolved_source_file_ref:
            first_frame_url = await get_provider_file_url(resolved_source_file_ref)

        base_url = _base_url()
        payload = build_video_payload(
            prompt,
            mode,
            duration,
            first_frame_url=first_frame_url,
        )
        async with httpx.AsyncClient(
            timeout=settings.dashscope_video_timeout_seconds
        ) as client:
            response = await client.post(
                f"{base_url}{VIDEO_SYNTHESIS_PATH}",
                headers=_headers(async_submission=True),
                json=payload,
            )
        if not response.is_success:
            logger.warning(
                "DashScope video submission returned HTTP %s", response.status_code
            )
            raise VideoGenerationProviderError(
                f"视频服务提交失败（HTTP {response.status_code}）"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise VideoGenerationProviderError("视频服务返回内容无法解析") from exc
        provider_task = _parse_provider_task(body)
        return ProviderTask(
            provider_task_id=provider_task.provider_task_id,
            status=provider_task.status,
            video_url=provider_task.video_url,
            error_message=provider_task.error_message,
            source_file_ref=resolved_source_file_ref,
        )
    except httpx.HTTPError as exc:
        # HTTP errors mean outcome is unknown - preserve source_file_ref
        logger.warning("DashScope video submission failed: %s", type(exc).__name__)
        raise VideoGenerationProviderError(
            "视频服务连接失败",
            outcome_unknown=True,
            source_file_ref=uploaded_file_ref,
        ) from exc
    except VideoGenerationInputError:
        # Input validation errors are definite failures - delete uploaded file
        await _cleanup_uploaded_file(uploaded_file_ref)
        raise
    except VideoGenerationNotConfiguredError:
        # Configuration errors are definite failures - delete uploaded file
        await _cleanup_uploaded_file(uploaded_file_ref)
        raise
    except VideoGenerationProviderError as exc:
        # Only delete file if this is a definite failure, not an unknown outcome
        if not exc.outcome_unknown:
            await _cleanup_uploaded_file(uploaded_file_ref)
        raise


async def poll_provider_task(provider_task_id: str) -> ProviderTask:
    headers = _headers(async_submission=False)
    try:
        async with httpx.AsyncClient(
            timeout=settings.dashscope_video_timeout_seconds
        ) as client:
            response = await client.get(provider_task_url(provider_task_id), headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("DashScope video polling failed: %s", type(exc).__name__)
        raise VideoGenerationProviderError("视频服务连接失败") from exc
    if not response.is_success:
        logger.warning(
            "DashScope video polling returned HTTP %s", response.status_code
        )
        raise VideoGenerationProviderError(
            f"视频任务查询失败（HTTP {response.status_code}）"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise VideoGenerationProviderError("视频服务返回内容无法解析") from exc
    return _parse_provider_task(body)
