from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core import database
from app.core.config import settings
from app.core.security import now
from app.main import app
from app.models.chat import ChatDispatchDecision
from app.models.video_generation import VideoGenerationTask
from app.services import video_generation as video_service
from app.services import media_task_dispatch
from app.services.video_generation import ProviderTask
from fastapi.testclient import TestClient
from tests._auth import bootstrap_admin_response


@contextmanager
def _configured_client(tmp_path: Path, *, configured: bool = True):
    original_path = settings.db_path
    original_url = settings.database_url
    original_key = settings.dashscope_api_key
    original_workspace = settings.dashscope_video_workspace_id
    db_path = tmp_path / "video-generation.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    settings.dashscope_api_key = "video-test-key" if configured else ""
    settings.dashscope_video_workspace_id = "video-test-workspace" if configured else ""
    try:
        with TestClient(app) as client:
            yield client
    finally:
        settings.db_path = original_path
        settings.database_url = original_url
        settings.dashscope_api_key = original_key
        settings.dashscope_video_workspace_id = original_workspace
        database.reset_database(db_path)


def _admin_headers(client: TestClient) -> dict[str, str]:
    login = bootstrap_admin_response(client)
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create_session(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post("/chat/sessions", headers=headers, json={"title": "视频测试"})
    assert response.status_code == 201
    return response.json()["id"]


def _create_member_headers(client: TestClient, admin_headers: dict[str, str]) -> dict[str, str]:
    invited = client.post(
        "/admin/users",
        headers=admin_headers,
        json={
            "username": "video-member",
            "email": "video-member@example.com",
            "display_name": "视频成员",
        },
    )
    assert invited.status_code == 201
    accepted = client.post(
        f"/auth/invitations/{invited.json()['invite_token']}/accept",
        json={
            "password": "Video-member-123",
            "password_confirmation": "Video-member-123",
        },
    )
    assert accepted.status_code == 200
    login = client.post(
        "/auth/login",
        json={"username": "video-member", "password": "Video-member-123"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_video_generation_requires_credentials(tmp_path: Path):
    with _configured_client(tmp_path, configured=False) as client:
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)

        response = client.post(
            "/video-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "月光下的猫", "duration": 5},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "视频生成功能未配置"


def test_video_generation_uses_persisted_chat_context(tmp_path: Path):
    created = ProviderTask("provider-context", "pending")
    with _configured_client(tmp_path) as client, patch(
        "app.api.video_generation.video_service.create_provider_task",
        new=AsyncMock(return_value=created),
    ) as create_provider:
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        context_response = client.post(
            f"/chat/sessions/{session_id}/messages",
            headers=headers,
            json={
                "messages": [
                    {"role": "user", "content": "我要制作一段品牌宣传视频。"},
                    {
                        "role": "assistant",
                        "content": "品牌名称是云杉，主色调为青绿色，场景在杭州。",
                    },
                ]
            },
        )
        assert context_response.status_code == 200

        response = client.post(
            "/video-generation/tasks",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "设计一段适合网页首屏播放的视频，保留上文信息。",
                "duration": 5,
            },
        )

    assert response.status_code == 201
    assert response.json()["prompt"] == (
        "设计一段适合网页首屏播放的视频，保留上文信息。"
    )
    provider_prompt = create_provider.await_args.kwargs["prompt"]
    assert "品牌名称是云杉，主色调为青绿色，场景在杭州。" in provider_prompt
    assert "设计一段适合网页首屏播放的视频，保留上文信息。" in provider_prompt
    assert "【当前会话上下文】" in provider_prompt
    assert "【本次视频生成要求】" in provider_prompt


def test_video_generation_creates_chat_messages_and_polls_task(tmp_path: Path):
    created = ProviderTask("provider-task-1", "pending")
    running = ProviderTask("provider-task-1", "running")
    succeeded = ProviderTask(
        "provider-task-1", "succeeded", video_url="https://example.com/video.mp4"
    )
    with _configured_client(tmp_path) as client, patch(
        "app.api.video_generation.video_service.create_provider_task",
        new=AsyncMock(return_value=created),
    ) as create_provider, patch(
        "app.api.video_generation.video_service.poll_provider_task",
        new=AsyncMock(side_effect=[running, succeeded]),
    ) as poll_provider:
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        response = client.post(
            "/video-generation/tasks",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "月光下的猫",
                "mode": "pro",
                "duration": 8,
            },
        )

        assert response.status_code == 201
        task = response.json()
        assert task["status"] == "pending"
        assert task["model"] == "kling/kling-v3-video-generation"
        assert task["mode"] == "pro"
        assert task["duration"] == 8
        assert task["video_url"] is None
        assert task["user_message_id"]
        assert task["assistant_message_id"]
        create_provider.assert_awaited_once_with(
            prompt="月光下的猫", mode="pro", duration=8
        )

        detail = client.get(
            f"/video-generation/tasks/{task['id']}", headers=headers
        )
        assert detail.status_code == 200
        assert detail.json()["status"] == "running"
        assert poll_provider.await_count == 1

        completed = client.get(
            f"/video-generation/tasks/{task['id']}", headers=headers
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "succeeded"
        assert completed.json()["video_url"] == "https://example.com/video.mp4"

        session = client.get(
            f"/chat/sessions/{session_id}", headers=headers
        ).json()
        assert [message["role"] for message in session["messages"]] == [
            "user",
            "assistant",
        ]
        assert session["messages"][0]["content"] == "月光下的猫"


def test_get_task_reconciles_expired_submitting_without_provider_id(
    tmp_path: Path,
):
    created = ProviderTask("video-provider-stale-read", "pending")
    with _configured_client(tmp_path) as client, patch(
        "app.api.video_generation.video_service.create_provider_task",
        new=AsyncMock(return_value=created),
    ), patch(
        "app.api.video_generation.video_service.poll_provider_task",
        new=AsyncMock(side_effect=AssertionError("stale task must not poll")),
    ) as poll_provider:
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        response = client.post(
            "/video-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "过期视频"},
        )
        assert response.status_code == 201
        task_id = response.json()["id"]

        with database.SessionLocal() as db:
            task = db.get(VideoGenerationTask, task_id)
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

        stale = client.get(f"/video-generation/tasks/{task_id}", headers=headers)
        assert stale.status_code == 200
        assert stale.json()["status"] == "outcome_unknown"
        poll_provider.assert_not_awaited()

        with database.SessionLocal() as db:
            task = db.get(VideoGenerationTask, task_id)
            assert task is not None
            assert task.status == "outcome_unknown"


def test_video_poll_retries_result_commit_without_polling_twice(
    tmp_path: Path, monkeypatch
):
    initial = ProviderTask("video-provider-commit-retry", "pending")
    completed = ProviderTask(
        "video-provider-commit-retry",
        "succeeded",
        video_url="https://p4-fdl.klingai.com/result.mp4",
    )
    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.video_generation.video_service.create_provider_task",
            new=AsyncMock(return_value=initial),
        ),
        patch(
            "app.api.video_generation.video_service.poll_provider_task",
            new=AsyncMock(return_value=completed),
        ) as poll_provider,
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created = client.post(
            "/video-generation/tasks",
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
                    raise RuntimeError("transient video result commit failure")
                original_commit()

            setattr(db, "commit", commit_once_fails)
            return db

        monkeypatch.setattr(media_task_dispatch, "SessionLocal", flaky_session_local)
        result = client.get(f"/video-generation/tasks/{task_id}", headers=headers)

        assert result.status_code == 200
        assert result.json()["status"] == "succeeded"
        assert commit_attempts == 2
        poll_provider.assert_awaited_once()


def test_video_poll_error_does_not_overwrite_concurrent_terminal_success(
    tmp_path: Path,
):
    initial = ProviderTask("video-provider-race", "pending")

    async def poll_after_concurrent_success(_provider_task_id: str):
        with database.SessionLocal() as db:
            task = db.get(VideoGenerationTask, task_id)
            assert task is not None
            task.status = "succeeded"
            task.video_url = "https://p4-fdl.klingai.com/race-success.mp4"
            task.completed_at = now()
            db.commit()
        raise video_service.VideoGenerationProviderError("HTTP 500")

    with (
        _configured_client(tmp_path) as client,
        patch(
            "app.api.video_generation.video_service.create_provider_task",
            new=AsyncMock(return_value=initial),
        ),
        patch(
            "app.api.video_generation.video_service.poll_provider_task",
            new=AsyncMock(side_effect=poll_after_concurrent_success),
        ),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created = client.post(
            "/video-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "并发结果"},
        )
        assert created.status_code == 201
        task_id = created.json()["id"]

        result = client.get(
            f"/video-generation/tasks/{task_id}", headers=headers
        )

        assert result.status_code == 200
        assert result.json()["status"] == "succeeded"


def test_video_generation_accepts_first_frame_image(tmp_path: Path):
    created = ProviderTask(
        "provider-image-1",
        "pending",
        source_file_ref="bailian://file-frame-1",
    )
    with _configured_client(tmp_path) as client, patch(
        "app.api.video_generation.video_service.create_provider_task",
        new=AsyncMock(return_value=created),
    ) as create_provider:
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        response = client.post(
            "/video-generation/tasks",
            headers=headers,
            json={
                "session_id": session_id,
                "prompt": "让图片中的人物动起来",
                "mode": "std",
                "duration": 5,
                "first_frame": {
                    "type": "image",
                    "data": "ZnJhbWU=",
                    "mimeType": "image/png",
                },
            },
        )
        stored_session = client.get(
            f"/chat/sessions/{session_id}", headers=headers
        ).json()

    assert response.status_code == 201
    assert response.json()["model"] == "kling/kling-v3-omni-video-generation"
    create_provider.assert_awaited_once_with(
        prompt="让图片中的人物动起来",
        mode="std",
        duration=5,
        first_frame={
            "type": "image",
            "data": "ZnJhbWU=",
            "mimeType": "image/png",
        },
    )
    assert stored_session["messages"][0]["images"] == [
        {
            "type": "image",
            "data": "ZnJhbWU=",
            "mimeType": "image/png",
        }
    ]

def test_video_generation_failed_task_can_be_retried(tmp_path: Path):
    initial = ProviderTask("provider-task-old", "failed", error_message="上游内部细节")
    retried = ProviderTask("provider-task-new", "pending")
    orphan = ProviderTask("provider-task-orphan", "pending")
    with _configured_client(tmp_path) as client, patch(
        "app.api.video_generation.video_service.create_provider_task",
        new=AsyncMock(side_effect=[initial, retried, orphan]),
    ) as create_provider, patch(
        "app.api.video_generation.video_service.poll_provider_task",
        new=AsyncMock(return_value=initial),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created = client.post(
            "/video-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "一只小狗", "duration": 4},
        ).json()
        failed = client.get(
            f"/video-generation/tasks/{created['id']}", headers=headers
        )
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert failed.json()["error_message"] == "视频生成失败，请重试。"

        retried_response = client.post(
            f"/video-generation/tasks/{created['id']}/retry", headers=headers
        )
        assert retried_response.status_code == 200
        assert retried_response.json()["status"] == "pending"
        assert retried_response.json()["error_message"] is None
        create_provider.assert_awaited_with(
            prompt="一只小狗", mode="std", duration=4
        )
        duplicate = client.post(
            f"/video-generation/tasks/{created['id']}/retry", headers=headers
        )
        assert duplicate.status_code == 409
        assert create_provider.await_count == 2
        session = client.get(
            f"/chat/sessions/{session_id}", headers=headers
        ).json()
        assert session["messages"][1]["content"] == "视频生成任务已重新提交，翠花正在处理中。"

def test_unknown_provider_task_is_reported_as_expired_failure(tmp_path: Path):
    with _configured_client(tmp_path) as client, patch(
        "app.api.video_generation.video_service.create_provider_task",
        new=AsyncMock(return_value=ProviderTask("provider-task-unknown", "pending")),
    ), patch(
        "app.api.video_generation.video_service.poll_provider_task",
        new=AsyncMock(
            return_value=ProviderTask(
                "provider-task-unknown",
                "unknown",
                error_message="上游内部细节",
            )
        ),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        created = client.post(
            "/video-generation/tasks",
            headers=headers,
            json={"session_id": session_id, "prompt": "过期任务", "duration": 5},
        ).json()

        response = client.get(
            f"/video-generation/tasks/{created['id']}", headers=headers
        )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_message"] == "任务已过期或不存在"


def test_video_generation_rejects_invalid_input_and_foreign_session(tmp_path: Path):
    with _configured_client(tmp_path) as client, patch(
        "app.api.video_generation.video_service.create_provider_task",
        new=AsyncMock(return_value=ProviderTask("provider-task-1", "pending")),
    ):
        headers = _admin_headers(client)
        session_id = _create_session(client, headers)
        for payload in (
            {"session_id": session_id, "prompt": "", "duration": 5},
            {"session_id": session_id, "prompt": "月光下的猫", "mode": "bad", "duration": 5},
            {"session_id": session_id, "prompt": "月光下的猫", "duration": 16},
        ):
            assert (
                client.post("/video-generation/tasks", headers=headers, json=payload).status_code
                == 422
            )

        foreign = client.get(
            "/video-generation/tasks/not-a-task", headers=headers
        )
        assert foreign.status_code == 404


def test_video_generation_denies_cross_user_task_and_session_access(tmp_path: Path):
    with _configured_client(tmp_path) as client, patch(
        "app.api.video_generation.video_service.create_provider_task",
        new=AsyncMock(return_value=ProviderTask("provider-task-1", "pending")),
    ):
        owner_headers = _admin_headers(client)
        session_id = _create_session(client, owner_headers)
        created = client.post(
            "/video-generation/tasks",
            headers=owner_headers,
            json={"session_id": session_id, "prompt": "海边日落", "duration": 5},
        ).json()
        member_headers = _create_member_headers(client, owner_headers)

        assert (
            client.get(
                f"/video-generation/tasks/{created['id']}", headers=member_headers
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/video-generation/tasks",
                headers=member_headers,
                params={"session_id": session_id},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/video-generation/tasks",
                headers=member_headers,
                json={"session_id": session_id, "prompt": "越权", "duration": 5},
            ).status_code
            == 404
        )
