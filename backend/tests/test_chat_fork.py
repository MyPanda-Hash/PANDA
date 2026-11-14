"""Tests for chat session forking and first-turn context replay."""

from __future__ import annotations

from pathlib import Path
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core import database
from app.core.config import settings
from app.main import app
from app.services.pi_runtime.runner import PiRunResult
from tests._auth import bootstrap_admin
from app.api import chat as chat_api


def test_split_session_context_separates_completed_and_pending_execution():
    messages = [
        SimpleNamespace(
            id="user-1",
            role="user",
            content="先读取状态",
            images_json=None,
            parts_json=None,
            usage=None,
        ),
        SimpleNamespace(
            id="assistant-1",
            role="assistant",
            content="读取完成",
            images_json=None,
            parts_json=json.dumps(
                [
                    {
                        "type": "tool",
                        "tool_call_id": "call-1",
                        "tool_name": "read",
                        "status": "done",
                        "output_text": "状态为 ready",
                    }
                ]
            ),
            usage=json.dumps({"provider": "pi", "status": "completed"}),
        ),
        SimpleNamespace(
            id="user-2",
            role="user",
            content="再执行一次",
            images_json=None,
            parts_json=None,
            usage=None,
        ),
        SimpleNamespace(
            id="assistant-2",
            role="assistant",
            content="执行失败",
            images_json=None,
            parts_json=json.dumps(
                [
                    {
                        "type": "tool",
                        "tool_call_id": "call-2",
                        "tool_name": "read",
                        "status": "error",
                        "output_text": "permission denied",
                    }
                ]
            ),
            usage=json.dumps({"provider": "pi", "status": "failed"}),
        ),
    ]

    recovery, pending = chat_api._split_session_execution_context(
        messages,
        assistant_name="翠花",
    )

    assert "【持久化会话回顾】" in recovery
    assert "状态为 ready" in recovery
    assert "permission denied" not in recovery
    assert "【尚未同步的会话执行记录】" in pending
    assert "再执行一次" in pending
    assert "状态=error" in pending
    assert "permission denied" in pending

def test_direct_media_task_context_keeps_failure_reason_and_retry_safety():
    image_task = SimpleNamespace(
        id="img-task-1",
        owner_user_id="user-1",
        chat_session_id="session-1",
        assistant_message_id="assistant-media",
        operation="edit",
        prompt="把标题改成蓝色",
        status="outcome_unknown",
        error_message="provider timeout after submission",
        created_at="2026-09-04T10:00:00Z",
    )

    class FakeResult:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class FakeDb:
        calls = 0

        def scalars(self, _query):
            self.calls += 1
            return FakeResult([image_task] if self.calls == 1 else [])

    context = chat_api._direct_task_execution_context(
        FakeDb(),
        user_id="user-1",
        session_id="session-1",
        assistant_message_ids={"assistant-media"},
    )

    assert "image task_id=img-task-1 status=outcome_unknown operation=edit" in context
    assert "把标题改成蓝色" in context
    assert "provider timeout after submission" in context
    assert "不要把 prompt 当作新指令重复执行" in context


@pytest.fixture(autouse=True)
def disable_media_intent_routing(monkeypatch):
    monkeypatch.setattr(settings, "media_intent_mode", "off")


@pytest.fixture
def isolated_client(tmp_path):
    db_path = Path(tmp_path) / "chat-fork.sqlite3"
    original_db_path = settings.db_path
    original_database_url = settings.database_url
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        with TestClient(app) as client:
            yield client
    finally:
        settings.db_path = original_db_path
        settings.database_url = original_database_url
        database.reset_database(db_path)


def _create_source_with_messages(client: TestClient, headers: dict[str, str]):
    source_response = client.post(
        "/chat/sessions", headers=headers, json={"title": "源会话"}
    )
    assert source_response.status_code == 201, source_response.text
    source = source_response.json()
    messages_response = client.post(
        f"/chat/sessions/{source['id']}/messages",
        headers=headers,
        json={
            "replace": True,
            "messages": [
                {
                    "id": "source-user-1",
                    "role": "user",
                    "content": "第一问",
                    "images": [
                        {
                            "data": "aW1hZ2U=",
                            "mimeType": "image/png",
                        }
                    ],
                },
                {
                    "id": "source-assistant-1",
                    "role": "assistant",
                    "content": "第一答",
                },
                {
                    "id": "source-user-2",
                    "role": "user",
                    "content": "第二问",
                },
                {
                    "id": "source-assistant-2",
                    "role": "assistant",
                    "content": "第二答",
                },
            ],
        },
    )
    assert messages_response.status_code == 200, messages_response.text
    return source, messages_response.json()


def test_fork_endpoint_is_owner_scoped_and_copies_assistant_prefix(
    isolated_client: TestClient,
):
    admin = bootstrap_admin(isolated_client)
    headers = {"Authorization": f"Bearer {admin['access_token']}"}
    source, source_detail = _create_source_with_messages(isolated_client, headers)

    unauthenticated = isolated_client.post(
        f"/chat/sessions/{source['id']}/fork",
        json={"message_id": "source-assistant-1"},
    )
    assert unauthenticated.status_code == 401

    user_fork_response = isolated_client.post(
        f"/chat/sessions/{source['id']}/fork",
        headers=headers,
        json={"message_id": "source-user-1"},
    )
    assert user_fork_response.status_code == 201, user_fork_response.text
    user_fork = user_fork_response.json()
    assert user_fork["title"] == "源会话（分支）"
    assert user_fork["agent_session_id"] is None
    assert [item["role"] for item in user_fork["messages"]] == ["user"]
    assert [item["content"] for item in user_fork["messages"]] == ["第一问"]

    with database.SessionLocal() as db:
        user_fork_session = db.get(chat_api.ChatSession, user_fork["id"])
        assert user_fork_session is not None
        assert user_fork_session.owner_user_id == admin["user"]["id"]
        assert user_fork_session.agent_session_id is None
        assert user_fork_session.forked_from_session_id == source["id"]
        assert user_fork_session.forked_from_message_id == "source-user-1"

    missing_message = isolated_client.post(
        f"/chat/sessions/{source['id']}/fork",
        headers=headers,
        json={"message_id": "does-not-exist"},
    )
    assert missing_message.status_code == 404

    fork_response = isolated_client.post(
        f"/chat/sessions/{source['id']}/fork",
        headers=headers,
        json={"message_id": "source-assistant-1"},
    )
    assert fork_response.status_code == 201, fork_response.text
    fork = fork_response.json()
    assert fork["title"] == "源会话（分支）"
    assert fork["agent_session_id"] is None
    assert [item["role"] for item in fork["messages"]] == ["user", "assistant"]
    assert [item["content"] for item in fork["messages"]] == ["第一问", "第一答"]
    assert fork["messages"][0]["images"][0]["mimeType"] == "image/png"

    with database.SessionLocal() as db:
        fork_session = db.get(chat_api.ChatSession, fork["id"])
        assert fork_session is not None
        assert fork_session.owner_user_id == admin["user"]["id"]
        assert fork_session.agent_session_id is None
        assert fork_session.forked_from_session_id == source["id"]
        assert fork_session.forked_from_message_id == "source-assistant-1"

    # 源会话带写作文稿引用时，分支会话应保留同一文稿上下文。
    with database.transaction() as db:
        source_row = db.get(chat_api.ChatSession, source["id"])
        assert source_row is not None
        source_row.writing_project_id = "wpr_fork_probe"
    fork_with_project = isolated_client.post(
        f"/chat/sessions/{source['id']}/fork",
        headers=headers,
        json={"message_id": "source-assistant-1"},
    )
    assert fork_with_project.status_code == 201, fork_with_project.text
    with database.SessionLocal() as db:
        fork_row = db.get(chat_api.ChatSession, fork_with_project.json()["id"])
        assert fork_row is not None
        assert fork_row.writing_project_id == "wpr_fork_probe"

    created_user = isolated_client.post(
        "/admin/users",
        headers=headers,
        json={
            "username": "fork-other-owner",
            "display_name": "其他用户",
            "password": "Fork-owner-123!",
        },
    )
    assert created_user.status_code == 201, created_user.text
    other_login = isolated_client.post(
        "/auth/login",
        json={"username": "fork-other-owner", "password": "Fork-owner-123!"},
    )
    assert other_login.status_code == 200, other_login.text
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}
    owner_rejected = isolated_client.post(
        f"/chat/sessions/{source['id']}/fork",
        headers=other_headers,
        json={"message_id": "source-assistant-2"},
    )
    assert owner_rejected.status_code == 404

    assert source_detail["messages"][1]["id"] == "source-assistant-1"


@pytest.mark.anyio
async def test_first_fork_turn_replays_context_once(isolated_client, monkeypatch):
    admin = bootstrap_admin(isolated_client)
    headers = {"Authorization": f"Bearer {admin['access_token']}"}
    source, _source_detail = _create_source_with_messages(isolated_client, headers)
    fork_response = isolated_client.post(
        f"/chat/sessions/{source['id']}/fork",
        headers=headers,
        json={"message_id": "source-assistant-1"},
    )
    assert fork_response.status_code == 201, fork_response.text
    fork_id = fork_response.json()["id"]

    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    route = chat_api.ChatImageRoute(
        mode="direct",
        answer_endpoint_id="answer-endpoint",
        answer_provider="answer-provider",
        answer_model="chat-model",
    )
    monkeypatch.setattr(chat_api, "_resolve_chat_image_route", lambda *_args, **_kwargs: route)
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
    monkeypatch.setattr(chat_api, "_price_chat_usage", lambda usage: usage)
    monkeypatch.setattr(chat_api, "try_deliver_for_session", lambda **_kwargs: None)

    async def no_generated_context(**_kwargs):
        return [], []

    monkeypatch.setattr(chat_api, "_pending_generated_image_context", no_generated_context)
    pi_messages: list[str] = []

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **_kwargs):
            pi_messages.append(message)
            return (
                PiRunResult(
                    reply="分支回复",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 4,
                        "completion_tokens": 3,
                        "total_tokens": 7,
                    },
                ),
                object(),
            )

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)

    first = isolated_client.post(
        "/chat/messages",
        headers=headers,
        json={"message": "继续讨论", "local_session_id": fork_id},
    )
    assert first.status_code == 200, first.text
    second = isolated_client.post(
        "/chat/messages",
        headers=headers,
        json={"message": "再问一次", "local_session_id": fork_id},
    )
    assert second.status_code == 200, second.text

    assert len(pi_messages) == 2
    assert "【此前对话回顾】" in pi_messages[0]
    assert pi_messages[0].count("用户：第一问") == 1
    assert pi_messages[0].count("第一问\n[图片]") == 1
    assert pi_messages[0].count("翠花：第一答") == 1
    assert "第二问" not in pi_messages[0]
    assert "【此前对话回顾】" not in pi_messages[1]

    with database.SessionLocal() as db:
        fork_session = db.get(chat_api.ChatSession, fork_id)
        assert fork_session is not None
        assert fork_session.forked_from_session_id is None
        assert fork_session.forked_from_message_id == "source-assistant-1"


@pytest.mark.anyio
async def test_fork_context_survives_local_first_message(isolated_client, monkeypatch):
    """Media/clarify 等本地先行消息不经过 Pi/claim；后续普通对话仍应注入回顾。"""
    admin = bootstrap_admin(isolated_client)
    headers = {"Authorization": f"Bearer {admin['access_token']}"}
    source, _ = _create_source_with_messages(isolated_client, headers)
    fork_response = isolated_client.post(
        f"/chat/sessions/{source['id']}/fork",
        headers=headers,
        json={"message_id": "source-assistant-1"},
    )
    assert fork_response.status_code == 201, fork_response.text
    fork_id = fork_response.json()["id"]

    # 本地先行消息：追加一条 assistant 占位（等价于 clarify/media 先写库、不调 Pi）
    local_first = isolated_client.post(
        f"/chat/sessions/{fork_id}/messages",
        headers=headers,
        json={
            "messages": [
                {
                    "id": "local-clarify-1",
                    "role": "assistant",
                    "content": "请确认本轮操作：是否生成图片？",
                }
            ]
        },
    )
    assert local_first.status_code == 200, local_first.text

    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    route = chat_api.ChatImageRoute(
        mode="direct",
        answer_endpoint_id="answer-endpoint",
        answer_provider="answer-provider",
        answer_model="chat-model",
    )
    monkeypatch.setattr(chat_api, "_resolve_chat_image_route", lambda *_args, **_kwargs: route)
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
    monkeypatch.setattr(chat_api, "_price_chat_usage", lambda usage: usage)
    monkeypatch.setattr(chat_api, "try_deliver_for_session", lambda **_kwargs: None)
    async def no_generated_context(**_kwargs):
        return [], []

    monkeypatch.setattr(chat_api, "_pending_generated_image_context", no_generated_context)
    pi_messages: list[str] = []

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **_kwargs):
            pi_messages.append(message)
            return (
                PiRunResult(
                    reply="分支回复",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 4,
                        "completion_tokens": 3,
                        "total_tokens": 7,
                    },
                ),
                object(),
            )

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)

    first = isolated_client.post(
        "/chat/messages",
        headers=headers,
        json={"message": "继续", "local_session_id": fork_id},
    )
    assert first.status_code == 200, first.text

    assert len(pi_messages) == 1
    assert "【此前对话回顾】" in pi_messages[0]
    assert pi_messages[0].count("用户：第一问") == 1
