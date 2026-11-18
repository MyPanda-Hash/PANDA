from __future__ import annotations

import base64
import json
from datetime import timedelta
from io import BytesIO
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import database
from app.core.security import now
from app.core.config import settings
from app.api import image_generation as image_api
from app.main import app
from app.models.chat import ChatDispatchDecision
from app.models.image_generation import ImageGenerationTask
from app.services import image_generation as image_service
from app.services import media_task_dispatch
from app.services.image_generation import ProviderTask


def _valid_png_bytes(size: tuple[int, int] = (4, 4)) -> bytes:
    """Return a small but genuinely decodable PNG payload."""
    from PIL import Image

    img = Image.new("RGB", size, color=(10, 120, 200))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@contextmanager
def _configured_client(tmp_path: Path, *, configured: bool = True):
    original_path = settings.db_path
    original_url = settings.database_url
    original_key = settings.dashscope_api_key
    original_image_workspace = settings.dashscope_image_workspace_id
    original_video_workspace = settings.dashscope_video_workspace_id
    original_linkmind_enabled = settings.linkmind_enabled
    db_path = tmp_path / "image-generation.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    settings.dashscope_api_key = "image-test-key" if configured else ""
    settings.dashscope_image_workspace_id = "image-test-workspace" if configured else ""
    settings.dashscope_video_workspace_id = (
        "" if not configured else "video-test-workspace"
    )
    settings.linkmind_enabled = False
    try:
        with TestClient(app) as client:
            yield client
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.dashscope_api_key = original_key
        settings.dashscope_image_workspace_id = original_image_workspace
        settings.dashscope_video_workspace_id = original_video_workspace
        settings.linkmind_enabled = original_linkmind_enabled
        database.reset_database(db_path)


def _admin_headers(client: TestClient) -> dict[str, str]:
    password = "Admin-image-generation-123"  # noqa: S105 - explicit test password
    initialized = client.post(
        "/auth/setup",
        json={
            "new_password": password,
            "password_confirmation": password,
        },
    )
    assert initialized.status_code == 200
    return {"Authorization": f"Bearer {initialized.json()['access_token']}"}


def _create_session(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        "/chat/sessions", headers=headers, json={"title": "图片测试"}
    )
    assert response.status_code == 201
    return response.json()["id"]


def _create_member_headers(
    client: TestClient, admin_headers: dict[str, str]
) -> dict[str, str]:
    invited = client.post(
        "/admin/users",
        headers=admin_headers,
        json={
            "username": "image-member",
            "email": "image-member@example.com",
            "display_name": "图片成员",
        },
    )
    assert invited.status_code == 201
    accepted = client.post(
        f"/auth/invitations/{invited.json()['invite_token']}/accept",
        json={
            "password": "Image-member-123",
            "password_confirmation": "Image-member-123",
        },
    )
    assert accepted.status_code == 200
    login = client.post(
        "/auth/login",
        json={"username": "image-member", "password": "Image-member-123"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_image_generation_requires_credentials(tmp_path: Path):
    with _configured_client(tmp_path, configured=False) as client:
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        response = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "海边的花店"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "图片生成功能未配置"


def test_image_generation_uses_persisted_chat_context(
    tmp_path: Path,
):
    created = ProviderTask("image-provider-context", "pending")
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(return_value=created),
        ) as create_provider,
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        context_response = client.post(
            f"/chat/sessions/{session_id}/messages",
            headers=headers,
            json={
                "messages": [
                    {"role": "user", "content": "我要做一张活动海报。"},
                    {
                        "role": "assistant",
                        "content": "活动名称是春日读书会，地点是杭州，主色调为青绿色。",
                    },
                ]
            },
        )
        assert context_response.status_code == 200

        response = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "设计一张适合网页展示的活动海报，保留上文信息。",
            },
        )

    assert response.status_code == 201
    assert response.json()["prompt"] == (
        "设计一张适合网页展示的活动海报，保留上文信息。"
    )
    assert create_provider.await_args is not None
    provider_prompt = create_provider.await_args.kwargs["prompt"]
    assert "活动名称是春日读书会，地点是杭州，主色调为青绿色。" in provider_prompt
    assert "设计一张适合网页展示的活动海报，保留上文信息。" in provider_prompt
    assert "【当前会话上下文】" in provider_prompt
    assert "【本次图片生成要求】" in provider_prompt


def test_image_generation_creates_chat_messages_and_polls_task(tmp_path: Path):
    created = ProviderTask("image-provider-1", "pending")
    running = ProviderTask("image-provider-1", "running")
    succeeded = ProviderTask(
        "image-provider-1", "succeeded", image_url="https://example.com/result.png"
    )
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(return_value=created),
        ),
        patch(
            "app.api.image_generation.image_service.poll_provider_task",
            new=AsyncMock(side_effect=[running, succeeded]),
        ) as poll_provider,
        patch(
            "app.api.image_generation.image_service.fetch_context_image_data",
            new=AsyncMock(),
        ) as fetch_context_image,
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        response = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "在海边，一间有着精致窗户的花店",
                "resolution": "2k",
            },
        )

        assert response.status_code == 201
        task = response.json()
        assert task["status"] == "pending"
        assert task["model"] == "kling/kling-v3-image-generation"
        assert task["aspect_ratio"] == "1:1"
        assert task["resolution"] == "2k"
        assert task["image_url"] is None
        assert task["operation"] == "generate"
        assert task["retryable"] is False

        running_response = client.get(
            f"/image-generation/tasks/{task['id']}", headers=headers
        )
        assert running_response.status_code == 200
        assert running_response.json()["status"] == "running"
        assert poll_provider.await_count == 1

        completed = client.get(f"/image-generation/tasks/{task['id']}", headers=headers)
        assert completed.status_code == 200
        assert completed.json()["status"] == "succeeded"
        assert completed.json()["image_url"] == "https://example.com/result.png"
        assert "image_data" not in completed.json()
        fetch_context_image.assert_not_awaited()
        with database.SessionLocal() as db:
            stored = db.scalar(
                select(ImageGenerationTask).where(ImageGenerationTask.id == task["id"])
            )
            assert stored is not None
            assert stored.image_data is None
            assert stored.image_context_consumed_at is None

        session = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
        assert [message["role"] for message in session["messages"]] == [
            "user",
            "assistant",
        ]
        assert session["messages"][0]["content"].startswith("在海边")
        assert session["messages"][1]["content"] == "图片生成任务已完成。"


def test_get_task_reconciles_expired_submitting_without_provider_id(
    tmp_path: Path,
):
    created = ProviderTask("image-provider-stale-read", "pending")
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(return_value=created),
        ),
        patch(
            "app.api.image_generation.image_service.poll_provider_task",
            new=AsyncMock(side_effect=AssertionError("stale task must not poll")),
        ) as poll_provider,
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        response = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "过期图片"},
        )
        assert response.status_code == 201
        task_id = response.json()["id"]

        with database.SessionLocal() as db:
            task = db.get(ImageGenerationTask, task_id)
            assert task is not None
            task.status = "submitting"
            task.provider_task_id = None
            task.updated_at = now() - timedelta(hours=1)
            assert task.dispatch_id is not None
            decision = db.get(ChatDispatchDecision, task.dispatch_id)
            assert decision is not None
            decision.status = "dispatching"
            decision.updated_at = task.updated_at
            db.commit()

        stale = client.get(f"/image-generation/tasks/{task_id}", headers=headers)
        assert stale.status_code == 200
        assert stale.json()["status"] == "outcome_unknown"
        poll_provider.assert_not_awaited()

        with database.SessionLocal() as db:
            task = db.get(ImageGenerationTask, task_id)
            assert task is not None
            assert task.status == "outcome_unknown"


def test_image_poll_retries_result_commit_without_polling_twice(
    tmp_path: Path, monkeypatch
):
    initial = ProviderTask("image-provider-commit-retry", "pending")
    completed = ProviderTask(
        "image-provider-commit-retry",
        "succeeded",
        image_url="https://p4-fdl.klingai.com/result.png",
    )
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(return_value=initial),
        ),
        patch(
            "app.api.image_generation.image_service.poll_provider_task",
            new=AsyncMock(return_value=completed),
        ) as poll_provider,
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "提交重试"},
        )
        assert created.status_code == 201
        task_id = created.json()["id"]

        real_session_local = database.SessionLocal
        commit_attempts = 0

        def flaky_session_local():
            nonlocal commit_attempts
            db = real_session_local()
            original_commit = db.commit

            def commit_once_fails() -> None:
                nonlocal commit_attempts
                commit_attempts += 1
                if commit_attempts == 1:
                    raise RuntimeError("transient image result commit failure")
                original_commit()

            setattr(db, "commit", commit_once_fails)
            return db

        monkeypatch.setattr(media_task_dispatch, "SessionLocal", flaky_session_local)
        result = client.get(f"/image-generation/tasks/{task_id}", headers=headers)

        assert result.status_code == 200
        assert result.json()["status"] == "succeeded"
        assert commit_attempts == 2
        poll_provider.assert_awaited_once()


def test_image_poll_error_does_not_overwrite_concurrent_terminal_success(
    tmp_path: Path,
):
    initial = ProviderTask("image-provider-race", "pending")

    async def poll_after_concurrent_success(_provider_task_id: str):
        with database.SessionLocal() as db:
            task = db.get(ImageGenerationTask, task_id)
            assert task is not None
            task.status = "succeeded"
            task.image_url = "https://p4-fdl.klingai.com/race-success.png"
            task.completed_at = now()
            db.commit()
        raise image_service.ImageGenerationProviderError("HTTP 500")

    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(return_value=initial),
        ),
        patch(
            "app.api.image_generation.image_service.poll_provider_task",
            new=AsyncMock(side_effect=poll_after_concurrent_success),
        ),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "并发结果"},
        )
        assert created.status_code == 201
        task_id = created.json()["id"]

        result = client.get(
            f"/image-generation/tasks/{task_id}", headers=headers
        )

        assert result.status_code == 200
        assert result.json()["status"] == "succeeded"


def test_image_cache_metadata_commit_failure_does_not_fail_image_response(
    tmp_path: Path, monkeypatch
):
    completed = ProviderTask(
        "image-provider-cache-repair",
        "succeeded",
        image_url="https://p4-fdl.klingai.com/cache-repair.png",
    )
    png_bytes = _valid_png_bytes()
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(return_value=completed),
        ),
        patch(
            "app.api.image_generation.image_service.read_cached_image_bytes_async",
            new=AsyncMock(return_value=png_bytes),
        ),
        patch(
            "app.api.image_generation.image_service.image_cache_is_available",
            return_value=False,
        ) as cache_available,
        patch(
            "app.api.image_generation.image_service.mirror_provider_image",
            new=AsyncMock(
                return_value="minio://dgmeta/image-generation/results/cache-repair"
            ),
        ) as mirror_provider,
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "缓存回写"},
        )
        assert created.status_code == 201
        task_id = created.json()["id"]
        cache_available.return_value = True

        real_session_local = database.SessionLocal
        session_calls = 0

        def flaky_session_local():
            nonlocal session_calls
            db = real_session_local()
            session_calls += 1
            if session_calls == 2:
                def commit_fails() -> None:
                    raise RuntimeError("transient cache metadata commit failure")

                setattr(db, "commit", commit_fails)
            return db

        monkeypatch.setattr(image_api, "SessionLocal", flaky_session_local)
        response = client.get(
            f"/image-generation/tasks/{task_id}/image",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.content == png_bytes
        mirror_provider.assert_awaited_once()
        with database.SessionLocal() as db:
            task = db.get(ImageGenerationTask, task_id)
            assert task is not None
            assert task.image_cache_uri is None


def test_image_generation_immediate_success_does_not_wait_for_context_cache(
    tmp_path: Path,
):
    succeeded = ProviderTask(
        "image-provider-immediate",
        "succeeded",
        image_url="https://example.com/immediate.png",
    )
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(return_value=succeeded),
        ),
        patch(
            "app.api.image_generation.image_service.fetch_context_image_data",
            new=AsyncMock(),
        ) as fetch_context_image,
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)

        response = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "花店", "resolution": "1k"},
        )

        assert response.status_code == 201
        assert response.json()["status"] == "succeeded"
        fetch_context_image.assert_not_awaited()


def test_image_generation_failed_task_can_be_retried(tmp_path: Path):
    submitted: list[dict] = []
    original_client = httpx.AsyncClient
    original_model = "kling/kling-v3-image-generation"

    def provider(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        submitted.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"output": {
                "task_id": f"image-provider-{len(submitted)}",
                "task_status": "FAILED" if len(submitted) == 1 else "SUCCEEDED",
                "choices": [{"message": {"content": [
                    {"image": "https://example.com/retried.png"},
                ]}}],
            }},
        )

    def provider_client(**kwargs):
        return original_client(transport=httpx.MockTransport(provider), **kwargs)

    with (
        _configured_client(tmp_path) as client,
        patch.object(settings, "dashscope_image_model", original_model),
        patch.object(image_service.httpx, "AsyncClient", side_effect=provider_client),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "花店", "resolution": "1k"},
        ).json()
        failed = client.get(f"/image-generation/tasks/{created['id']}", headers=headers)
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert created["model"] == original_model

        settings.dashscope_image_model = "kling/kling-v3-omni-image-generation"
        retried_response = client.post(
            f"/image-generation/tasks/{created['id']}/retry", headers=headers
        )
        assert retried_response.status_code == 200
        assert retried_response.json()["status"] == "succeeded"
        assert retried_response.json()["error_message"] is None
        assert retried_response.json()["model"] == original_model
        assert [payload["model"] for payload in submitted] == [
            original_model, original_model,
        ]

        duplicate = client.post(
            f"/image-generation/tasks/{created['id']}/retry", headers=headers
        )
        assert duplicate.status_code == 409
        assert len(submitted) == 2
        session = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
        assert session["messages"][1]["content"] == "图片生成任务已完成。"


def test_image_edit_persists_source_and_reuses_it_on_retry(tmp_path: Path):
    initial = ProviderTask("image-edit-old", "failed")
    retried = ProviderTask(
        "image-edit-new",
        "succeeded",
        image_url="https://example.com/edited.png",
    )
    source_image = {
        "type": "image",
        "data": base64.b64encode(_valid_png_bytes((640, 480))).decode("ascii"),
        "mimeType": "image/png",
    }
    with (
        _configured_client(tmp_path) as client,
        patch.object(settings, "dashscope_image_edit_model", "wan2.7-image-pro"),
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(side_effect=[initial, retried]),
        ),
        patch(
            "app.api.image_generation.image_service.fetch_edit_source_image",
            new=AsyncMock(return_value=source_image),
        ) as fetch_edit_source,
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created_response = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={
                "session_id": session_id,
                "operation": "edit",
                "prompt": "只把背景改成海边",
                "resolution": "2k",
                "source_image": source_image,
            },
        )

        assert created_response.status_code == 201
        created = created_response.json()
        assert created["operation"] == "edit"
        assert created["model"] == "wan2.7-image-pro"
        assert created["aspect_ratio"] == "source"
        assert created["retryable"] is True

        session = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
        assert session["messages"][0]["images"] == [source_image]
        assert session["messages"][1]["content"] == "图片编辑任务失败，请重试。"

        retried_response = client.post(
            f"/image-generation/tasks/{created['id']}/retry",
            headers=headers,
        )
        assert retried_response.status_code == 200
        assert retried_response.json()["status"] == "succeeded"
        assert retried_response.json()["retryable"] is False
        session = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
        assert session["messages"][1]["content"] == "图片编辑任务已完成。"
        edit_source = client.get(
            f"/image-generation/tasks/{created['id']}/edit-source",
            headers=headers,
        )
        assert edit_source.status_code == 200
        assert edit_source.json() == {"source_image": source_image}
        fetch_edit_source.assert_awaited_once_with("https://example.com/edited.png")
        fetch_edit_source.return_value = None
        unavailable = client.get(
            f"/image-generation/tasks/{created['id']}/edit-source",
            headers=headers,
        )
        assert unavailable.status_code == 409
        assert unavailable.json()["detail"] == "编辑结果图片已不可用，请重新上传原图"


@pytest.mark.parametrize(
    ("model", "size", "expected_ratio", "next_default"),
    [
        ("kling/kling-v3-image-generation", (800, 450), "16:9", "wan2.7-image-pro"),
        ("kling/kling-v3-image-generation", (450, 800), "9:16", "wan2.7-image-pro"),
        ("wan2.7-image-pro", (800, 450), "source", "kling/kling-v3-image-generation"),
    ],
)
def test_image_edit_ratio_and_historical_retry_pin_provider_model(
    tmp_path: Path,
    model: str,
    size: tuple[int, int],
    expected_ratio: str,
    next_default: str,
):
    source = {
        "type": "image",
        "data": base64.b64encode(_valid_png_bytes(size)).decode("ascii"),
        "mimeType": "image/png",
    }
    submitted: list[dict] = []
    source_url = "https://images.example.com/private-source.png?signature=test"
    original_client = httpx.AsyncClient

    def provider(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        submitted.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_id": f"edit-pinned-{len(submitted)}",
                    "task_status": "FAILED",
                }
            },
        )

    def provider_client(**kwargs):
        return original_client(transport=httpx.MockTransport(provider), **kwargs)

    with (
        _configured_client(tmp_path) as client,
        patch.object(settings, "dashscope_image_edit_model", model),
        patch.object(image_service.httpx, "AsyncClient", side_effect=provider_client),
        patch(
            "app.services.image_edit_source.publish_source",
            new=AsyncMock(return_value=source_url),
        ),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={
                "session_id": session_id,
                "operation": "edit",
                "prompt": "保留主体，将背景改成海边",
                "resolution": "2k",
                "source_image": source,
            },
        )
        assert created.status_code == 201
        task = created.json()
        assert (task["model"], task["aspect_ratio"]) == (model, expected_ratio)
        assert task["status"] == "failed"

        # Historical tasks have no dispatch decision, but retain their adapter.
        with database.SessionLocal() as db:
            stored = db.get(ImageGenerationTask, task["id"])
            assert stored is not None
            stored.dispatch_id = None
            db.commit()
        settings.dashscope_image_edit_model = next_default
        retried = client.post(
            f"/image-generation/tasks/{task['id']}/retry", headers=headers
        )
        assert retried.status_code == 200
        assert (retried.json()["model"], retried.json()["aspect_ratio"]) == (
            model,
            expected_ratio,
        )
        fetched = client.get(
            f"/image-generation/tasks/{task['id']}", headers=headers
        )
        assert fetched.status_code == 200
        assert fetched.json()["aspect_ratio"] == expected_ratio
        assert len(submitted) == 2
        for payload in submitted:
            assert payload["model"] == model
            image = payload["input"]["messages"][0]["content"][0]["image"]
            if expected_ratio == "source":
                assert payload["parameters"]["size"] == "2K"
                assert "aspect_ratio" not in payload["parameters"]
                assert image == f"data:image/png;base64,{source['data']}"
            else:
                assert payload["parameters"]["aspect_ratio"] == expected_ratio
                assert image == source_url


def test_invalid_edit_source_does_not_persist_task_or_messages(tmp_path: Path):
    with (
        _configured_client(tmp_path) as client,
        patch.object(
            settings, "dashscope_image_edit_model", "kling/kling-v3-image-generation"
        ),
        patch(
            "app.services.image_edit_source.publish_source",
            new=AsyncMock(side_effect=AssertionError("invalid source must not publish")),
        ),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        response = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={
                "session_id": session_id,
                "operation": "edit",
                "prompt": "背景改成海边",
                "source_image": {
                    "type": "image",
                    "data": base64.b64encode(b"not-an-image").decode("ascii"),
                    "mimeType": "image/png",
                },
            },
        )
        assert response.status_code == 422
        tasks = client.get(
            "/image-generation/tasks",
            params={"session_id": session_id},
            headers=headers,
        )
        assert tasks.status_code == 200
        assert tasks.json() == []
        session = client.get(f"/chat/sessions/{session_id}", headers=headers)
        assert session.status_code == 200
        assert session.json()["messages"] == []


def test_source_publish_failure_remains_retryable_without_provider_submission(
    tmp_path: Path,
):
    from app.services.image_edit_source import ImageEditSourcePublishError

    submitted: list[httpx.Request] = []
    original_client = httpx.AsyncClient

    def provider(request: httpx.Request) -> httpx.Response:
        submitted.append(request)
        raise AssertionError("source publication failure must not reach the provider")

    def provider_client(**kwargs):
        return original_client(transport=httpx.MockTransport(provider), **kwargs)

    with (
        _configured_client(tmp_path) as client,
        patch.object(
            settings, "dashscope_image_edit_model", "kling/kling-v3-image-generation"
        ),
        patch.object(image_service.httpx, "AsyncClient", side_effect=provider_client),
        patch(
            "app.services.image_edit_source.publish_source",
            new=AsyncMock(side_effect=ImageEditSourcePublishError("源图暂时无法发布")),
        ),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        response = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={
                "session_id": session_id,
                "operation": "edit",
                "prompt": "背景改成海边",
                "source_image": {
                    "type": "image",
                    "data": base64.b64encode(_valid_png_bytes((640, 480))).decode("ascii"),
                    "mimeType": "image/png",
                },
            },
        )
        assert response.status_code == 502
        tasks = client.get(
            "/image-generation/tasks",
            params={"session_id": session_id},
            headers=headers,
        )
        assert tasks.status_code == 200
        task, = tasks.json()
        assert task["status"] == "failed"
        assert task["retryable"] is True
        assert submitted == []


def test_image_edit_is_rejected_in_linkmind_mode(tmp_path: Path):
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(),
        ) as create_provider,
    ):
        settings.linkmind_enabled = True
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        response = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={
                "session_id": session_id,
                "operation": "edit",
                "prompt": "改成蓝色",
                "source_image": {
                    "type": "image",
                    "data": "b3JpZ2luYWwtaW1hZ2U=",
                    "mimeType": "image/png",
                },
            },
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "图片编辑在当前部署模式下不可用"
        create_provider.assert_not_awaited()
        edit_source = client.get(
            "/image-generation/tasks/not-visible/edit-source",
            headers=headers,
        )
        assert edit_source.status_code == 503


def test_image_generation_rejects_invalid_input_and_foreign_session(tmp_path: Path):
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(return_value=ProviderTask("image-provider-1", "pending")),
        ),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        assert (
            client.post(
                "/image-generation/tasks",
                headers=headers,
                json={"session_id": session_id, "prompt": "", "resolution": "1k"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/image-generation/tasks",
                headers=headers,
                json={
                    "session_id": session_id,
                    "prompt": "花店",
                    "resolution": "4k",
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/image-generation/tasks",
                headers=headers,
                json={"session_id": "not-my-session", "prompt": "越权"},
            ).status_code
            == 404
        )


def test_image_generation_denies_cross_user_task_and_session_access(tmp_path: Path):
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(return_value=ProviderTask("image-provider-1", "pending")),
        ),
    ):
        owner_headers = _admin_headers(client)
        session_id = _create_session(client, owner_headers)
        created = client.post(
            "/image-generation/tasks",
            headers=owner_headers,
            json={"session_id": session_id, "prompt": "海边日落"},
        ).json()
        member_headers = _create_member_headers(client, owner_headers)

        assert (
            client.get(
                f"/image-generation/tasks/{created['id']}", headers=member_headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/image-generation/tasks/{created['id']}/edit-source",
                headers=member_headers,
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/image-generation/tasks",
                headers=member_headers,
                params={"session_id": session_id},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/image-generation/tasks",
                headers=member_headers,
                json={"session_id": session_id, "prompt": "越权"},
            ).status_code
            == 404
        )


def test_get_task_mirrors_succeeded_provider_image_once(tmp_path: Path):
    """Polling a task to succeeded must trigger the durable image mirror."""
    created = ProviderTask("image-provider-mirror", "pending")
    succeeded = ProviderTask(
        "image-provider-mirror",
        "succeeded",
        image_url="https://p4-fdl.klingai.com/ksc2/result.png",
    )
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(return_value=created),
        ),
        patch(
            "app.api.image_generation.image_service.poll_provider_task",
            new=AsyncMock(side_effect=[succeeded]),
        ),
        patch(
            "app.api.image_generation.media_task_dispatch._mirror_succeeded_image",
            new=AsyncMock(),
        ) as mirror,
        patch(
            "app.api.image_generation.image_service.fetch_context_image_data",
            new=AsyncMock(),
        ),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created_response = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "海边花店"},
        )
        assert created_response.status_code == 201
        task_id = created_response.json()["id"]

        completed = client.get(
            f"/image-generation/tasks/{task_id}", headers=headers
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "succeeded"

        mirror.assert_awaited_once()
        call = mirror.await_args
        assert call is not None
        mirrored_task, kwargs = call.args[0], call.kwargs
        assert mirrored_task.id == task_id
        assert kwargs["provider_image_url"] == (
            "https://p4-fdl.klingai.com/ksc2/result.png"
        )

        # A second poll observes the terminal state and must not re-mirror.
        mirror.reset_mock()
        again = client.get(f"/image-generation/tasks/{task_id}", headers=headers)
        assert again.status_code == 200
        assert again.json()["status"] == "succeeded"
        mirror.assert_not_awaited()


def test_task_image_endpoint_serves_cached_bytes(tmp_path: Path):
    """The same-origin image endpoint returns cache bytes to the owner."""
    png_bytes = _valid_png_bytes()
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.read_cached_image_bytes",
            return_value=png_bytes,
        ),
        patch(
            "app.api.image_generation.image_service.image_cache_is_available",
            return_value=True,
        ),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        from app.core import database as db_mod
        from app.models.auth import User
        from sqlalchemy import select

        with db_mod.SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            owner_id = admin.id
        from app.models.chat import ChatMessage, ChatSession
        from sqlalchemy import update as sa_update

        with db_mod.SessionLocal() as db:
            db.execute(
                sa_update(ChatSession)
                .where(ChatSession.id == session_id)
                .values(owner_user_id=owner_id)
            )
            user_msg = ChatMessage(
                id="chm-serve-u",
                owner_user_id=owner_id,
                session_id=session_id,
                role="user",
                content="生成一张图",
                created_at=now(),
            )
            assistant_msg = ChatMessage(
                id="chm-serve-a",
                owner_user_id=owner_id,
                session_id=session_id,
                role="assistant",
                content="图片生成任务已完成。",
                created_at=now(),
            )
            db.add_all([user_msg, assistant_msg])
            db.flush()
            task = ImageGenerationTask(
                id="igt-serve-1",
                owner_user_id=owner_id,
                chat_session_id=session_id,
                user_message_id="chm-serve-u",
                assistant_message_id="chm-serve-a",
                operation="generate",
                model="kling/kling-v3-image-generation",
                aspect_ratio="1:1",
                resolution="2k",
                prompt="测试",
                status="succeeded",
                image_url="https://p4-fdl.klingai.com/ksc2/result.png",
                image_cache_uri="minio://dgmeta/k1",
                created_at=now(),
                updated_at=now(),
                completed_at=now(),
            )
            db.add(task)
            db.commit()

        response = client.get(
            "/image-generation/tasks/igt-serve-1/image",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/")
        assert response.content == png_bytes

        # Foreign users must not reach the image.
        member_headers = _create_member_headers(client, headers)
        denied = client.get(
            "/image-generation/tasks/igt-serve-1/image",
            headers=member_headers,
        )
        assert denied.status_code == 404


def test_poll_mirrors_succeeded_image_with_real_helper(tmp_path: Path):
    """Polling to succeeded persists image_cache_uri via the real mirror.

    Only the provider boundary is mocked; the mirror helper runs for real so
    the durable cache reference is proven to reach the database row.
    """
    png_bytes = _valid_png_bytes()
    created = ProviderTask("image-provider-real-mirror", "pending")
    succeeded = ProviderTask(
        "image-provider-real-mirror",
        "succeeded",
        image_url="https://p4-fdl.klingai.com/ksc2/result.png",
    )
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.create_provider_task",
            new=AsyncMock(return_value=created),
        ),
        patch(
            "app.api.image_generation.image_service.poll_provider_task",
            new=AsyncMock(side_effect=[succeeded]),
        ),
        patch(
            "app.api.image_generation.image_service.fetch_context_image_data",
            new=AsyncMock(),
        ),
        patch(
            "app.api.image_generation.media_task_dispatch.SessionLocal",
        ) as dispatch_session_local,
        patch(
            "app.api.image_generation.media_task_dispatch.image_generation.image_cache_is_available",
            return_value=True,
        ),
        patch(
            "app.api.image_generation.media_task_dispatch.image_generation.mirror_provider_image",
            new=AsyncMock(return_value="minio://dgmeta/image-generation/results/u/igt-m"),
        ),
        patch(
            "app.api.image_generation.media_task_dispatch.image_generation.cache_image_bytes",
            return_value="minio://dgmeta/image-generation/results/u/igt-m",
        ),
        patch(
            "app.api.image_generation.image_service.read_cached_image_bytes",
            return_value=png_bytes,
        ),
        patch(
            "app.api.image_generation.image_service._image_content_type",
            return_value="image/png",
        ),
    ):
        # Let the mirror helper's own SessionLocal update actually commit to
        # the same SQLite file by pointing it at the app database module.
        import app.core.database as db_mod

        def _real_session_local():
            return db_mod.SessionLocal()

        # Restore real session for the helper (patch above only guards the
        # production code path from accidentally opening extra handles).
        dispatch_session_local.side_effect = _real_session_local

        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created_response = client.post(
            "/image-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "海边花店"},
        )
        assert created_response.status_code == 201
        task_id = created_response.json()["id"]

        completed = client.get(f"/image-generation/tasks/{task_id}", headers=headers)
        assert completed.status_code == 200
        assert completed.json()["status"] == "succeeded"

        with database.SessionLocal() as db:
            stored = db.scalar(
                select(ImageGenerationTask).where(ImageGenerationTask.id == task_id)
            )
            assert stored is not None
            assert stored.image_cache_uri == (
                "minio://dgmeta/image-generation/results/u/igt-m"
            )
            assert stored.status == "succeeded"

        # A second poll on the terminal task must not re-mirror.
        mirror_provider = (
            "app.api.image_generation.image_service.mirror_provider_image"
        )
        with patch(mirror_provider, new=AsyncMock()) as mirror_spy:
            again = client.get(f"/image-generation/tasks/{task_id}", headers=headers)
            assert again.status_code == 200
            assert again.json()["status"] == "succeeded"
            mirror_spy.assert_not_awaited()


def test_edit_source_prefers_durable_cache_when_provider_url_expired(
    tmp_path: Path,
):
    """A succeeded task whose provider URL is dead still edits from cache."""
    png_bytes = _valid_png_bytes()
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.image_generation.image_service.read_cached_image_bytes",
            return_value=png_bytes,
        ),
        patch(
            "app.api.image_generation.media_task_dispatch.SessionLocal",
        ) as dispatch_session_local,
        patch(
            "app.api.image_generation.media_task_dispatch.image_generation.mirror_provider_image",
            new=AsyncMock(return_value=None),
        ),
    ):
        import app.core.database as db_mod

        dispatch_session_local.side_effect = lambda: db_mod.SessionLocal()

        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        from app.models.auth import User
        from app.models.chat import ChatMessage, ChatSession
        from sqlalchemy import select as sa_select
        from sqlalchemy import update as sa_update

        with database.SessionLocal() as db:
            owner_id = db.scalar(
                sa_select(User.id).where(User.username == "admin")
            )
            db.execute(
                sa_update(ChatSession)
                .where(ChatSession.id == session_id)
                .values(owner_user_id=owner_id)
            )
            db.add_all(
                [
                    ChatMessage(
                        id="chm-exp-u",
                        owner_user_id=owner_id,
                        session_id=session_id,
                        role="user",
                        content="生成一张图",
                        created_at=now(),
                    ),
                    ChatMessage(
                        id="chm-exp-a",
                        owner_user_id=owner_id,
                        session_id=session_id,
                        role="assistant",
                        content="图片生成任务已完成。",
                        created_at=now(),
                    ),
                ]
            )
            db.flush()
            db.add(
                ImageGenerationTask(
                    id="igt-expired-url",
                    owner_user_id=owner_id,
                    chat_session_id=session_id,
                    user_message_id="chm-exp-u",
                    assistant_message_id="chm-exp-a",
                    operation="generate",
                    model="kling/kling-v3-image-generation",
                    aspect_ratio="1:1",
                    resolution="2k",
                    prompt="测试",
                    status="succeeded",
                    # Provider URL from hours ago: expired/signed-out.
                    image_url="https://p4-fdl.klingai.com/ksc2/old-expired.png",
                    image_cache_uri="minio://dgmeta/image-generation/results/u/igt-m",
                    created_at=now(),
                    updated_at=now(),
                    completed_at=now(),
                )
            )
            db.commit()

        response = client.get(
            "/image-generation/tasks/igt-expired-url/edit-source",
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()["source_image"]
        assert body["mimeType"] == "image/png"
        assert body["data"].startswith("iVBOR")
