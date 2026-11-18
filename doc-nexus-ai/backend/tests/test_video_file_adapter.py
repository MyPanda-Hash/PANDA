from __future__ import annotations

import pytest

from app.services import minio_storage
from app.services.video_file_adapter import (
    BailianVideoFileAdapter,
    MinioVideoFileAdapter,
    VideoFileAdapterError,
    adapter_for_ref,
    build_video_file_adapter,
)


@pytest.mark.asyncio
async def test_minio_adapter_uploads_signs_and_deletes_temp_object(monkeypatch):
    config = minio_storage.MinioSettings(
        endpoint_url="https://t-minio.digimeta.com.cn",
        show_url="https://t-minio.digimeta.com.cn",
        access_key="access-key",
        secret_key="secret-key",
        bucket="dgmeta",
        temp_bucket="dgmeta-temp",
        presign_minutes=120,
    )
    uploaded: dict[str, object] = {}
    deleted: dict[str, object] = {}

    monkeypatch.setattr(minio_storage, "load_minio_settings", lambda: config)

    def upload_bytes(**kwargs):
        uploaded.update(kwargs)
        return f"minio://dgmeta-temp/{kwargs['object_key']}"

    def presign_get_url(**kwargs):
        uploaded["presign"] = kwargs
        return "https://t-minio.digimeta.com.cn/dgmeta-temp/signed-frame.png?sig=1"

    def delete_object(**kwargs):
        deleted.update(kwargs)

    monkeypatch.setattr(minio_storage, "upload_bytes", upload_bytes)
    monkeypatch.setattr(minio_storage, "presign_get_url", presign_get_url)
    monkeypatch.setattr(minio_storage, "delete_object", delete_object)

    adapter = MinioVideoFileAdapter()
    file_ref = await adapter.upload_first_frame(
        content=b"frame", filename="first-frame.png", mime_type="image/png"
    )
    url = await adapter.resolve_url(file_ref)
    deleted_ok = await adapter.delete(file_ref)

    assert file_ref.startswith("minio://dgmeta-temp/video-generation/first-frames/")
    assert file_ref.endswith(".png")
    assert uploaded["content"] == b"frame"
    assert uploaded["bucket"] == "dgmeta-temp"
    assert uploaded["content_type"] == "image/png"
    assert uploaded["presign"] == {"minio_uri": file_ref, "expire_minutes": 120}
    assert url.startswith("https://t-minio.digimeta.com.cn/")
    assert deleted_ok is True
    assert deleted == {"minio_uri": file_ref}


@pytest.mark.asyncio
async def test_minio_adapter_rejects_local_show_url(monkeypatch):
    config = minio_storage.MinioSettings(
        endpoint_url="http://minio:9000",
        show_url="http://localhost:19000",
        access_key="access-key",
        secret_key="secret-key",
        bucket="dgmeta",
        temp_bucket="dgmeta-temp",
        presign_minutes=120,
    )
    monkeypatch.setattr(minio_storage, "load_minio_settings", lambda: config)

    with pytest.raises(VideoFileAdapterError, match="公网 URL"):
        await MinioVideoFileAdapter().resolve_url(
            "minio://dgmeta-temp/video-generation/first-frame.png"
        )


def test_video_file_adapter_selects_configured_backend():
    minio_adapter = build_video_file_adapter(
        name="minio",
        base_url="https://workspace.cn-beijing.maas.aliyuncs.com",
        api_key="test-key",
        timeout_seconds=30,
    )
    bailian_adapter = build_video_file_adapter(
        name="bailian",
        base_url="https://workspace.cn-beijing.maas.aliyuncs.com",
        api_key="test-key",
        timeout_seconds=30,
    )

    assert isinstance(minio_adapter, MinioVideoFileAdapter)
    assert isinstance(bailian_adapter, BailianVideoFileAdapter)
    assert isinstance(
        adapter_for_ref(
            file_ref="minio://dgmeta-temp/frame.png",
            configured=bailian_adapter,
            base_url="https://workspace.cn-beijing.maas.aliyuncs.com",
            api_key="test-key",
            timeout_seconds=30,
        ),
        MinioVideoFileAdapter,
    )


def test_video_file_adapter_rejects_unknown_backend():
    with pytest.raises(VideoFileAdapterError, match="bailian 或 minio"):
        build_video_file_adapter(
            name="unknown",
            base_url="https://workspace.cn-beijing.maas.aliyuncs.com",
            api_key="test-key",
            timeout_seconds=30,
        )
