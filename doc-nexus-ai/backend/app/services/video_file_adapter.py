from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, Protocol
from urllib.parse import quote, urlparse

import httpx

from app.services import minio_storage

logger = logging.getLogger(__name__)

_LOCAL_MINIO_HOSTS = {"localhost", "minio", "127.0.0.1", "::1", "0.0.0.0"}
VideoFileAdapterName = Literal["bailian", "minio"]
BAILIAN_FILE_REF_PREFIX = "bailian://"


class VideoFileAdapterError(RuntimeError):
    """A configured first-frame file adapter failed safely."""


class VideoFileAdapter(Protocol):
    async def upload_first_frame(
        self, *, content: bytes, filename: str, mime_type: str
    ) -> str: ...

    async def resolve_url(self, file_ref: str) -> str: ...

    async def delete(self, file_ref: str) -> bool: ...


@dataclass(frozen=True)
class BailianVideoFileAdapter:
    base_url: str
    api_key: str
    timeout_seconds: float

    def _headers(self) -> dict[str, str]:
        if not self.api_key.strip():
            raise VideoFileAdapterError("DASHSCOPE_API_KEY 未配置")
        return {"Authorization": f"Bearer {self.api_key.strip()}"}

    @staticmethod
    def _file_id(file_ref: str) -> str:
        normalized = (file_ref or "").strip()
        if normalized.startswith(BAILIAN_FILE_REF_PREFIX):
            normalized = normalized[len(BAILIAN_FILE_REF_PREFIX) :]
        if not normalized or "://" in normalized:
            raise VideoFileAdapterError("百炼首帧文件 ID 无效")
        return normalized

    def _file_url(self, file_ref: str) -> str:
        return f"{self.base_url}/api/v1/files/{quote(self._file_id(file_ref), safe='')}"

    async def upload_first_frame(
        self, *, content: bytes, filename: str, mime_type: str
    ) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/files",
                    headers=self._headers(),
                    files={"files": (filename, content, mime_type)},
                )
        except httpx.HTTPError as exc:
            logger.warning("Bailian first-frame upload failed: %s", type(exc).__name__)
            raise VideoFileAdapterError("视频首帧上传失败") from exc
        if not response.is_success:
            logger.warning(
                "Bailian first-frame upload returned HTTP %s", response.status_code
            )
            raise VideoFileAdapterError("视频首帧上传失败")
        try:
            body = response.json()
        except ValueError as exc:
            raise VideoFileAdapterError("视频首帧上传响应无法解析") from exc
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            raise VideoFileAdapterError("视频首帧上传响应格式异常")
        uploaded = body["data"].get("uploaded_files")
        if not isinstance(uploaded, list) or not uploaded or not isinstance(uploaded[0], dict):
            raise VideoFileAdapterError("视频首帧上传失败")
        file_id = str(uploaded[0].get("file_id") or "").strip()
        if not file_id:
            raise VideoFileAdapterError("视频首帧上传未返回文件 ID")
        return f"{BAILIAN_FILE_REF_PREFIX}{file_id}"

    async def resolve_url(self, file_ref: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    self._file_url(file_ref), headers=self._headers()
                )
        except httpx.HTTPError as exc:
            logger.warning("Bailian first-frame lookup failed: %s", type(exc).__name__)
            raise VideoFileAdapterError("视频首帧查询失败") from exc
        if not response.is_success:
            logger.warning(
                "Bailian first-frame lookup returned HTTP %s", response.status_code
            )
            raise VideoFileAdapterError("视频首帧查询失败")
        try:
            body = response.json()
        except ValueError as exc:
            raise VideoFileAdapterError("视频首帧查询响应无法解析") from exc
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            raise VideoFileAdapterError("视频首帧查询响应格式异常")
        url = body["data"].get("url")
        if not isinstance(url, str) or not url.strip():
            raise VideoFileAdapterError("视频首帧查询未返回文件地址")
        return url.strip()

    async def delete(self, file_ref: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.delete(
                    self._file_url(file_ref), headers=self._headers()
                )
        except httpx.HTTPError as exc:
            logger.warning("Bailian first-frame cleanup failed: %s", type(exc).__name__)
            return False
        if response.status_code in {200, 204, 404}:
            return True
        logger.warning(
            "Bailian first-frame cleanup returned HTTP %s", response.status_code
        )
        return False


@dataclass(frozen=True)
class MinioVideoFileAdapter:
    async def upload_first_frame(
        self, *, content: bytes, filename: str, mime_type: str
    ) -> str:
        def upload() -> str:
            config = minio_storage.load_minio_settings()
            if config is None:
                raise VideoFileAdapterError("MinIO 未配置或未启用")
            suffix = PurePosixPath(filename).suffix.lower() or ".bin"
            object_key = (
                "video-generation/first-frames/"
                f"{secrets.token_urlsafe(18)}{suffix}"
            )
            return minio_storage.upload_bytes(
                content=content,
                object_key=object_key,
                content_type=mime_type,
                bucket=config.temp_bucket,
            )

        try:
            return await asyncio.to_thread(upload)
        except VideoFileAdapterError:
            raise
        except Exception as exc:
            logger.warning("MinIO first-frame upload failed: %s", type(exc).__name__)
            raise VideoFileAdapterError("视频首帧上传失败") from exc

    async def resolve_url(self, file_ref: str) -> str:
        def presign() -> str:
            config = minio_storage.load_minio_settings()
            if config is None:
                raise VideoFileAdapterError("MinIO 未配置或未启用")
            public_url = urlparse(config.show_url)
            if (
                public_url.scheme not in {"http", "https"}
                or not public_url.hostname
                or public_url.hostname.lower() in _LOCAL_MINIO_HOSTS
            ):
                raise VideoFileAdapterError(
                    "MinIO 展示地址必须是百炼可访问的公网 URL"
                )
            return minio_storage.presign_get_url(
                minio_uri=file_ref, expire_minutes=config.presign_minutes
            )

        try:
            return await asyncio.to_thread(presign)
        except VideoFileAdapterError:
            raise
        except Exception as exc:
            logger.warning("MinIO first-frame signing failed: %s", type(exc).__name__)
            raise VideoFileAdapterError("视频首帧地址生成失败") from exc

    async def delete(self, file_ref: str) -> bool:
        try:
            await asyncio.to_thread(minio_storage.delete_object, minio_uri=file_ref)
            return True
        except Exception as exc:
            logger.warning("MinIO first-frame cleanup failed: %s", type(exc).__name__)
            return False


def build_video_file_adapter(
    *,
    name: VideoFileAdapterName,
    base_url: str,
    api_key: str,
    timeout_seconds: float,
) -> VideoFileAdapter:
    normalized = name.strip().lower()
    if normalized == "bailian":
        return BailianVideoFileAdapter(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
    if normalized == "minio":
        return MinioVideoFileAdapter()
    raise VideoFileAdapterError(
        "DOC_NEXUS_VIDEO_FILE_ADAPTER 只能是 bailian 或 minio"
    )


def adapter_for_ref(
    *,
    file_ref: str,
    configured: VideoFileAdapter,
    base_url: str,
    api_key: str,
    timeout_seconds: float,
) -> VideoFileAdapter:
    if file_ref.startswith("minio://"):
        return MinioVideoFileAdapter()
    if file_ref.startswith(BAILIAN_FILE_REF_PREFIX) or "://" not in file_ref:
        return BailianVideoFileAdapter(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
    return configured
