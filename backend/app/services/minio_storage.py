"""MinIO object storage helpers (SaaS-compatible upload + presigned GET)."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urlparse, urlunparse, urlsplit, urlunsplit

from app.core.config import settings

logger = logging.getLogger(__name__)

_MINIO_URI_PREFIX = "minio://"


@dataclass(frozen=True)
class MinioSettings:
    endpoint_url: str
    show_url: str
    access_key: str
    secret_key: str
    bucket: str
    temp_bucket: str
    presign_minutes: int


def _resolve(key: str, fallback: str = "") -> str:
    """Resolve Compose-owned MinIO settings before legacy DB overrides."""
    import os

    env_value = os.environ.get(f"DOC_NEXUS_{key.upper()}", "").strip()
    if env_value:
        return env_value
    from app.repositories.system_config import resolve

    return (resolve(key) or fallback or "").strip()


def load_minio_settings() -> MinioSettings | None:
    """Return MinIO settings when enabled and credentials are present; else None."""
    enabled = (
        _resolve("minio_enabled") or str(getattr(settings, "minio_enabled", "") or "")
    ).lower()
    endpoint = _resolve("minio_url") or settings.minio_url
    access_key = _resolve("minio_access_key") or settings.minio_access_key
    secret_key = _resolve("minio_secret_key") or settings.minio_secret_key
    bucket = _resolve("minio_bucket") or settings.minio_bucket
    if enabled in ("0", "false", "no", "off"):
        return None
    if not (endpoint and access_key and secret_key and bucket):
        return None
    try:
        minutes = int(
            _resolve("minio_presign_minutes")
            or str(settings.minio_presign_minutes)
            or "120"
        )
    except ValueError:
        minutes = 120
    return MinioSettings(
        endpoint_url=endpoint.rstrip("/"),
        show_url=(_resolve("minio_show_url") or settings.minio_show_url or "").rstrip(
            "/"
        ),
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
        temp_bucket=_resolve("minio_temp_bucket")
        or settings.minio_temp_bucket
        or bucket,
        presign_minutes=max(5, minutes),
    )


def is_minio_enabled() -> bool:
    return load_minio_settings() is not None


def is_minio_uri(value: str | None) -> bool:
    return bool(value) and str(value).startswith(_MINIO_URI_PREFIX)


def parse_minio_uri(uri: str) -> tuple[str, str]:
    """Parse minio://bucket/object/key -> (bucket, object_key)."""
    raw = uri[len(_MINIO_URI_PREFIX) :]
    bucket, _, object_key = raw.partition("/")
    if not bucket or not object_key:
        raise ValueError(f"invalid minio uri: {uri}")
    return bucket, object_key


def build_minio_uri(bucket: str, object_key: str) -> str:
    return f"{_MINIO_URI_PREFIX}{bucket}/{object_key.lstrip('/')}"


def object_key_for_version(
    *, space_id: str, document_id: str, version_id: str, filename: str
) -> str:
    from pathlib import Path

    ext = Path(filename).suffix.lower() or ".bin"
    return f"doc-nexus/{space_id}/{document_id}/{version_id}{ext}"


def _client(cfg: MinioSettings):
    from minio import Minio

    parsed = urlparse(
        cfg.endpoint_url if "://" in cfg.endpoint_url else f"http://{cfg.endpoint_url}"
    )
    host = parsed.netloc or parsed.path
    secure = parsed.scheme == "https"
    return Minio(
        host, access_key=cfg.access_key, secret_key=cfg.secret_key, secure=secure
    )


def _object_location(
    cfg: MinioSettings,
    *,
    minio_uri: str | None = None,
    object_key: str | None = None,
    bucket: str | None = None,
) -> tuple[str, str]:
    if minio_uri:
        return parse_minio_uri(minio_uri)
    if object_key:
        return bucket or cfg.bucket, object_key
    raise ValueError("object_key or minio_uri required")


def stat_object(
    *,
    minio_uri: str | None = None,
    object_key: str | None = None,
    bucket: str | None = None,
):
    """Return MinIO's bounded metadata object without reading content."""
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    target_bucket, target_key = _object_location(
        cfg, minio_uri=minio_uri, object_key=object_key, bucket=bucket
    )
    return _client(cfg).stat_object(target_bucket, target_key)


def open_object(
    *,
    minio_uri: str | None = None,
    object_key: str | None = None,
    bucket: str | None = None,
):
    """Open a streaming MinIO response; the caller must close and release it."""
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    target_bucket, target_key = _object_location(
        cfg, minio_uri=minio_uri, object_key=object_key, bucket=bucket
    )
    return _client(cfg).get_object(target_bucket, target_key)


def download_to_path(
    *,
    minio_uri: str | None = None,
    object_key: str | None = None,
    bucket: str | None = None,
    path: Path,
    chunk_size: int = 4 * 1024 * 1024,
) -> None:
    """Stream an object to a caller-owned path without buffering it in memory."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    response = open_object(minio_uri=minio_uri, object_key=object_key, bucket=bucket)
    try:
        with path.open("wb") as stream:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                stream.write(chunk)
    finally:
        response.close()
        response.release_conn()


def initiate_multipart_upload(
    *,
    object_key: str,
    bucket: str | None = None,
    metadata: dict[str, str] | None = None,
) -> str:
    """Start one private multipart upload and return its opaque upload ID."""
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    ensure_configured_buckets(cfg)
    target_bucket = bucket or cfg.temp_bucket
    headers = {str(key): str(value) for key, value in (metadata or {}).items()}
    return _client(cfg)._create_multipart_upload(target_bucket, object_key, headers)


def list_multipart_parts(
    *,
    object_key: str,
    upload_id: str,
    bucket: str | None = None,
) -> list[dict[str, int | str]]:
    """List the server's current multipart manifest."""
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    target_bucket = bucket or cfg.temp_bucket
    result = _client(cfg)._list_parts(
        target_bucket, object_key, upload_id, max_parts=1000
    )
    rows: list[dict[str, int | str]] = []
    for item in result.parts:
        rows.append(
            {
                "part_number": int(item.part_number),
                "size_bytes": int(item.size),
                "etag": str(item.etag),
            }
        )
    return rows


def list_multipart_uploads(
    *,
    bucket: str | None = None,
    prefix: str = "",
    max_uploads: int = 1000,
) -> list[dict[str, object]]:
    """List bounded multipart uploads under one caller-supplied prefix."""
    if not prefix:
        raise ValueError("multipart listing requires a prefix")
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    target_bucket = bucket or cfg.temp_bucket
    result = _client(cfg)._list_multipart_uploads(
        target_bucket,
        prefix=prefix,
        max_uploads=max(1, min(max_uploads, 1000)),
    )
    rows: list[dict[str, object]] = []
    for item in result.uploads:
        rows.append(
            {
                "object_key": str(item.object_name),
                "upload_id": str(item.upload_id or ""),
                "initiated": item.initiated_time,
            }
        )
    return rows


def complete_multipart_upload(
    *,
    object_key: str,
    upload_id: str,
    parts: Iterable[dict[str, int | str]],
    bucket: str | None = None,
) -> None:
    """Finalize a multipart object using the server-listed parts."""
    from minio.datatypes import Part

    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    target_bucket = bucket or cfg.temp_bucket
    manifest = [Part(int(item["part_number"]), str(item["etag"])) for item in parts]
    _client(cfg)._complete_multipart_upload(
        target_bucket, object_key, upload_id, manifest
    )


def abort_multipart_upload(
    *,
    object_key: str,
    upload_id: str,
    bucket: str | None = None,
) -> None:
    """Abort one private multipart upload; callers scope the key to their row."""
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    target_bucket = bucket or cfg.temp_bucket
    _client(cfg)._abort_multipart_upload(target_bucket, object_key, upload_id)


def copy_object(
    *,
    source_bucket: str,
    source_key: str,
    target_bucket: str,
    target_key: str,
) -> None:
    """Copy a completed object server-side without loading bytes in Python."""
    from minio.commonconfig import CopySource

    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    _client(cfg).copy_object(
        target_bucket, target_key, CopySource(source_bucket, source_key)
    )


def presign_upload_part(
    *,
    bucket: str,
    object_key: str,
    upload_id: str,
    part_number: int,
    expires_seconds: int,
    public_url: str = "",
) -> tuple[str, dict[str, str]]:
    """Sign only one PUT part, using either the internal or configured public host."""
    if part_number < 1:
        raise ValueError("part_number must be positive")
    if expires_seconds <= 0:
        raise ValueError("expires_seconds must be positive")
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")

    public_origin = (public_url or "").strip()
    target_cfg = cfg
    public = urlsplit(public_origin)
    if public_origin:
        if (
            public.scheme not in {"http", "https"}
            or not public.netloc
            or public.username
            or public.password
            or public.query
            or public.fragment
        ):
            raise ValueError("document_upload_public_url must be an HTTP(S) origin")
        target_cfg = replace(
            cfg,
            endpoint_url=urlunsplit((public.scheme, public.netloc, "", "", "")),
        )

    from datetime import timedelta

    signed = _client(target_cfg).get_presigned_url(
        "PUT",
        bucket,
        object_key,
        expires=timedelta(seconds=expires_seconds),
        extra_query_params={
            "uploadId": upload_id,
            "partNumber": str(part_number),
        },
    )
    parsed = urlsplit(signed)
    if public_origin:
        prefix = public.path.rstrip("/")
        return (
            urlunsplit(
                (
                    public.scheme,
                    public.netloc,
                    f"{prefix}{parsed.path}",
                    parsed.query,
                    parsed.fragment,
                )
            ),
            {},
        )
    path = (
        f"/storage-upload/{quote(bucket, safe='')}/"
        f"{quote(object_key.lstrip('/'), safe='/')}"
    )
    return urlunsplit(("", "", path, parsed.query, "")), {}


def ensure_configured_buckets(cfg: MinioSettings | None = None) -> list[str]:
    """Create configured buckets when the MinIO server is empty (local compose)."""
    cfg = cfg or load_minio_settings()
    if not cfg:
        return []
    client = _client(cfg)
    created: list[str] = []
    for name in dict.fromkeys([cfg.bucket, cfg.temp_bucket]):
        if not name or client.bucket_exists(name):
            continue
        client.make_bucket(name)
        created.append(name)
        logger.info("minio created bucket %s", name)
    return created


def upload_bytes(
    *,
    content: bytes,
    object_key: str,
    content_type: str = "application/octet-stream",
    bucket: str | None = None,
) -> str:
    """Upload bytes to MinIO. Returns minio://bucket/object_key URI."""
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    ensure_configured_buckets(cfg)
    target_bucket = bucket or cfg.bucket
    client = _client(cfg)
    client.put_object(
        target_bucket,
        object_key,
        io.BytesIO(content),
        length=len(content),
        content_type=content_type,
    )
    logger.info(
        "minio upload ok bucket=%s key=%s bytes=%s",
        target_bucket,
        object_key,
        len(content),
    )
    return build_minio_uri(target_bucket, object_key)


def presign_get_url(
    *,
    object_key: str | None = None,
    minio_uri: str | None = None,
    expire_minutes: int | None = None,
) -> str:
    """Generate a GET presigned URL; optionally rewrite host to show-url."""
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    if minio_uri:
        bucket, key = parse_minio_uri(minio_uri)
    elif object_key:
        bucket, key = cfg.bucket, object_key
    else:
        raise ValueError("object_key or minio_uri required")

    from datetime import timedelta

    client = _client(cfg)
    minutes = expire_minutes if expire_minutes is not None else cfg.presign_minutes
    url = client.presigned_get_object(bucket, key, expires=timedelta(minutes=minutes))

    if cfg.show_url:
        url = _rewrite_host(url, cfg.show_url)
    return url


def _rewrite_host(presigned_url: str, show_url: str) -> str:
    """Replace scheme/host of a MinIO-signed URL with the public show-url host."""
    signed = urlparse(presigned_url)
    public = urlparse(show_url if "://" in show_url else f"https://{show_url}")
    if not public.netloc:
        return presigned_url
    return urlunparse(
        (
            public.scheme or signed.scheme,
            public.netloc,
            signed.path,
            signed.params,
            signed.query,
            signed.fragment,
        )
    )


def delete_object(
    *,
    minio_uri: str | None = None,
    object_key: str | None = None,
    bucket: str | None = None,
) -> None:
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    if minio_uri:
        target_bucket, target_key = parse_minio_uri(minio_uri)
    elif object_key:
        target_bucket, target_key = bucket or cfg.bucket, object_key
    else:
        raise ValueError("object_key or minio_uri required")
    _client(cfg).remove_object(target_bucket, target_key)


def object_exists(
    *,
    minio_uri: str | None = None,
    object_key: str | None = None,
    bucket: str | None = None,
) -> bool:
    """Return whether an object exists without downloading its bytes."""
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    if minio_uri:
        target_bucket, target_key = parse_minio_uri(minio_uri)
    elif object_key:
        target_bucket, target_key = bucket or cfg.bucket, object_key
    else:
        raise ValueError("object_key or minio_uri required")
    try:
        _client(cfg).stat_object(target_bucket, target_key)
    except Exception as exc:
        if getattr(exc, "code", None) in {
            "NoSuchBucket",
            "NoSuchKey",
            "NoSuchObject",
            "ResourceNotFound",
        }:
            return False
        raise
    return True


def download_bytes(
    *, minio_uri: str | None = None, object_key: str | None = None
) -> bytes:
    cfg = load_minio_settings()
    if not cfg:
        raise RuntimeError("MinIO 未配置或未启用")
    if minio_uri:
        bucket, key = parse_minio_uri(minio_uri)
    elif object_key:
        bucket, key = cfg.bucket, object_key
    else:
        raise ValueError("object_key or minio_uri required")
    client = _client(cfg)
    response = client.get_object(bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
