from __future__ import annotations

import base64
from io import BytesIO
import importlib
from unittest.mock import AsyncMock

import httpx
from PIL import Image
import pytest

from app.core.config import Settings, settings


def image_service():
    return importlib.import_module("app.services.image_generation")


def test_build_image_payload_uses_kling_controls(monkeypatch):
    service = image_service()
    monkeypatch.setattr(
        settings, "dashscope_image_model", "kling/kling-v3-image-generation"
    )

    assert service.build_image_payload("海边的花店", "2k") == {
        "model": "kling/kling-v3-image-generation",
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": "海边的花店"}],
                }
            ]
        },
        "parameters": {"n": 1, "aspect_ratio": "1:1", "resolution": "2k"},
    }


def _encoded_source_image(
    *, mode: str = "RGB", color=(35, 90, 160),
    size: tuple[int, int] = (320, 240), image_format: str = "PNG",
) -> str:
    image = Image.new(mode, size, color=color)
    raw = BytesIO()
    image.save(raw, format=image_format)
    return base64.b64encode(raw.getvalue()).decode("ascii")


def test_build_image_edit_payload_preserves_original_opaque_source(monkeypatch):
    service = image_service()
    monkeypatch.setattr(settings, "dashscope_image_edit_model", "wan2.7-image-pro")
    source_data = _encoded_source_image()

    payload = service.build_image_payload(
        "把背景改成海边",
        "2k",
        operation="edit",
        source_image={
            "type": "image",
            "data": source_data,
            "mimeType": "image/png",
        },
    )

    assert payload == {
        "model": "wan2.7-image-pro",
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"image": f"data:image/png;base64,{source_data}"},
                        {"text": "把背景改成海边"},
                    ],
                }
            ]
        },
        "parameters": {"size": "2K", "n": 1, "watermark": False},
    }


def test_normalize_image_edit_source_flattens_alpha_only_for_provider():
    service = image_service()
    original = _encoded_source_image(mode="RGBA", color=(255, 0, 0, 0))

    normalized = service.normalize_edit_source(
        {"data": original, "mimeType": "image/png"}
    )

    assert normalized.data != original
    assert normalized.mime_type == "image/png"
    with Image.open(BytesIO(base64.b64decode(normalized.data))) as provider_image:
        assert provider_image.mode == "RGB"
        assert provider_image.getpixel((0, 0)) == (255, 255, 255)



@pytest.mark.parametrize(
    "size,ratio", [((512, 512), "1:1"), ((800, 450), "16:9"), ((450, 800), "9:16")],
)
def test_kling_edit_payload_uses_url_and_actual_output_ratio(size, ratio):
    service = image_service()
    source_url = "https://images.example.com/private/source?X-Amz-Signature=test"
    payload = service.build_image_payload(
        "改成蓝色", "2k", operation="edit",
        model="kling/kling-v3-image-generation",
        source_image={"data": _encoded_source_image(size=size), "mimeType": "image/png"},
        source_image_url=source_url,
    )
    assert payload["input"]["messages"][0]["content"] == [
        {"image": source_url}, {"text": "改成蓝色"},
    ]
    assert payload["parameters"] == {
        "resolution": "2k", "aspect_ratio": ratio, "n": 1, "watermark": False,
    }


@pytest.mark.parametrize("source_url", [None, "data:image/png;base64,AAAA", "file:///private/source"])
def test_kling_edit_never_sends_inline_or_local_file_reference(source_url):
    service = image_service()
    with pytest.raises(service.ImageGenerationInputError, match="HTTPS"):
        service.build_image_payload(
            "改成蓝色", "1k", operation="edit",
            model="kling/kling-v3-image-generation",
            source_image={"data": _encoded_source_image(size=(512, 512)), "mimeType": "image/png"},
            source_image_url=source_url,
        )


@pytest.mark.parametrize("size", [(299, 512), (751, 300)])
def test_kling_source_limits_do_not_restrict_wan(size):
    service = image_service()
    source = {"data": _encoded_source_image(size=size), "mimeType": "image/png"}
    with pytest.raises(service.ImageGenerationInputError):
        service.KlingImageAdapter().normalize_source(source)
    wan = service.build_image_payload(
        "改成蓝色", "1k", operation="edit", model="wan2.7-image-pro", source_image=source,
    )
    assert wan["input"]["messages"][0]["content"][0]["image"].endswith(source["data"])


def test_kling_normalizes_webp_and_transparency_without_changing_source():
    service = image_service()
    original = _encoded_source_image(
        mode="RGBA", color=(255, 0, 0, 0), size=(512, 320), image_format="WEBP",
    )
    source = {"data": original, "mimeType": "image/webp"}
    normalized = service.KlingImageAdapter().normalize_source(source)
    with Image.open(BytesIO(base64.b64decode(normalized.data))) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (512, 320)
        assert image.getpixel((0, 0)) == (255, 255, 255)
    assert source["data"] == original


def test_kling_rejects_oversized_source_without_restricting_wan():
    service = image_service()
    # PNG permits trailing bytes; exercise the transport bound without a huge bitmap.
    content = base64.b64decode(_encoded_source_image(size=(512, 512)))
    content += b"\0" * (10 * 1024 * 1024 + 1 - len(content))
    source = {"data": base64.b64encode(content).decode("ascii"), "mimeType": "image/png"}
    with pytest.raises(service.ImageGenerationInputError, match="10 MB"):
        service.KlingImageAdapter().normalize_source(source)
    assert service.WanImageAdapter().normalize_source(source).data == source["data"]


@pytest.mark.asyncio
async def test_kling_publication_failure_never_submits_generation(monkeypatch):
    from app.services import image_edit_source

    service = image_service()
    monkeypatch.setattr(settings, "linkmind_enabled", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "dashscope_image_workspace_id", "image-workspace")
    monkeypatch.setattr(
        image_edit_source, "publish_source",
        AsyncMock(side_effect=image_edit_source.ImageEditSourceNotConfiguredError("源图地址不可用")),
    )
    def unexpected_client(**kwargs):
        pytest.fail("Provider must not receive a request without a usable source URL")
    monkeypatch.setattr(service.httpx, "AsyncClient", unexpected_client)
    with pytest.raises(service.ImageGenerationNotConfiguredError, match="源图地址"):
        await service.create_provider_task(
            prompt="改成蓝色", resolution="1k", operation="edit",
            model="kling/kling-v3-image-generation",
            source_image={"data": _encoded_source_image(size=(512, 512)), "mimeType": "image/png"},
        )


def test_unknown_edit_model_never_falls_back_to_another_provider():
    service = image_service()
    with pytest.raises(service.ImageGenerationNotConfiguredError):
        service.build_image_payload(
            "改成蓝色", "1k", operation="edit", model="unknown-model",
            source_image={"data": _encoded_source_image(size=(512, 512)), "mimeType": "image/png"},
        )

@pytest.mark.asyncio
async def test_image_edit_provider_is_disabled_in_linkmind_mode(monkeypatch):
    service = image_service()
    monkeypatch.setattr(settings, "linkmind_enabled", True)

    with pytest.raises(
        service.ImageGenerationNotConfiguredError,
        match="当前部署模式",
    ):
        await service.create_provider_task(
            prompt="改成蓝色",
            resolution="1k",
            operation="edit",
            source_image={
                "data": _encoded_source_image(),
                "mimeType": "image/png",
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["generate", "edit"])
async def test_create_provider_task_sends_async_request_and_parses_pending(
    monkeypatch, operation,
):
    service = image_service()
    from app.services import image_edit_source

    for name in (
        "DASHSCOPE_IMAGE_MODEL", "DOC_NEXUS_DASHSCOPE_IMAGE_MODEL",
        "DASHSCOPE_IMAGE_EDIT_MODEL", "DOC_NEXUS_DASHSCOPE_IMAGE_EDIT_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    configured = Settings(_env_file=None)
    configured.linkmind_enabled = False
    configured.dashscope_api_key = "test-key"
    configured.dashscope_image_workspace_id = "image-workspace"
    monkeypatch.setattr(service, "settings", configured)
    source_url = "https://images.example.com/source.png?signature=test"
    monkeypatch.setattr(
        image_edit_source, "publish_source", AsyncMock(return_value=source_url),
    )
    captured: dict[str, object] = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return httpx.Response(
                200,
                json={
                    "output": {"task_id": "image-provider-1", "task_status": "PENDING"}
                },
            )

    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    result = await service.create_provider_task(
        prompt="海边的花店", resolution="1k", operation=operation,
        source_image=(
            {"data": _encoded_source_image(size=(512, 512)), "mimeType": "image/png"}
            if operation == "edit" else None
        ),
    )

    assert result.provider_task_id == "image-provider-1"
    assert result.status == "pending"
    assert captured["json"]["model"] == "kling/kling-v3-image-generation"
    if operation == "edit":
        assert captured["json"]["input"]["messages"][0]["content"][0] == {
            "image": source_url,
        }
    assert captured["url"] == (
        "https://image-workspace.cn-beijing.maas.aliyuncs.com/"
        "api/v1/services/aigc/image-generation/generation"
    )
    assert captured["headers"] == {
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,prompt,resolution",
    [("generate", " ", "1k"), ("edit", "改成蓝色", "4k")],
)
async def test_invalid_image_input_precedes_missing_provider_configuration(
    monkeypatch, operation, prompt, resolution,
):
    service = image_service()
    monkeypatch.setattr(settings, "linkmind_enabled", False)
    monkeypatch.setattr(settings, "dashscope_image_workspace_id", "")
    monkeypatch.setattr(settings, "dashscope_video_workspace_id", "")
    monkeypatch.setattr(settings, "dashscope_api_key", "")
    with pytest.raises(service.ImageGenerationInputError):
        await service.create_provider_task(
            prompt=prompt, resolution=resolution, operation=operation,
        )


@pytest.mark.asyncio
async def test_poll_provider_task_parses_image_and_omits_async_header(monkeypatch):
    service = image_service()
    captured: dict[str, object] = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get(self, url, *, headers):
            captured.update(url=url, headers=headers)
            return httpx.Response(
                200,
                json={
                    "output": {
                        "task_id": "image-provider-1",
                        "task_status": "SUCCEEDED",
                        "choices": [
                            {
                                "message": {
                                    "content": [
                                        {"image": "https://example.com/result.png"}
                                    ]
                                }
                            }
                        ],
                    }
                },
            )

    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "dashscope_image_workspace_id", "image-workspace")

    result = await service.poll_provider_task("image-provider-1")

    assert result.status == "succeeded"
    assert result.image_url == "https://example.com/result.png"
    assert captured["headers"] == {"Authorization": "Bearer test-key"}


def test_provider_failures_use_safe_user_messages():
    service = image_service()

    failed = service._parse_provider_task(
        {
            "output": {
                "task_id": "image-provider-failed",
                "task_status": "FAILED",
                "message": "上游内部细节与凭据不应暴露",
            }
        }
    )
    expired = service._parse_provider_task(
        {
            "output": {
                "task_id": "image-provider-expired",
                "task_status": "SOMETHING_ELSE",
                "message": "原始错误不应暴露",
            }
        }
    )

    assert failed.status == "failed"
    assert failed.error_message == "图片生成失败，请重试。"
    assert expired.status == "unknown"
    assert expired.error_message == "任务已过期或不存在"


def test_encode_context_image_downscales_and_bounds_payload():
    service = image_service()
    source = Image.new("RGB", (2400, 1600), color=(35, 90, 160))
    raw = BytesIO()
    source.save(raw, format="PNG")

    encoded = service.encode_context_image(raw.getvalue())

    assert encoded is not None
    assert len(encoded) <= service.IMAGE_CONTEXT_MAX_DATA_CHARS
    with Image.open(BytesIO(base64.b64decode(encoded))) as normalized:
        assert normalized.format == "JPEG"
        assert max(normalized.size) <= 768


@pytest.mark.asyncio
async def test_fetch_context_image_data_downloads_only_provider_images(monkeypatch):
    service = image_service()
    source = Image.new("RGB", (1200, 900), color=(180, 50, 30))
    raw = BytesIO()
    source.save(raw, format="PNG")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "dashscope-result.oss-cn-beijing.aliyuncs.com"
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=raw.getvalue(),
        )

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    client_options: list[dict[str, object]] = []
    original_to_thread = service.asyncio.to_thread
    offload_calls: list[tuple[object, bytes]] = []

    async def tracked_to_thread(function, payload):
        offload_calls.append((function, payload))
        return await original_to_thread(function, payload)

    def client_factory(**kwargs):
        client_options.append(kwargs)
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(service.asyncio, "to_thread", tracked_to_thread)

    encoded = await service.fetch_context_image_data(
        "https://dashscope-result.oss-cn-beijing.aliyuncs.com/result.png"
    )

    assert encoded is not None
    assert len(encoded) <= service.IMAGE_CONTEXT_MAX_DATA_CHARS
    assert (
        client_options[0]["timeout"] == service.IMAGE_CONTEXT_DOWNLOAD_TIMEOUT_SECONDS
    )
    assert len(offload_calls) == 1
    assert offload_calls[0][0] is service.encode_context_image
    assert offload_calls[0][1] == raw.getvalue()
    assert (
        await service.fetch_context_image_data("https://example.com/result.png") is None
    )


@pytest.mark.asyncio
async def test_fetch_edit_source_preserves_trusted_provider_pixels(monkeypatch):
    service = image_service()
    original = base64.b64decode(_encoded_source_image())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "dashscope-result.oss-cn-beijing.aliyuncs.com"
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=original,
        )

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)

    source = await service.fetch_edit_source_image(
        "https://dashscope-result.oss-cn-beijing.aliyuncs.com/result.png"
    )

    assert source == {
        "type": "image",
        "data": base64.b64encode(original).decode("ascii"),
        "mimeType": "image/png",
    }
    assert (
        await service.fetch_edit_source_image("https://example.com/result.png") is None
    )


@pytest.mark.asyncio
async def test_fetch_edit_source_accepts_kling_cdn_urls(monkeypatch):
    """Regression: Kling result CDNs must be treated as trusted providers."""
    service = image_service()
    original = base64.b64decode(_encoded_source_image())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "p4-fdl.klingai.com"
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=original,
        )

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)

    source = await service.fetch_edit_source_image(
        "https://p4-fdl.klingai.com/ksc2/signed-result"
    )

    assert source == {
        "type": "image",
        "data": base64.b64encode(original).decode("ascii"),
        "mimeType": "image/png",
    }


def test_provider_image_content_type_detects_format():
    service = image_service()
    png = BytesIO()
    Image.new("RGB", (32, 24), color=(10, 20, 30)).save(png, format="PNG")
    assert service._image_content_type(png.getvalue()) == "image/png"
    webp = BytesIO()
    Image.new("RGB", (32, 24), color=(10, 20, 30)).save(webp, format="WEBP")
    assert service._image_content_type(webp.getvalue()) == "image/webp"
    assert service._image_content_type(b"not-an-image") == "application/octet-stream"


def test_encode_raw_image_content_preserves_bytes_and_mime():
    service = image_service()
    raw = BytesIO()
    Image.new("RGB", (64, 48), color=(200, 60, 10)).save(raw, format="PNG")
    payload = raw.getvalue()

    encoded = service.encode_raw_image_content(payload)

    assert encoded == {
        "type": "image",
        "data": base64.b64encode(payload).decode("ascii"),
        "mimeType": "image/png",
    }
    assert service.encode_raw_image_content(b"") is None


@pytest.mark.asyncio
async def test_mirror_provider_image_caches_allowlisted_bytes(monkeypatch):
    """A live Kling CDN image is mirrored into the durable object cache."""
    for key in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    service = image_service()
    original = base64.b64decode(_encoded_source_image())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=original,
        )

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        kwargs.pop("timeout", None)
        return original_client(transport=transport, trust_env=False, **kwargs)

    monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(service.minio_storage, "is_minio_enabled", lambda: True)
    uploaded: dict[str, object] = {}

    def fake_upload_bytes(*, content: bytes, object_key: str, content_type: str):
        uploaded["content"] = content
        uploaded["object_key"] = object_key
        uploaded["content_type"] = content_type
        return f"minio://dgmeta/{object_key}"

    monkeypatch.setattr(service.minio_storage, "upload_bytes", fake_upload_bytes)

    uri = await service.mirror_provider_image(
        owner_user_id="user-1",
        task_id="igt-1",
        image_url="https://p4-fdl.klingai.com/ksc2/result.png",
    )

    assert uri == "minio://dgmeta/image-generation/results/user-1/igt-1"
    assert uploaded["content"] == original
    assert uploaded["content_type"] == "image/png"
    assert uploaded["object_key"] == "image-generation/results/user-1/igt-1"


@pytest.mark.asyncio
async def test_mirror_provider_image_skips_untrusted_hosts(monkeypatch):
    """A URL outside the provider allowlist is never fetched or cached."""
    for key in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    service = image_service()
    monkeypatch.setattr(service.minio_storage, "is_minio_enabled", lambda: True)

    def client_factory(**kwargs):
        raise AssertionError("untrusted host must not create a client")

    monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)

    uri = await service.mirror_provider_image(
        owner_user_id="user-1",
        task_id="igt-1",
        image_url="https://evil.example.com/result.png",
    )

    assert uri is None


@pytest.mark.asyncio
async def test_mirror_provider_image_offloads_cache_work_and_propagates_worker_errors(
    monkeypatch,
):
    service = image_service()
    payload = b"provider-image"
    monkeypatch.setattr(
        service, "_download_provider_image", AsyncMock(return_value=payload)
    )
    monkeypatch.setattr(service, "image_cache_is_available", lambda: True)
    monkeypatch.setattr(
        service,
        "cache_image_bytes",
        lambda **_kwargs: "minio://dgmeta/result",
    )
    monkeypatch.setattr(service, "_image_content_type", lambda _data: "image/png")
    original_to_thread = service.asyncio.to_thread
    calls: list[tuple[object, tuple[object, ...]]] = []

    async def tracked_to_thread(function, *args):
        calls.append((function, args))
        return await original_to_thread(function, *args)

    monkeypatch.setattr(service.asyncio, "to_thread", tracked_to_thread)

    assert (
        await service.mirror_provider_image(
            owner_user_id="user-1",
            task_id="igt-1",
            image_url="https://p4-fdl.klingai.com/result.png",
        )
        == "minio://dgmeta/result"
    )
    assert len(calls) == 1
    assert calls[0][1] == ("user-1", "igt-1", payload)

    async def raising_to_thread(function, *args):
        raise RuntimeError("cache worker failed")

    monkeypatch.setattr(service.asyncio, "to_thread", raising_to_thread)
    assert (
        await service.mirror_provider_image(
            owner_user_id="user-1",
            task_id="igt-1",
            image_url="https://p4-fdl.klingai.com/result.png",
        )
        is None
    )


@pytest.mark.asyncio
async def test_async_cached_image_read_runs_in_worker_and_propagates_errors(monkeypatch):
    service = image_service()
    original_to_thread = service.asyncio.to_thread
    calls: list[tuple[object, tuple[object, ...]]] = []

    def read(uri: str | None) -> bytes:
        assert uri == "minio://dgmeta/result"
        return b"cached"

    monkeypatch.setattr(service, "read_cached_image_bytes", read)

    async def tracked_to_thread(function, *args):
        calls.append((function, args))
        return await original_to_thread(function, *args)

    monkeypatch.setattr(service.asyncio, "to_thread", tracked_to_thread)
    assert (
        await service.read_cached_image_bytes_async("minio://dgmeta/result")
        == b"cached"
    )
    assert calls == [(read, ("minio://dgmeta/result",))]

    def raising_read(_uri: str | None) -> bytes:
        raise RuntimeError("cache read failed")

    monkeypatch.setattr(service, "read_cached_image_bytes", raising_read)
    with pytest.raises(RuntimeError, match="cache read failed"):
        await service.read_cached_image_bytes_async("minio://dgmeta/result")


@pytest.mark.asyncio
async def test_download_rejects_redirect_to_untrusted_host(monkeypatch):
    """An allowlisted CDN redirecting off-provider must never be followed."""
    for key in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    service = image_service()
    hops = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        if len(hops) == 1:
            return httpx.Response(
                302, headers={"Location": "https://evil.example.com/payload.png"}
            )
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"x")

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        kwargs.pop("timeout", None)
        return original_client(transport=transport, trust_env=False, **kwargs)

    monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)

    payload = await service._download_provider_image(
        "https://p4-fdl.klingai.com/ksc2/result.png",
        max_bytes=1024 * 1024,
    )

    assert payload is None
    assert len(hops) == 1
    assert hops[0].startswith("https://p4-fdl.klingai.com")


@pytest.mark.asyncio
async def test_download_follows_redirect_within_provider_allowlist(monkeypatch):
    """Redirects that stay on the provider allowlist are followed."""
    for key in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    service = image_service()
    hops = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        if len(hops) == 1:
            return httpx.Response(
                302, headers={"Location": "https://p4-fdl.klingai.com/ksc2/final.png"}
            )
        return httpx.Response(
            200, headers={"Content-Type": "image/png"}, content=b"real-image"
        )

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        kwargs.pop("timeout", None)
        return original_client(transport=transport, trust_env=False, **kwargs)

    monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)

    payload = await service._download_provider_image(
        "https://p4-fdl.klingai.com/ksc2/result.png",
        max_bytes=1024 * 1024,
    )

    assert payload == b"real-image"
    assert len(hops) == 2


def test_encode_raw_image_content_accepts_large_provider_output():
    """2K PNG outputs above the old 4.5MB edit-source cap must round-trip."""
    service = image_service()
    import random
    random.seed(7)
    # 2400x2400 RGB random noise compresses poorly and yields >4.5MB PNG.
    pixels = bytes(random.randrange(256) for _ in range(2400 * 2400 * 3))
    img = Image.frombytes("RGB", (2400, 2400), pixels)
    raw = BytesIO()
    img.save(raw, format="PNG", compress_level=1)
    payload = raw.getvalue()
    assert len(payload) > 4_500_000, f"expected >4.5MB payload, got {len(payload)}"

    encoded = service.encode_raw_image_content(payload)

    assert encoded is not None
    assert encoded["mimeType"] == "image/png"
    assert base64.b64decode(encoded["data"]) == payload

def test_bound_edit_source_to_schema_compresses_oversized_png():
    """A 2K PNG beyond the 6M-char schema bound becomes a smaller JPEG."""
    service = image_service()
    import random
    random.seed(11)
    pixels = bytes(random.randrange(256) for _ in range(2200 * 2200 * 3))
    img = Image.frombytes("RGB", (2200, 2200), pixels)
    raw = BytesIO()
    img.save(raw, format="PNG", compress_level=1)
    payload = raw.getvalue()

    bound = service.bound_edit_source_to_schema(payload)

    assert bound is not None
    assert len(bound["data"]) <= service.EDIT_SOURCE_MAX_DATA_CHARS
    assert bound["mimeType"] == "image/jpeg"
    with Image.open(BytesIO(base64.b64decode(bound["data"]))) as out:
        assert out.size == (2200, 2200)

@pytest.mark.asyncio
async def test_fetch_edit_source_accepts_oversized_provider_png(monkeypatch):
    """Provider PNGs above the old 4.5MB edit-source cap still download."""
    import random

    for key in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    service = image_service()
    random.seed(23)
    pixels = bytes(random.randrange(256) for _ in range(2000 * 2000 * 3))
    img = Image.frombytes("RGB", (2000, 2000), pixels)
    raw = BytesIO()
    img.save(raw, format="PNG", compress_level=1)
    payload = raw.getvalue()
    assert len(payload) > service._EDIT_IMAGE_MAX_BYTES // 4  # > 5MB

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "image/png"},
            content=payload,
        )

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        kwargs.pop("timeout", None)
        return original_client(transport=transport, trust_env=False, **kwargs)

    monkeypatch.setattr(service.httpx, "AsyncClient", client_factory)

    source = await service.fetch_edit_source_image(
        "https://p4-fdl.klingai.com/ksc2/big-result.png"
    )

    assert source is not None
    assert source["mimeType"] == "image/png"
    # Raw bytes must survive; schema-bound compression is a separate layer.
    import base64 as _b64

    assert _b64.b64decode(source["data"]) == payload
