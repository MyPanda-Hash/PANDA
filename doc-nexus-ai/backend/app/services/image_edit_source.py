"""Publish short-lived private source images for external image-edit providers."""

from __future__ import annotations

import asyncio
import io
import ipaddress
import re
import secrets
import socket
from datetime import timedelta
from urllib.parse import urlsplit

from app.core.database import transaction
from app.core.security import now
from app.services import external_object_cleanup, minio_storage

_SOURCE_EXPIRY = timedelta(hours=24)
_SOURCE_CLEANUP_DELAY = timedelta(hours=25)
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)
_LOCAL_SUFFIXES = (
    ".localhost", ".local", ".localdomain", ".internal", ".lan", ".home", ".home.arpa",
    ".test", ".invalid",
)
_CONFIGURATION_ERROR = "图片编辑需要配置可公网访问的 HTTPS 私有对象存储地址。"
_PUBLISH_ERROR = "图片编辑源图暂时无法发布，请稍后重试。"


class ImageEditSourceNotConfiguredError(RuntimeError):
    """The source store has no safe public HTTPS origin."""


class ImageEditSourcePublishError(RuntimeError):
    """DNS, storage, signing or durable cleanup tracking failed."""


def _require_global_address(address: str) -> None:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        raise ImageEditSourceNotConfiguredError(_CONFIGURATION_ERROR) from None
    if not ip.is_global or ip.is_reserved or ip.is_multicast:
        raise ImageEditSourceNotConfiguredError(_CONFIGURATION_ERROR)


def _public_endpoint(show_url: str) -> str:
    try:
        parsed = urlsplit(show_url)
        host = parsed.hostname or ""
        port = parsed.port or 443
        if (
            parsed.scheme != "https"
            or not host
            or parsed.port == 0
            or parsed.netloc.endswith(":")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or any(char.isspace() or ord(char) < 32 for char in show_url)
            or any(char in show_url for char in "\\%?#")
        ):
            raise ValueError("invalid public origin")
    except ValueError:
        raise ImageEditSourceNotConfiguredError(_CONFIGURATION_ERROR) from None

    try:
        ipaddress.ip_address(host)
    except ValueError:
        name = host.rstrip(".").lower()
        if (
            "." not in name
            or len(name) > 253
            or name.endswith(_LOCAL_SUFFIXES)
            or not all(_DNS_LABEL.fullmatch(label) for label in name.split("."))
        ):
            raise ImageEditSourceNotConfiguredError(_CONFIGURATION_ERROR)
    else:
        _require_global_address(host)

    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise ImageEditSourcePublishError(_PUBLISH_ERROR) from None
    if not addresses:
        raise ImageEditSourcePublishError(_PUBLISH_ERROR)
    for address in addresses:
        _require_global_address(str(address[4][0]))
    return parsed.netloc


def _publish_source(*, content: bytes, mime_type: str) -> str:
    from minio import Minio

    try:
        config = minio_storage.load_minio_settings()
        if config is None:
            raise ImageEditSourceNotConfiguredError(_CONFIGURATION_ERROR)
        public_endpoint = _public_endpoint(config.show_url)
        internal_client = minio_storage._client(config)
        # Discover the bucket region internally; signing must never query the public host.
        region = internal_client._get_region(config.bucket)
        public_client = Minio(
            public_endpoint,
            access_key=config.access_key,
            secret_key=config.secret_key,
            secure=True,
            region=region,
        )
        public_client.disable_virtual_style_endpoint()
        object_key = f"image-generation/results/edit-source/{secrets.token_hex(32)}"
        uri = minio_storage.build_minio_uri(config.bucket, object_key)
        stamp = now()
        # Commit before upload: a crash or an ambiguous PUT outcome remains reclaimable.
        with transaction() as db:
            tombstone = external_object_cleanup.enqueue(db, uri)
            if tombstone is None:
                raise ImageEditSourcePublishError(_PUBLISH_ERROR)
            tombstone.next_attempt_at = stamp + _SOURCE_CLEANUP_DELAY

        # Fix the signing time to the tombstone's lifetime, even if uploading is slow.
        source_url = public_client.presigned_get_object(
            config.bucket, object_key, expires=_SOURCE_EXPIRY, request_date=stamp
        )
        internal_client.put_object(
            config.bucket,
            object_key,
            io.BytesIO(content),
            length=len(content),
            content_type=mime_type,
        )
        return source_url
    except (ImageEditSourceNotConfiguredError, ImageEditSourcePublishError):
        raise
    except Exception:
        # Provider-visible errors must not contain endpoint credentials or signed URLs.
        raise ImageEditSourcePublishError(_PUBLISH_ERROR) from None


async def publish_source(*, content: bytes, mime_type: str) -> str:
    """Return a private HTTPS URL whose independent cleanup is already durable."""
    return await asyncio.to_thread(_publish_source, content=content, mime_type=mime_type)
