import importlib

import httpx
import pytest

from app.core.config import settings
from app import models


def video_service():
    return importlib.import_module("app.services.video_generation")


def test_video_settings_default_to_kling_v3():
    assert getattr(settings, "dashscope_video_model", None) == (
        "kling/kling-v3-video-generation"
    )
    assert getattr(settings, "dashscope_video_image_model", None) == (
        "kling/kling-v3-omni-video-generation"
    )
    assert getattr(settings, "video_file_adapter", None) == "bailian"


def test_video_task_model_is_exported():
    assert hasattr(models, "VideoGenerationTask")


def test_build_video_payload_uses_kling_controls(monkeypatch):
    service = video_service()
    monkeypatch.setattr(
        settings, "dashscope_video_model", "kling/kling-v3-video-generation"
    )

    assert service.build_video_payload("猫在月光下奔跑", "pro", 8) == {
        "model": "kling/kling-v3-video-generation",
        "input": {"prompt": "猫在月光下奔跑"},
        "parameters": {
            "mode": "pro",
            "aspect_ratio": "16:9",
            "duration": 8,
            "audio": False,
            "watermark": False,
        },
    }

def test_build_video_payload_uses_first_frame_model_and_media(monkeypatch):
    service = video_service()
    monkeypatch.setattr(
        settings,
        "dashscope_video_image_model",
        "kling/kling-v3-omni-video-generation",
    )

    assert service.build_video_payload(
        "让图片中的人物动起来",
        "std",
        5,
        first_frame_url="https://files.example/frame.png",
    ) == {
        "model": "kling/kling-v3-omni-video-generation",
        "input": {
            "prompt": "让图片中的人物动起来",
            "media": [
                {
                    "type": "first_frame",
                    "url": "https://files.example/frame.png",
                }
            ],
        },
        "parameters": {
            "mode": "std",
            "duration": 5,
            "audio": False,
            "watermark": False,
        },
    }


@pytest.mark.asyncio
async def test_create_provider_task_uploads_first_frame_before_submission(monkeypatch):
    service = video_service()
    captured: list[dict[str, object]] = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, *, headers, json=None, files=None):
            captured.append(
                {"method": "POST", "url": url, "headers": headers, "json": json, "files": files}
            )
            if url.endswith("/api/v1/files"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "uploaded_files": [
                                {"file_id": "file-frame-1", "name": "first-frame.png"}
                            ]
                        }
                    },
                )
            return httpx.Response(
                200,
                json={"output": {"task_id": "provider-1", "task_status": "PENDING"}},
            )

        async def get(self, url, *, headers):
            captured.append({"method": "GET", "url": url, "headers": headers})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "file_id": "file-frame-1",
                        "url": "https://files.example/frame.png",
                    }
                },
            )

    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "dashscope_video_workspace_id", "workspace-1")
    monkeypatch.setattr(
        settings,
        "dashscope_video_image_model",
        "kling/kling-v3-omni-video-generation",
    )

    result = await service.create_provider_task(
        prompt="让图片中的人物动起来",
        mode="std",
        duration=5,
        first_frame={"data": "ZnJhbWU=", "mimeType": "image/png"},
    )

    assert result.source_file_ref == "bailian://file-frame-1"
    provider_request = captured[-1]
    assert provider_request["json"]["model"] == "kling/kling-v3-omni-video-generation"
    assert provider_request["json"]["input"]["media"][0]["url"] == (
        "https://files.example/frame.png"
    )
    assert captured[0]["headers"] == {"Authorization": "Bearer test-key"}

@pytest.mark.asyncio
async def test_create_provider_task_sends_async_request(monkeypatch):
    service = video_service()
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
                json={"output": {"task_id": "provider-1", "task_status": "PENDING"}},
            )

    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "dashscope_video_workspace_id", "workspace-1")

    result = await service.create_provider_task(
        prompt="一只小猫在月光下奔跑", mode="std", duration=5
    )

    assert result.provider_task_id == "provider-1"
    assert result.status == "pending"
    assert captured["url"] == (
        "https://workspace-1.cn-beijing.maas.aliyuncs.com/"
        "api/v1/services/aigc/video-generation/video-synthesis"
    )
    assert captured["headers"] == {
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
@pytest.mark.asyncio
async def test_poll_provider_task_omits_async_submission_header(monkeypatch):
    service = video_service()
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
                json={"output": {"task_id": "provider-1", "task_status": "PENDING"}},
            )

    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "dashscope_video_workspace_id", "workspace-1")

    result = await service.poll_provider_task("provider-1")

    assert result.status == "pending"
    assert captured["headers"] == {"Authorization": "Bearer test-key"}


def test_build_video_payload_rejects_invalid_duration(monkeypatch):
    service = video_service()

    with pytest.raises(service.VideoGenerationInputError):
        service.build_video_payload("测试", "std", 2)

def test_provider_failures_use_safe_user_messages():
    service = video_service()

    failed = service._parse_provider_task(
        {
            "output": {
                "task_id": "provider-failed",
                "task_status": "FAILED",
                "message": "上游内部细节与凭据不应暴露",
            }
        }
    )
    expired = service._parse_provider_task(
        {
            "output": {
                "task_id": "provider-expired",
                "task_status": "SOMETHING_ELSE",
                "message": "原始错误不应暴露",
            }
        }
    )

    assert failed.status == "failed"
    assert failed.error_message == "视频生成失败，请重试。"
    assert expired.status == "unknown"
    assert expired.error_message == "任务已过期或不存在"