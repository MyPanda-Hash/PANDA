from __future__ import annotations

import asyncio
import base64
import binascii
from io import BytesIO
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping
from urllib.parse import quote, urljoin, urlparse

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.services import minio_storage

logger = logging.getLogger(__name__)

IMAGE_GENERATION_PATH = "/api/v1/services/aigc/image-generation/generation"
TASK_PATH = "/api/v1/tasks"
IMAGE_PROMPT_MAX_LENGTH = 2500
_IMAGE_RESOLUTIONS = frozenset({"1k", "2k"})
_IMAGE_ASPECT_RATIOS = frozenset({"1:1"})
_GENERIC_PROVIDER_ERROR = "图片生成失败，请重试。"
_EXPIRED_PROVIDER_ERROR = "任务已过期或不存在"
IMAGE_CONTEXT_MAX_DATA_CHARS = 300_000
IMAGE_CONTEXT_DOWNLOAD_TIMEOUT_SECONDS = 5.0
_IMAGE_CONTEXT_MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
_IMAGE_CONTEXT_MAX_PIXELS = 20_000_000
_IMAGE_CONTEXT_MAX_REDIRECTS = 3
_IMAGE_CONTEXT_EDGES = (768, 640, 512, 384)
EDIT_SOURCE_MAX_DATA_CHARS = 6_000_000
_IMAGE_CONTEXT_QUALITIES = (80, 70, 60)
_EDIT_IMAGE_MIME_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_EDIT_IMAGE_MIMES = frozenset(_EDIT_IMAGE_MIME_BY_FORMAT.values())
_EDIT_IMAGE_MAX_BYTES = 20 * 1024 * 1024
# Durable mirror downloads accept the full upload bound (raw binary, no
# base64 inflation); 2K PNG outputs routinely exceed the 4.5MB edit-source
# download cap that is sized for inline base64 request payloads.
_IMAGE_CACHE_MAX_DOWNLOAD_BYTES = _EDIT_IMAGE_MAX_BYTES
_EDIT_IMAGE_MAX_PIXELS = 64_000_000
_EDIT_IMAGE_MIN_EDGE = 240
_EDIT_IMAGE_MAX_EDGE = 8_000
_IMAGE_CACHE_OBJECT_PREFIX = "image-generation/results/"
_MISSING_OBJECT_CODES = frozenset(
    {"NoSuchBucket", "NoSuchKey", "NoSuchObject", "ResourceNotFound"}
)
ImageOperation = Literal["generate", "edit"]

ImageProviderStatus = Literal[
    "pending", "running", "succeeded", "failed", "canceled", "unknown"
]


class ImageGenerationNotConfiguredError(RuntimeError):
    """The server has no usable DashScope image configuration."""


class ImageGenerationInputError(ValueError):
    """An image generation request violates the provider contract."""


class ImageGenerationProviderError(RuntimeError):
    """DashScope returned an unusable or unsuccessful response."""

    def __init__(self, message: str, *, outcome_unknown: bool | None = None) -> None:
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True)
class ProviderTask:
    provider_task_id: str
    status: ImageProviderStatus
    image_url: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class NormalizedEditSource:
    mime_type: str
    data: str = field(repr=False)
    width: int
    height: int

    @property
    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{self.data}"


def _allowed_context_image_url(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    hostname = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and (
            hostname == "aliyuncs.com"
            or hostname.endswith(".aliyuncs.com")
            or hostname == "klingai.com"
            or hostname.endswith(".klingai.com")
        )
    )


def encode_context_image(data: bytes) -> str | None:
    """Normalize one provider image to a bounded JPEG for model context."""
    if not data or len(data) > _IMAGE_CONTEXT_MAX_DOWNLOAD_BYTES:
        return None
    try:
        with Image.open(BytesIO(data)) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0 or width * height > _IMAGE_CONTEXT_MAX_PIXELS:
                return None
            oriented = ImageOps.exif_transpose(opened)
            if "A" in oriented.getbands():
                rgba = oriented.convert("RGBA")
                source = Image.new("RGB", rgba.size, "white")
                source.paste(rgba, mask=rgba.getchannel("A"))
            else:
                source = oriented.convert("RGB")

            for edge in _IMAGE_CONTEXT_EDGES:
                candidate = source.copy()
                candidate.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                for quality in _IMAGE_CONTEXT_QUALITIES:
                    output = BytesIO()
                    candidate.save(
                        output,
                        format="JPEG",
                        quality=quality,
                        optimize=True,
                    )
                    encoded = base64.b64encode(output.getvalue()).decode("ascii")
                    if len(encoded) <= IMAGE_CONTEXT_MAX_DATA_CHARS:
                        return encoded
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        logger.warning("Generated image could not be normalized for chat context")
    return None


async def _download_provider_image(
    image_url: str,
    *,
    max_bytes: int,
) -> bytes | None:
    current_url = (image_url or "").strip()
    if not _allowed_context_image_url(current_url):
        logger.warning(
            "Generated image URL is outside the provider allowlist"
        )
        return None
    try:
        async with httpx.AsyncClient(
            timeout=IMAGE_CONTEXT_DOWNLOAD_TIMEOUT_SECONDS
        ) as client:
            for redirect_count in range(_IMAGE_CONTEXT_MAX_REDIRECTS + 1):
                # Every hop is re-validated: an allowlisted CDN may redirect to
                # an arbitrary host, which must never be followed.
                if not _allowed_context_image_url(current_url):
                    logger.warning(
                        "Generated image redirect is outside the provider allowlist"
                    )
                    return None
                async with client.stream(
                    "GET",
                    current_url,
                    headers={"Accept": "image/*"},
                    follow_redirects=False,
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "").strip()
                        if (
                            not location
                            or redirect_count >= _IMAGE_CONTEXT_MAX_REDIRECTS
                        ):
                            logger.warning(
                                "Generated image download exceeded redirect limit"
                            )
                            return None
                        current_url = urljoin(current_url, location)
                        continue
                    if not response.is_success:
                        logger.warning(
                            "Generated image download returned HTTP %s",
                            response.status_code,
                        )
                        return None
                    content_type = response.headers.get("content-type", "")
                    if not content_type.lower().startswith("image/"):
                        logger.warning(
                            "Generated image download returned non-image content"
                        )
                        return None
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            if int(content_length) > max_bytes:
                                return None
                        except ValueError:
                            pass
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > max_bytes:
                            return None
                    return bytes(payload)
    except httpx.HTTPError as exc:
        logger.warning("Generated image download failed: %s", type(exc).__name__)
    return None


def bound_edit_source_to_schema(data: bytes) -> dict[str, str] | None:
    """Return a ChatImageContent payload for a provider result image.

    Full-size PNGs above the 6M-char schema bound are re-encoded as same-size
    JPEGs at decreasing quality until they fit; smaller images round-trip
    verbatim with their detected MIME.
    """
    if not data or len(data) > _EDIT_IMAGE_MAX_BYTES:
        return None
    try:
        with Image.open(BytesIO(data)) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0 or width * height > _EDIT_IMAGE_MAX_PIXELS:
                return None
            opened.load()
            mime_type = _EDIT_IMAGE_MIME_BY_FORMAT.get(
                str(opened.format or "").upper()
            )
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        return None
    if mime_type is None:
        return None

    def content(blob: bytes, mime: str) -> dict[str, str]:
        return {
            "type": "image",
            "data": base64.b64encode(blob).decode("ascii"),
            "mimeType": mime,
        }

    candidate = content(data, mime_type)
    if len(candidate["data"]) <= EDIT_SOURCE_MAX_DATA_CHARS:
        return candidate

    # Oversized output: flatten onto white and re-encode as JPEG, dropping
    # quality until the schema bound is met while preserving dimensions.
    try:
        with Image.open(BytesIO(data)) as opened:
            oriented = ImageOps.exif_transpose(opened)
            if "A" in oriented.getbands():
                rgba = oriented.convert("RGBA")
                flat = Image.new("RGB", rgba.size, "white")
                flat.paste(rgba, mask=rgba.getchannel("A"))
            else:
                flat = oriented.convert("RGB")
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        return None
    for quality in (92, 85, 75, 65, 55, 45, 35, 25):
        output = BytesIO()
        try:
            flat.save(output, format="JPEG", quality=quality, optimize=True)
        except OSError:
            return None
        candidate = content(output.getvalue(), "image/jpeg")
        if len(candidate["data"]) <= EDIT_SOURCE_MAX_DATA_CHARS:
            return candidate
    return None


def encode_raw_image_content(data: bytes) -> dict[str, str] | None:

    """Return a full-resolution image content dict (data + real mime).

    Unlike the provider edit-source path this preserves bytes verbatim and
    detects the actual image format instead of trusting a URL extension.
    """
    if not data or len(data) > _EDIT_IMAGE_MAX_BYTES:
        return None
    try:
        with Image.open(BytesIO(data)) as opened:
            mime_type = _EDIT_IMAGE_MIME_BY_FORMAT.get(
                str(opened.format or "").upper()
            )
        if mime_type is None:
            return None
        encoded = base64.b64encode(data).decode("ascii")
        return {"type": "image", "data": encoded, "mimeType": mime_type}
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


def rebound_to_schema(source: Mapping[str, object]) -> dict[str, str] | None:
    """Ensure an encoded image dict fits the ChatImageContent data bound.

    Used as a last guard on provider downloads; oversized payloads are
    re-encoded smaller instead of failing schema validation downstream.
    """
    data = source.get("data")
    if not isinstance(data, str) or not data:
        return None
    if len(data) <= EDIT_SOURCE_MAX_DATA_CHARS:
        mime = str(source.get("mimeType") or "")
        return {
            "type": "image",
            "data": data,
            "mimeType": mime or "image/jpeg",
        }
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None
    return bound_edit_source_to_schema(raw)


def encode_edit_source_image(data: bytes) -> dict[str, str] | None:


    """Return a full-resolution provider image for a subsequent edit."""
    if not data or len(data) > _EDIT_IMAGE_MAX_BYTES:
        return None
    try:
        with Image.open(BytesIO(data)) as opened:
            mime_type = _EDIT_IMAGE_MIME_BY_FORMAT.get(
                str(opened.format or "").upper()
            )
        if mime_type is None:
            return None
        encoded = base64.b64encode(data).decode("ascii")
        source = {"type": "image", "data": encoded, "mimeType": mime_type}
        normalize_edit_source(source)
        return source
    except (
        ImageGenerationInputError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ):
        return None

def _image_cache_key(owner_user_id: str, task_id: str) -> str:
    """Return the MinIO object key for one durable generated-image entry."""
    return f"{_IMAGE_CACHE_OBJECT_PREFIX}{owner_user_id}/{task_id}"


def _image_content_type(data: bytes) -> str:
    """Best-effort MIME detection for mirrored provider images."""
    try:
        with Image.open(BytesIO(data)) as opened:
            detected = _EDIT_IMAGE_MIME_BY_FORMAT.get(
                str(opened.format or "").upper()
            )
            if detected is not None:
                return detected
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        pass
    return "application/octet-stream"


def image_cache_is_available() -> bool:
    """Return True when the durable object-store image cache is configured."""
    return minio_storage.is_minio_enabled()


def cache_image_bytes(
    *,
    owner_user_id: str,
    task_id: str,
    data: bytes,
    content_type: str,
) -> str:
    """Persist one generated image to MinIO and return its ``minio://`` URI.

    The URI is the durable reference stored on the task; display URLs are
    derived from it later with a short-lived presign. Objects land in the
    main bucket (never the routinely-emptied temp bucket) so an uploaded
    result remains fetchable for later edit/context reuse.
    """
    if not minio_storage.is_minio_enabled():
        raise RuntimeError("MinIO 未配置或未启用")
    return minio_storage.upload_bytes(
        content=data,
        object_key=_image_cache_key(owner_user_id, task_id),
        content_type=content_type,
    )


def cached_image_uri_exists(minio_uri: str | None) -> bool:
    """Return True when the cached object is still present in MinIO."""
    if not minio_uri or not minio_storage.is_minio_uri(minio_uri):
        return False
    try:
        return minio_storage.object_exists(minio_uri=minio_uri)
    except Exception as exc:
        logger.warning(
            "Generated image cache stat failed uri=%s: %s",
            minio_uri,
            type(exc).__name__,
        )
        return False



def delete_cached_image(minio_uri: str | None) -> bool:
    """Best-effort removal of one cached image object."""
    if not minio_uri or not minio_storage.is_minio_uri(minio_uri):
        return False
    try:
        minio_storage.delete_object(minio_uri=minio_uri)
        return True
    except Exception as exc:
        if getattr(exc, "code", None) in _MISSING_OBJECT_CODES:
            return True
        logger.warning(
            "Generated image cache delete failed uri=%s: %s",
            minio_uri,
            type(exc).__name__,
        )
        return False



def read_cached_image_bytes(image_cache_uri: str | None) -> bytes | None:
    """Return the cached image bytes for a ``minio://`` URI, if present."""
    if not image_cache_uri or not minio_storage.is_minio_uri(image_cache_uri):
        return None
    try:
        return minio_storage.download_bytes(minio_uri=image_cache_uri)
    except Exception as exc:
        logger.warning(
            "Generated image cache read failed uri=%s: %s",
            image_cache_uri,
            type(exc).__name__,
        )
        return None


async def read_cached_image_bytes_async(image_cache_uri: str | None) -> bytes | None:
    """Read cached image bytes without blocking the event loop."""
    return await asyncio.to_thread(read_cached_image_bytes, image_cache_uri)


def _cache_provider_image(owner_user_id: str, task_id: str, payload: bytes) -> str:
    return cache_image_bytes(
        owner_user_id=owner_user_id,
        task_id=task_id,
        data=payload,
        content_type=_image_content_type(payload),
    )


async def mirror_provider_image(
    *,
    owner_user_id: str,
    task_id: str,
    image_url: str,
) -> str | None:
    """Download a still-live provider image into the durable cache.

    Returns the ``minio://`` URI on success, else None. Trusted (allowlisted)
    provider hosts only; other URLs return None without a network call.
    """
    payload = await _download_provider_image(
        image_url,
        max_bytes=_IMAGE_CACHE_MAX_DOWNLOAD_BYTES,
    )
    if payload is None:
        return None
    try:
        return await asyncio.to_thread(
            _cache_provider_image,
            owner_user_id,
            task_id,
            payload,
        )
    except Exception as exc:
        logger.warning(
            "Generated image cache upload failed task=%s: %s",
            task_id,
            type(exc).__name__,
        )
        return None


async def fetch_context_image_data(image_url: str) -> str | None:
    """Download a trusted provider image and return bounded JPEG base64."""
    payload = await _download_provider_image(
        image_url,
        max_bytes=_IMAGE_CONTEXT_MAX_DOWNLOAD_BYTES,
    )
    if payload is None:
        return None
    return await asyncio.to_thread(encode_context_image, payload)


async def fetch_edit_source_image(image_url: str) -> dict[str, str] | None:
    """Download a trusted provider image without changing its pixels.

    The download bound is the full 20MB upload limit, not the inline
    base64 request cap: a 2K PNG result routinely exceeds 4.5MB and must be
    accepted here before any schema-bound compression happens downstream.
    """
    payload = await _download_provider_image(
        image_url,
        max_bytes=_EDIT_IMAGE_MAX_BYTES,
    )
    if payload is None:
        return None
    return await asyncio.to_thread(encode_edit_source_image, payload)


def _validate_input(prompt: str, resolution: str, aspect_ratio: str) -> str:
    normalized_prompt = (prompt or "").strip()
    if not normalized_prompt:
        raise ImageGenerationInputError("图片提示词不能为空")
    if len(normalized_prompt) > IMAGE_PROMPT_MAX_LENGTH:
        raise ImageGenerationInputError("图片提示词不能超过 2500 个字符")
    if resolution not in _IMAGE_RESOLUTIONS:
        raise ImageGenerationInputError("图片分辨率必须是 1k 或 2k")
    if aspect_ratio not in _IMAGE_ASPECT_RATIOS:
        raise ImageGenerationInputError("图片比例目前仅支持 1:1")
    return normalized_prompt


def normalize_edit_source(
    image: Mapping[str, object],
) -> NormalizedEditSource:
    raw_data = image.get("data")
    declared_mime = str(image.get("mimeType") or image.get("mime_type") or "").lower()
    if declared_mime not in _EDIT_IMAGE_MIMES:
        raise ImageGenerationInputError("待编辑图片只支持 JPEG、PNG 或 WebP")
    if not isinstance(raw_data, str) or not raw_data.strip():
        raise ImageGenerationInputError("待编辑图片内容为空")
    try:
        content = base64.b64decode(raw_data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageGenerationInputError("待编辑图片编码无效") from exc
    if not content or len(content) > _EDIT_IMAGE_MAX_BYTES:
        raise ImageGenerationInputError("待编辑图片不能超过 20 MB")

    try:
        with Image.open(BytesIO(content)) as opened:
            actual_mime = _EDIT_IMAGE_MIME_BY_FORMAT.get(str(opened.format or "").upper())
            if actual_mime is None or actual_mime != declared_mime:
                raise ImageGenerationInputError("待编辑图片格式与内容不一致")
            width, height = opened.size
            if not (
                _EDIT_IMAGE_MIN_EDGE <= width <= _EDIT_IMAGE_MAX_EDGE
                and _EDIT_IMAGE_MIN_EDGE <= height <= _EDIT_IMAGE_MAX_EDGE
            ):
                raise ImageGenerationInputError("待编辑图片宽高必须在 240 到 8000 像素之间")
            if width * height > _EDIT_IMAGE_MAX_PIXELS:
                raise ImageGenerationInputError("待编辑图片像素数量超出限制")
            ratio = width / height
            if ratio < 1 / 8 or ratio > 8:
                raise ImageGenerationInputError("待编辑图片宽高比必须在 1:8 到 8:1 之间")
            opened.load()
            has_alpha = "A" in opened.getbands() or "transparency" in opened.info
            if not has_alpha:
                return NormalizedEditSource(
                    mime_type=actual_mime,
                    data=raw_data,
                    width=width,
                    height=height,
                )

            oriented = ImageOps.exif_transpose(opened).convert("RGBA")
            background = Image.new("RGB", oriented.size, "white")
            background.paste(oriented, mask=oriented.getchannel("A"))
            output = BytesIO()
            background.save(output, format="PNG", optimize=True)
            provider_content = output.getvalue()
            if len(provider_content) > _EDIT_IMAGE_MAX_BYTES:
                raise ImageGenerationInputError("待编辑图片规范化后超过 20 MB")
            return NormalizedEditSource(
                mime_type="image/png",
                data=base64.b64encode(provider_content).decode("ascii"),
                width=background.width,
                height=background.height,
            )
    except ImageGenerationInputError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ImageGenerationInputError("待编辑图片内容无效") from exc


class WanImageAdapter:
    model = "wan2.7-image-pro"
    requires_source_url = False
    normalize_source = staticmethod(normalize_edit_source)

    @staticmethod
    def output_aspect_ratio(source: NormalizedEditSource) -> Literal["source"]:
        return "source"

    def build_payload(
        self,
        prompt: str,
        resolution: str,
        source: NormalizedEditSource,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "input": {"messages": [{
                "role": "user",
                "content": [{"image": source.data_url}, {"text": prompt}],
            }]},
            "parameters": {"size": resolution.upper(), "n": 1, "watermark": False},
        }


class KlingImageAdapter:
    model = "kling/kling-v3-image-generation"
    requires_source_url = True

    @staticmethod
    def normalize_source(image: Mapping[str, object]) -> NormalizedEditSource:
        source = normalize_edit_source(image)
        content = base64.b64decode(source.data)
        with Image.open(BytesIO(content)) as opened:
            # Preserve opaque JPEG/PNG bytes unless EXIF orientation needs baking.
            if source.mime_type == "image/webp" or opened.getexif().get(274, 1) in range(2, 9):
                oriented = ImageOps.exif_transpose(opened)
                output = BytesIO()
                oriented.convert("RGB").save(output, format="PNG")
                content = output.getvalue()
                source = NormalizedEditSource(
                    mime_type="image/png",
                    data=base64.b64encode(content).decode("ascii"),
                    width=oriented.width,
                    height=oriented.height,
                )
        if min(source.width, source.height) < 300:
            raise ImageGenerationInputError("Kling 待编辑图片宽高必须在 300 到 8000 像素之间")
        if not 0.4 <= source.width / source.height <= 2.5:
            raise ImageGenerationInputError("Kling 待编辑图片宽高比必须在 1:2.5 到 2.5:1 之间")
        if len(content) > 10 * 1024 * 1024:
            raise ImageGenerationInputError("Kling 待编辑图片规范化后不能超过 10 MB")
        return source

    @staticmethod
    def output_aspect_ratio(
        source: NormalizedEditSource,
    ) -> Literal["1:1", "16:9", "9:16"]:
        # Nearest supported ratio on a log scale; ties preserve the square.
        if source.width * 3 > source.height * 4:
            return "16:9"
        if source.height * 3 > source.width * 4:
            return "9:16"
        return "1:1"

    def build_payload(
        self,
        prompt: str,
        resolution: str,
        source: NormalizedEditSource,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        parsed = urlparse(source_url or "")
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ImageGenerationInputError("Kling 图片编辑需要有效的 HTTPS 源图地址")
        return {
            "model": self.model,
            "input": {"messages": [{
                "role": "user",
                "content": [{"image": source_url}, {"text": prompt}],
            }]},
            "parameters": {
                "resolution": resolution,
                "aspect_ratio": self.output_aspect_ratio(source),
                "n": 1,
                "watermark": False,
            },
        }


_EDIT_ADAPTERS = {
    adapter.model: adapter for adapter in (KlingImageAdapter(), WanImageAdapter())
}


def _edit_adapter(model: str) -> KlingImageAdapter | WanImageAdapter:
    adapter = _EDIT_ADAPTERS.get(model.strip())
    if adapter is None:
        raise ImageGenerationNotConfiguredError(
            "图片编辑模型仅支持 kling/kling-v3-image-generation 或 wan2.7-image-pro"
        )
    return adapter


def edit_output_aspect_ratio(
    model: str, source_image: Mapping[str, object],
) -> Literal["1:1", "16:9", "9:16", "source"]:
    adapter = _edit_adapter(model)
    return adapter.output_aspect_ratio(adapter.normalize_source(source_image))


def build_image_payload(
    prompt: str,
    resolution: str,
    *,
    aspect_ratio: str = "1:1",
    operation: ImageOperation = "generate",
    source_image: Mapping[str, object] | None = None,
    model: str | None = None,
    source_image_url: str | None = None,
) -> dict[str, Any]:
    normalized_prompt = _validate_input(prompt, resolution, aspect_ratio)
    if operation == "generate":
        if source_image is not None:
            raise ImageGenerationInputError("文生图任务不能包含待编辑图片")
        return {
            "model": model if model is not None else settings.dashscope_image_model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": normalized_prompt}],
                    }
                ]
            },
            "parameters": {
                "n": 1,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
            },
        }
    if operation != "edit":
        raise ImageGenerationInputError("不支持的图片任务类型")
    if source_image is None:
        raise ImageGenerationInputError("图片编辑任务必须包含一张待编辑图片")
    adapter = _edit_adapter(
        model if model is not None else settings.dashscope_image_edit_model
    )
    return adapter.build_payload(
        normalized_prompt,
        resolution,
        adapter.normalize_source(source_image),
        source_image_url,
    )


def _workspace_id() -> str:
    workspace_id = (
        settings.dashscope_image_workspace_id.strip()
        or settings.dashscope_video_workspace_id.strip()
    )
    if not workspace_id:
        raise ImageGenerationNotConfiguredError("图片生成业务空间未配置")
    if not workspace_id.replace("-", "").replace("_", "").isalnum():
        raise ImageGenerationNotConfiguredError("图片生成业务空间配置无效")
    return workspace_id


def _base_url() -> str:
    return f"https://{_workspace_id()}.cn-beijing.maas.aliyuncs.com"


def provider_task_url(provider_task_id: str) -> str:
    normalized = (provider_task_id or "").strip()
    if not normalized:
        raise ImageGenerationProviderError("图片任务 ID 为空")
    return f"{_base_url()}{TASK_PATH}/{quote(normalized, safe='')}"


def _headers(*, async_submission: bool) -> dict[str, str]:
    api_key = settings.dashscope_api_key.strip()
    if not api_key:
        raise ImageGenerationNotConfiguredError("DASHSCOPE_API_KEY 未配置")
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
    output: dict[str, Any], status: ImageProviderStatus
) -> str | None:
    if status == "failed":
        if output.get("message") or output.get("error_message"):
            logger.warning("DashScope image task failed with a provider message")
        return _GENERIC_PROVIDER_ERROR
    if status == "unknown":
        return _EXPIRED_PROVIDER_ERROR
    return None


def _normalize_status(value: object) -> ImageProviderStatus:
    status_by_name: dict[str, ImageProviderStatus] = {
        "PENDING": "pending",
        "RUNNING": "running",
        "SUCCEEDED": "succeeded",
        "FAILED": "failed",
        "CANCELED": "canceled",
    }
    return status_by_name.get(str(value or "").strip().upper(), "unknown")


def _extract_image_url(output: dict[str, Any]) -> str | None:
    choices = output.get("choices")
    if not isinstance(choices, list):
        return None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            image_url = item.get("image")
            if isinstance(image_url, str) and image_url.strip():
                return image_url.strip()
    return None


def _parse_provider_task(body: object) -> ProviderTask:
    if not isinstance(body, dict) or not isinstance(body.get("output"), dict):
        raise ImageGenerationProviderError("图片服务返回结果格式异常")
    output = body["output"]
    provider_task_id = str(output.get("task_id") or "").strip()
    if not provider_task_id:
        raise ImageGenerationProviderError("图片服务未返回任务 ID")
    status = _normalize_status(output.get("task_status"))
    return ProviderTask(
        provider_task_id=provider_task_id,
        status=status,
        image_url=_extract_image_url(output) if status == "succeeded" else None,
        error_message=_safe_provider_message(output, status),
    )


async def create_provider_task(
    *,
    prompt: str,
    resolution: str,
    aspect_ratio: str = "1:1",
    operation: ImageOperation = "generate",
    source_image: Mapping[str, object] | None = None,
    model: str | None = None,
) -> ProviderTask:
    if operation == "edit" and settings.linkmind_enabled:
        raise ImageGenerationNotConfiguredError(
            "图片编辑在当前部署模式下不可用"
        )
    if operation == "edit":
        normalized_prompt = _validate_input(prompt, resolution, aspect_ratio)
        if source_image is None:
            raise ImageGenerationInputError("图片编辑任务必须包含一张待编辑图片")
        adapter = _edit_adapter(
            model if model is not None else settings.dashscope_image_edit_model
        )
        source = await asyncio.to_thread(adapter.normalize_source, source_image)
        base_url = _base_url()
        headers = _headers(async_submission=True)
        source_url = None
        if adapter.requires_source_url:
            from app.services import image_edit_source

            try:
                source_url = await image_edit_source.publish_source(
                    content=base64.b64decode(source.data),
                    mime_type=source.mime_type,
                )
            except image_edit_source.ImageEditSourceNotConfiguredError as exc:
                raise ImageGenerationNotConfiguredError(str(exc)) from exc
            except image_edit_source.ImageEditSourcePublishError as exc:
                raise ImageGenerationProviderError(str(exc), outcome_unknown=False) from exc
        payload = adapter.build_payload(normalized_prompt, resolution, source, source_url)
    else:
        payload = build_image_payload(
            prompt,
            resolution,
            aspect_ratio=aspect_ratio,
            operation=operation,
            source_image=source_image,
            model=model,
        )
        base_url = _base_url()
        headers = _headers(async_submission=True)
    try:
        async with httpx.AsyncClient(
            timeout=settings.dashscope_image_timeout_seconds
        ) as client:
            response = await client.post(
                f"{base_url}{IMAGE_GENERATION_PATH}",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.warning("DashScope image submission failed: %s", type(exc).__name__)
        raise ImageGenerationProviderError("图片服务连接失败") from exc
    if not response.is_success:
        logger.warning(
            "DashScope image submission returned HTTP %s", response.status_code
        )
        raise ImageGenerationProviderError(
            f"图片服务提交失败（HTTP {response.status_code}）"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise ImageGenerationProviderError("图片服务返回内容无法解析") from exc
    return _parse_provider_task(body)


async def poll_provider_task(provider_task_id: str) -> ProviderTask:
    headers = _headers(async_submission=False)
    try:
        async with httpx.AsyncClient(
            timeout=settings.dashscope_image_timeout_seconds
        ) as client:
            response = await client.get(
                provider_task_url(provider_task_id), headers=headers
            )
    except httpx.HTTPError as exc:
        logger.warning("DashScope image polling failed: %s", type(exc).__name__)
        raise ImageGenerationProviderError("图片服务连接失败") from exc
    if not response.is_success:
        logger.warning("DashScope image polling returned HTTP %s", response.status_code)
        raise ImageGenerationProviderError(
            f"图片任务查询失败（HTTP {response.status_code}）"
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise ImageGenerationProviderError("图片服务返回内容无法解析") from exc
    return _parse_provider_task(body)
