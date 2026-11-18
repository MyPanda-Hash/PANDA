"""Unit tests for chat API (Pi-mode only)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api import chat as chat_api
from app.core.config import settings
from app.main import app
from app.repositories import chat as chat_repo
from app.schemas.chat import ChatImageContent
from app.services.agent_capability import decode_capability_token
from app.services.pi_runtime.runner import PiRpcError
from tests._auth import bootstrap_admin


def test_chat_image_route_silently_uses_vision_endpoint_for_text_only_model(
    monkeypatch,
):
    answer_endpoint = SimpleNamespace(
        id="answer-endpoint",
        provider="answer-provider",
        model="text-model",
        capability="chat",
        supports_vision=False,
        allowed_data_level="internal",
    )
    vision_endpoint = SimpleNamespace(
        id="vision-endpoint",
        provider="vision-provider",
        model="vision-model",
        capability="vision",
        supports_vision=True,
        allowed_data_level="internal",
    )
    resolved_data_levels: list[str] = []
    monkeypatch.setattr(settings, "vision_fallback_enabled", False)
    monkeypatch.setattr(
        chat_api,
        "resolve_model_endpoint",
        lambda *args, **kwargs: answer_endpoint,
    )
    monkeypatch.setattr(
        chat_api,
        "resolve_vision_endpoint",
        lambda *args, **kwargs: (
            resolved_data_levels.append(kwargs["data_level"]) or vision_endpoint
        ),
    )

    route = chat_api._resolve_chat_image_route(
        MagicMock(), model="text-model", image_count=1
    )

    assert route.mode == "vision_fallback"
    assert route.answer_endpoint_id == "answer-endpoint"
    assert route.vision_endpoint_id == "vision-endpoint"
    assert route.answer_model == "text-model"
    assert route.vision_model == "vision-model"
    assert resolved_data_levels == ["public"]


def test_chat_image_route_keeps_direct_path_for_vision_capable_model(monkeypatch):
    endpoint = SimpleNamespace(
        id="vision-chat-endpoint",
        provider="provider",
        model="vision-chat",
        capability="chat",
        supports_vision=True,
        allowed_data_level="internal",
        status="active",
        concurrency_limit=1,
        timeout_ms=30_000,
        secret_ref="configured",
        last_test_status="capability_failed",
    )
    monkeypatch.setattr(settings, "vision_fallback_enabled", True)
    monkeypatch.setattr(
        chat_api,
        "resolve_model_endpoint",
        lambda *args, **kwargs: endpoint,
    )
    route = chat_api._resolve_chat_image_route(
        MagicMock(), model="vision-chat", image_count=2
    )
    assert route.mode == "direct"
    assert route.vision_endpoint_id is None


def test_chat_image_route_reports_unavailable_vision_endpoint(monkeypatch):
    endpoint = SimpleNamespace(
        id="text-endpoint",
        provider="provider",
        model="text-model",
        capability="chat",
        supports_vision=False,
        allowed_data_level="internal",
    )
    monkeypatch.setattr(settings, "vision_fallback_enabled", True)
    monkeypatch.setattr(
        chat_api,
        "resolve_model_endpoint",
        lambda *args, **kwargs: endpoint,
    )
    monkeypatch.setattr(
        chat_api,
        "resolve_vision_endpoint",
        lambda *args, **kwargs: None,
    )
    with pytest.raises(HTTPException) as captured:
        chat_api._resolve_chat_image_route(
            MagicMock(), model="text-model", image_count=1
        )
    assert captured.value.status_code == 503
    assert captured.value.detail["error_code"] == "vision_endpoint_unavailable"
    assert captured.value.detail["reasons"] == ["image_support_disabled"]


def test_chat_image_route_preserves_explicit_data_level_restriction(monkeypatch):
    endpoint = SimpleNamespace(
        id="vision-endpoint",
        provider="provider",
        model="vision-model",
        capability="chat",
        status="active",
        supports_vision=True,
        allowed_data_level="internal",
        concurrency_limit=1,
        timeout_ms=30_000,
        secret_ref="configured",
        last_test_status=None,
    )
    monkeypatch.setattr(settings, "vision_fallback_enabled", False)
    monkeypatch.setattr(settings, "chat_image_ocr_fallback_enabled", False)
    monkeypatch.setattr(
        chat_api,
        "resolve_model_endpoint",
        lambda *args, **kwargs: endpoint,
    )
    monkeypatch.setattr(chat_api, "resolve_vision_endpoint", lambda *_a, **_k: None)

    with pytest.raises(HTTPException) as captured:
        chat_api._resolve_chat_image_route(
            MagicMock(),
            model="vision-model",
            image_count=1,
            data_level="restricted",
        )

    assert captured.value.status_code == 503
    assert captured.value.detail["error_code"] == "vision_endpoint_unavailable"
    assert captured.value.detail["reasons"] == ["data_level_blocked"]


def test_chat_image_route_can_explicitly_use_local_ocr_when_vision_is_unavailable(
    monkeypatch,
):
    endpoint = SimpleNamespace(
        id="text-endpoint",
        provider="provider",
        model="text-model",
        capability="chat",
        supports_vision=False,
        allowed_data_level="internal",
    )
    monkeypatch.setattr(settings, "vision_fallback_enabled", False)
    monkeypatch.setattr(settings, "chat_image_ocr_fallback_enabled", True)
    monkeypatch.setattr(
        chat_api,
        "resolve_model_endpoint",
        lambda *args, **kwargs: endpoint,
    )
    monkeypatch.setattr(
        chat_api,
        "resolve_vision_endpoint",
        lambda *args, **kwargs: None,
    )

    route = chat_api._resolve_chat_image_route(
        MagicMock(), model="text-model", image_count=1
    )

    assert route.mode == "ocr_assisted"
    assert route.answer_model == "text-model"


def test_pending_uploaded_images_recover_only_latest_owned_unfinished_turn():
    stamp = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)

    def image(value: str) -> dict[str, str]:
        return {
            "type": "image",
            "data": value,
            "mimeType": "image/png",
        }

    completed = SimpleNamespace(
        id="assistant-completed",
        role="assistant",
        owner_user_id="owner-1",
        session_id="session-1",
        usage={"status": "completed"},
        images_json="[]",
        created_at=stamp,
    )
    pending = SimpleNamespace(
        id="pending-upload",
        role="user",
        owner_user_id="owner-1",
        session_id="session-1",
        usage=None,
        images_json=json.dumps(
            [
                image("a"),
                image("a"),
                image("b"),
                image("c"),
                image("d"),
                image("e"),
            ]
        ),
        created_at=stamp.replace(minute=1),
    )
    foreign = SimpleNamespace(
        id="foreign-upload",
        role="user",
        owner_user_id="owner-2",
        session_id="session-1",
        usage=None,
        images_json=json.dumps([image("foreign")]),
        created_at=stamp.replace(minute=2),
    )
    messages = [completed, pending, foreign]

    recovered = chat_api._pending_uploaded_images(
        messages,
        user_id="owner-1",
        session_id="session-1",
        current_images=[],
        existing_images=[],
        excluded_message_id=None,
    )

    assert [item.data for item in recovered] == ["a", "b", "c", "d"]
    assert (
        chat_api._pending_uploaded_images(
            messages,
            user_id="owner-1",
            session_id="session-1",
            current_images=[ChatImageContent(data="new", mimeType="image/png")],
            existing_images=[],
            excluded_message_id=None,
        )
        == []
    )
    completed_after = SimpleNamespace(
        id="assistant-completed-after",
        role="assistant",
        owner_user_id="owner-1",
        session_id="session-1",
        usage={"status": "completed"},
        images_json="[]",
        created_at=stamp.replace(minute=3),
    )
    assert (
        chat_api._pending_uploaded_images(
            [*messages, completed_after],
            user_id="owner-1",
            session_id="session-1",
            current_images=[],
            existing_images=[],
            excluded_message_id=None,
        )
        == []
    )
    assert (
        chat_api._pending_uploaded_images(
            messages,
            user_id="owner-1",
            session_id="session-1",
            current_images=[],
            existing_images=[],
            excluded_message_id="pending-upload",
        )
        == []
    )
    latest_duplicate = SimpleNamespace(
        id="latest-duplicate",
        role="user",
        owner_user_id="owner-1",
        session_id="session-1",
        usage=None,
        images_json=json.dumps([image("a")]),
        created_at=stamp.replace(minute=4),
    )
    assert (
        chat_api._pending_uploaded_images(
            [*messages, latest_duplicate],
            user_id="owner-1",
            session_id="session-1",
            current_images=[],
            existing_images=[ChatImageContent(data="a", mimeType="image/png")],
            excluded_message_id=None,
        )
        == []
    )


def test_stream_chat_persists_blocked_image_preflight_before_http_failure(
    tmp_path,
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from app.core import database

    original_path, original_url = settings.db_path, settings.database_url
    db_path = tmp_path / "blocked-vision.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(settings, "media_intent_mode", "off")
    image = ChatImageContent(data="aW1hZ2U=", mimeType="image/png").model_dump()

    def blocked_route(*_args, **kwargs):
        assert kwargs["image_count"] == 1
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "vision_endpoint_unavailable",
                "message": "upstream raw api_key=route-secret",
                "reasons": [
                    "image_support_disabled",
                    "internal host=https://private.example",
                ],
            },
        )

    monkeypatch.setattr(chat_api, "_resolve_chat_image_route", blocked_route)
    monkeypatch.setattr(
        chat_api,
        "LocalPiAgentClient",
        lambda **_kwargs: pytest.fail("blocked preflight must not create runtime"),
    )
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            session_id = client.post(
                "/chat/sessions", headers=headers, json={"title": "截图分析"}
            ).json()["id"]
            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "读取截图",
                    "images": [image],
                    "local_session_id": session_id,
                    "client_message_id": "msg-preflight-user",
                    "client_assistant_message_id": "msg-preflight-assistant",
                },
            )
            assert response.status_code == 503
            assert response.json()["detail"]["reasons"] == ["image_support_disabled"]
            assert response.json()["detail"]["message"] == (
                "图片暂时无法解析，请移除图片后重试，或切换支持识图的模型。"
            )
            assert "route-secret" not in response.text
            saved = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
            history_image = saved["messages"][0]["images"][0]
            assert history_image == {
                "type": "image",
                "mimeType": "image/png",
                "url": (
                    f"/api/chat/sessions/{session_id}/messages/"
                    "msg-preflight-user/images/0"
                ),
            }
            image_response = client.get(
                history_image["url"].removeprefix("/api"),
                headers=headers,
            )
            assert image_response.status_code == 200
            assert image_response.content == b"image"
            assistant = saved["messages"][1]
            error_part = next(
                part for part in assistant["parts"] if part["type"] == "error"
            )
            assert json.loads(error_part["metadata_text"])["reasons"] == [
                "image_support_disabled"
            ]
            assert error_part["text"] == (
                "图片暂时无法解析，请移除图片后重试，或切换支持识图的模型。"
            )
            assert "route-secret" not in json.dumps(assistant, ensure_ascii=False)
            assert assistant["usage"]["status"] == "failed"
            assert assistant["usage"]["total_tokens"] == 0
            assert assistant["usage"]["calls"] == []
            continuation = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "继续告诉我",
                    "local_session_id": session_id,
                    "client_message_id": "msg-preflight-next-user",
                    "client_assistant_message_id": "msg-preflight-next-assistant",
                },
            )
            assert continuation.status_code == 503
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(db_path)


def test_stream_chat_preserves_vision_timeout_for_retry(
    tmp_path,
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from app.core import database

    original_path, original_url = settings.db_path, settings.database_url
    db_path = tmp_path / "vision-timeout.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(settings, "media_intent_mode", "off")
    image = ChatImageContent(data="aW1hZ2U=", mimeType="image/png").model_dump()

    def timed_out_route(*_args, **_kwargs):
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "vision_timeout",
                "message": "upstream raw api_key=route-secret",
            },
        )

    monkeypatch.setattr(chat_api, "_resolve_chat_image_route", timed_out_route)
    monkeypatch.setattr(
        chat_api,
        "LocalPiAgentClient",
        lambda **_kwargs: pytest.fail("vision timeout must not create runtime"),
    )
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            session_id = client.post(
                "/chat/sessions", headers=headers, json={"title": "超时截图"}
            ).json()["id"]
            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "看看这张图",
                    "images": [image],
                    "local_session_id": session_id,
                    "client_message_id": "msg-timeout-user",
                    "client_assistant_message_id": "msg-timeout-assistant",
                },
            )
            assert response.status_code == 503
            # 超时是可重试的瞬时失败：不能被改写成“没有可用视觉端点”，
            # 否则用户会被引向检查模型配置。
            assert response.json()["detail"]["error_code"] == "vision_timeout"
            assert response.json()["detail"]["message"] == (
                "图片识别超时，本轮未能读取图片内容。"
            )
            assert "route-secret" not in response.text
            saved = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
            error_part = next(
                part
                for part in saved["messages"][1]["parts"]
                if part["type"] == "error"
            )
            assert json.loads(error_part["metadata_text"])["error_code"] == (
                "vision_timeout"
            )
            assert error_part["text"] == "图片识别超时，本轮未能读取图片内容。"
            assert saved["messages"][0]["images"][0]["type"] == "image"
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(db_path)


@pytest.mark.anyio
async def test_stream_chat_reuses_pending_upload_when_vision_route_recovers(
    monkeypatch,
):
    from app.services.pi_runtime.runner import PiRunResult

    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(settings, "media_intent_mode", "off")
    image = ChatImageContent(data="aW1hZ2U=", mimeType="image/png")
    stamp = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    pending_user = SimpleNamespace(
        id="failed-user",
        role="user",
        owner_user_id="user-recover",
        session_id="chs_recover_1",
        content="请读取图片",
        images_json=json.dumps([image.model_dump()]),
        usage=None,
        created_at=stamp,
    )
    pending_assistant = SimpleNamespace(
        id="failed-assistant",
        role="assistant",
        owner_user_id="user-recover",
        session_id="chs_recover_1",
        content="",
        images_json="[]",
        usage={"status": "failed"},
        created_at=stamp.replace(microsecond=1),
    )
    local_session = SimpleNamespace(
        writing_project_id=None,
        preferred_model="vision-model",
        messages=[pending_user, pending_assistant],
    )
    route_counts: list[int] = []
    runtime_images: list[object] = []
    persisted: list[dict[str, Any]] = []

    class _DbContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, model, message_id):
            if model is chat_api.ChatMessage:
                return next(
                    (item for item in local_session.messages if item.id == message_id),
                    None,
                )
            if model is chat_api.ChatDispatchDecision:
                return None
            raise AssertionError(f"unexpected model lookup: {model}")

        def scalars(self, _statement):
            # 本轮是新消息，历史图片所在的旧消息不属于模板身份锚点。
            return []

    def resolve_route(*_args, **kwargs):
        route_counts.append(kwargs["image_count"])
        return chat_api.ChatImageRoute(
            mode="direct",
            answer_endpoint_id="answer-endpoint",
            answer_provider="answer-provider",
            answer_model="vision-model",
        )

    class FakePiClient:
        effective_model = "vision-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            assert "上一轮未完成消息的图片附件" in message
            runtime_images.append(kwargs["images"])
            return (
                PiRunResult(
                    reply="已继续读取图片",
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

    async def no_generated_context(**_kwargs):
        return [], []

    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _DbContext())
    monkeypatch.setattr(
        chat_api,
        "_chat_session_endpoint_context_window",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        chat_api.chat_repo,
        "get_owned_session",
        lambda *_args: local_session,
    )
    monkeypatch.setattr(
        chat_api,
        "_direct_task_execution_context",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        chat_api,
        "_pending_generated_image_context",
        no_generated_context,
    )
    monkeypatch.setattr(
        chat_api,
        "_claim_chat_session_boundary_decision",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(chat_api, "_resolve_chat_image_route", resolve_route)
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    monkeypatch.setattr(chat_api, "_start_session_title_task", lambda **_kwargs: None)
    monkeypatch.setattr(
        chat_api,
        "_persist_server_chat_messages_with_retry",
        lambda **kwargs: persisted.append(kwargs) or 1,
    )

    response = await chat_api.stream_chat_message(
        chat_api.ChatMessageRequest(
            message="继续告诉我",
            local_session_id="chs_recover_1",
            client_message_id="continuation-user",
            client_assistant_message_id="continuation-assistant",
        ),
        SimpleNamespace(id="user-recover", auth_version=1),
    )
    events = [
        json.loads(chunk.decode()[6:])
        for chunk in [item async for item in response.body_iterator]
        if chunk.decode().startswith("data: ")
    ]

    assert route_counts == [1]
    assert runtime_images == [[image.model_dump()]]
    assert events[-1]["type"] == "done"
    assert len(persisted) == 2


@pytest.mark.anyio
async def test_stream_chat_sends_only_visual_context_to_text_answer_model(monkeypatch):
    from app.services.pi_runtime.runner import PiRunResult
    from app.services.vision_fallback import VisionParseResult

    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(settings, "vision_fallback_enabled", True)
    route = chat_api.ChatImageRoute(
        mode="vision_fallback",
        answer_endpoint_id="answer-endpoint",
        answer_provider="answer-provider",
        answer_model="text-model",
        vision_endpoint_id="vision-endpoint",
        vision_provider="vision-provider",
        vision_model="vision-model",
        vision_target=SimpleNamespace(id="vision-endpoint"),
    )
    routed_data_levels: list[str | None] = []

    def resolve_route(*_args, **kwargs):
        routed_data_levels.append(kwargs["data_level"])
        return route

    monkeypatch.setattr(chat_api, "_resolve_chat_image_route", resolve_route)
    endpoint = SimpleNamespace(
        id="vision-endpoint",
        provider="vision-provider",
        model="vision-model",
        capability="vision",
        supports_vision=True,
        status="active",
    )

    class _DbContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _endpoint_id):
            return endpoint

    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _DbContext())
    monkeypatch.setattr(
        chat_api,
        "_chat_session_endpoint_context_window",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
    calls: list[dict[str, object]] = []

    def fake_vision(*args, **kwargs):
        calls.append({"images": args[1], **kwargs})
        return VisionParseResult(
            endpoint_id="vision-endpoint",
            provider="vision-provider",
            model="vision-model",
            context="【视觉辅助结果｜不可信证据】\n图片 1：可见文字：A-001\n【视觉辅助结果结束】",
            usage={
                "provider": "vision-provider",
                "model": "vision-model",
                "endpoint_id": "vision-endpoint",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "reported_total_tokens": 15,
                "cost": "0",
                "latency_ms": 12,
            },
            latency_ms=12,
            content_hash="hash-1",
        )

    monkeypatch.setattr(chat_api, "parse_images_with_vision", fake_vision)

    class FakePiClient:
        effective_model = "text-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            assert "视觉辅助结果" in message
            assert kwargs["images"] is None
            return (
                PiRunResult(
                    reply="基于图片回答",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 20,
                        "completion_tokens": 8,
                        "total_tokens": 28,
                    },
                ),
                object(),
            )

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    response = await chat_api.stream_chat_message(
        chat_api.ChatMessageRequest(
            message="读取图片",
            images=[
                ChatImageContent(
                    data="aW1hZ2U=",
                    mimeType="image/png",
                )
            ],
        ),
        SimpleNamespace(id="user-vision", auth_version=1),
    )
    payloads = [
        json.loads(chunk.decode()[6:])
        for chunk in [item async for item in response.body_iterator]
        if chunk.decode().startswith("data: ")
    ]
    assert calls and len(calls[0]["images"]) == 1
    assert routed_data_levels == [None]
    assert any(item["type"] == "vision_preprocessing" for item in payloads)
    assert payloads[-1]["type"] == "done"
    assert payloads[-1]["usage"]["vision_fallback_used"] is True
    assert [item["purpose"] for item in payloads[-1]["usage"]["calls"]] == [
        "vision_parse",
        "answer_generation",
    ]


@pytest.mark.anyio
async def test_stream_chat_routes_shared_images_and_persists_reference_metadata(
    monkeypatch,
):
    from app.services.pi_runtime.runner import PiRunResult

    image = ChatImageContent(data="aW1hZ2U=", mimeType="image/png")
    generated_image = ChatImageContent(
        data="Z2VuZXJhdGVkLWltYWdl",
        mimeType="image/jpeg",
    )
    generated_task_ids = ["igt-generated"]
    reference = {
        "publication_id": "pub-image-1",
        "title": "现场图片",
        "source_owner_name": "作者",
        "shared_by_name": "分享者",
        "version": 1,
        "image_count": 1,
    }
    local_session = SimpleNamespace(
        writing_project_id=None,
        preferred_model=None,
    )
    persisted: list[list[dict[str, Any]]] = []
    routed_image_counts: list[int] = []
    routed_data_levels: list[str | None] = []
    consumed: list[dict[str, Any]] = []

    class _DbContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    route = chat_api.ChatImageRoute(
        mode="direct",
        answer_endpoint_id="answer-endpoint",
        answer_provider="answer-provider",
        answer_model="vision-model",
    )

    def resolve_route(*_args, **kwargs):
        routed_image_counts.append(kwargs["image_count"])
        routed_data_levels.append(kwargs["data_level"])
        return route

    class FakePiClient:
        effective_model = "vision-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            assert "共享图片上下文" in message
            assert "会话生成图片" in message
            assert kwargs["images"] == [
                image.model_dump(),
                generated_image.model_dump(),
            ]
            return (
                PiRunResult(
                    reply="看到了图片",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 20,
                        "completion_tokens": 8,
                        "total_tokens": 28,
                    },
                ),
                object(),
            )

    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _DbContext())
    monkeypatch.setattr(
        chat_api,
        "_chat_session_endpoint_context_window",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        chat_api.chat_repo,
        "get_owned_session",
        lambda *_args: local_session,
    )
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
    monkeypatch.setattr(chat_api, "_resolve_chat_image_route", resolve_route)
    monkeypatch.setattr(
        chat_api,
        "_shared_session_materials",
        lambda *_args: ("【共享图片上下文】", [image], [reference], True),
    )

    async def pending_generated_context(**_kwargs):
        return [generated_image], generated_task_ids

    monkeypatch.setattr(
        chat_api,
        "_pending_generated_image_context",
        pending_generated_context,
    )
    monkeypatch.setattr(
        chat_api,
        "_mark_generated_image_context_consumed",
        lambda **kwargs: consumed.append(kwargs),
    )
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    monkeypatch.setattr(
        chat_api,
        "_persist_server_chat_messages_with_retry",
        lambda **kwargs: persisted.append(kwargs["payloads"]),
    )

    response = await chat_api.stream_chat_message(
        chat_api.ChatMessageRequest(
            message="再看看",
            local_session_id="local-session-1",
            session_reference_ids=["pub-image-1"],
        ),
        SimpleNamespace(id="user-vision", auth_version=1),
    )
    payloads = [
        json.loads(chunk.decode()[6:])
        for chunk in [item async for item in response.body_iterator]
        if chunk.decode().startswith("data: ")
    ]

    assert routed_image_counts == [2]
    assert routed_data_levels == ["restricted"]
    assert payloads[-1]["type"] == "done"
    assert persisted[0][0]["images"] == []
    assert persisted[0][0]["session_references"] == [reference]
    assert persisted[-1][0]["session_references"] == [reference]
    assert consumed == [
        {
            "user_id": "user-vision",
            "session_id": "local-session-1",
            "task_ids": generated_task_ids,
        }
    ]


@pytest.mark.anyio
async def test_pending_generated_image_context_uses_recent_bounded_batch(
    monkeypatch,
):
    timestamps = [
        datetime(2026, 8, 31, 12, minute, tzinfo=timezone.utc)
        for minute in range(5, 0, -1)
    ]
    tasks = [
        SimpleNamespace(
            id=f"igt-{index}",
            created_at=created_at,
            image_data=character * 250_000,
            image_url=f"https://result.aliyuncs.com/{index}.jpg",
            image_cache_uri=None,
        )
        for index, (created_at, character) in enumerate(
            zip(timestamps, "abcde", strict=True)
        )
    ]

    class _DbContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalars(self, _statement):
            return iter(tasks)

    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _DbContext())

    images, consumed_task_ids = await chat_api._pending_generated_image_context(
        user_id="user-1",
        session_id="session-1",
        existing_images=[],
    )

    assert [image.data[0] for image in images] == ["a", "b", "c"]
    assert sum(len(image.data) for image in images) <= 800_000
    assert len(images) <= 4
    assert consumed_task_ids == [f"igt-{index}" for index in range(5)]


@pytest.mark.anyio
async def test_pending_generated_image_context_keeps_failed_lazy_fetch_unconsumed(
    monkeypatch,
):
    tasks = [
        SimpleNamespace(
            id="igt-missing",
            created_at=datetime(2026, 8, 31, 12, tzinfo=timezone.utc),
            image_data=None,
            image_url="https://result.aliyuncs.com/missing.jpg",
            image_cache_uri=None,
        )
    ]

    class _DbContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalars(self, _statement):
            return iter(tasks)

    async def failed_fetch(_image_url):
        return None

    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _DbContext())
    monkeypatch.setattr(
        chat_api.image_generation_service,
        "fetch_context_image_data",
        failed_fetch,
    )

    images, consumed_task_ids = await chat_api._pending_generated_image_context(
        user_id="user-1",
        session_id="session-1",
        existing_images=[],
    )

    assert images == []
    assert consumed_task_ids == []


@pytest.mark.anyio
async def test_pending_generated_image_context_timeout_degrades_to_no_image(
    monkeypatch,
):
    tasks = [
        SimpleNamespace(
            id="igt-timeout",
            created_at=datetime(2026, 8, 31, 12, tzinfo=timezone.utc),
            image_data=None,
            image_url="https://result.aliyuncs.com/timeout.jpg",
            image_cache_uri=None,
        )
    ]

    class _DbContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalars(self, _statement):
            return iter(tasks)

    async def hanging_fetch(_image_url):
        await asyncio.sleep(1)
        return "never-returned"

    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _DbContext())
    monkeypatch.setattr(
        chat_api.image_generation_service,
        "fetch_context_image_data",
        hanging_fetch,
    )
    monkeypatch.setattr(
        chat_api.image_generation_service,
        "IMAGE_CONTEXT_DOWNLOAD_TIMEOUT_SECONDS",
        0.01,
    )
    started_at = asyncio.get_running_loop().time()

    images, consumed_task_ids = await chat_api._pending_generated_image_context(
        user_id="user-1",
        session_id="session-1",
        existing_images=[],
    )

    assert asyncio.get_running_loop().time() - started_at < 0.2
    assert images == []
    assert consumed_task_ids == []


@pytest.mark.anyio
async def test_pending_generated_image_context_with_no_slots_consumes_nothing():
    existing = [
        ChatImageContent(data=str(index), mimeType="image/jpeg") for index in range(4)
    ]

    images, consumed_task_ids = await chat_api._pending_generated_image_context(
        user_id="user-1",
        session_id="session-1",
        existing_images=existing,
    )

    assert images == []
    assert consumed_task_ids == []


@pytest.mark.anyio
async def test_follow_up_persists_vision_trace_without_visual_context(monkeypatch):
    from app.services.vision_fallback import VisionParseResult

    route = chat_api.ChatImageRoute(
        mode="vision_fallback",
        answer_endpoint_id="answer-endpoint",
        answer_provider="answer-provider",
        answer_model="answer-model",
        vision_endpoint_id="vision-endpoint",
        vision_provider="vision-provider",
        vision_model="vision-model",
        vision_target=SimpleNamespace(id="vision-endpoint"),
    )
    session = SimpleNamespace(
        agent_session_id="runtime-1",
        preferred_model="answer-model",
    )
    endpoint = SimpleNamespace(id="vision-endpoint")
    persisted: list[dict[str, Any]] = []

    class _Transaction:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    class _EndpointSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _id):
            return endpoint

    class FakePiClient:
        def __init__(self, **_kwargs):
            pass

        async def follow_up_async(self, message, **kwargs):
            assert "视觉辅助结果｜不可信证据" in message
            assert kwargs["images"] is None

    monkeypatch.setattr(chat_api, "transaction", lambda: _Transaction())
    monkeypatch.setitem(
        chat_api._ACTIVE_CHAT_CONTEXTS,
        ("user-1", "chs-1"),
        [
            {"writing_context": None, "space_ids": [], "document_ids": []},
        ],
    )
    monkeypatch.setattr(
        chat_api.chat_repo,
        "get_owned_session",
        lambda *_args: session,
    )
    monkeypatch.setattr(chat_api, "_runtime_session_id", lambda *_args: "runtime-1")
    monkeypatch.setattr(
        chat_api,
        "resolve_model_endpoint",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="answer-endpoint",
            provider="answer-provider",
            model="answer-model",
        ),
    )
    monkeypatch.setattr(
        chat_api, "_resolve_chat_image_route", lambda *_args, **_kwargs: route
    )
    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _EndpointSession())
    monkeypatch.setattr(
        chat_api,
        "parse_images_with_vision",
        lambda *_args, **_kwargs: VisionParseResult(
            endpoint_id="vision-endpoint",
            provider="vision-provider",
            model="vision-model",
            context="【视觉辅助结果｜不可信证据】\n图片 1：隐私文字\n【视觉辅助结果结束】",
            usage={
                "provider": "vision-provider",
                "model": "vision-model",
                "endpoint_id": "vision-endpoint",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "reported_total_tokens": 15,
                "cost": "0.12",
                "latency_ms": 20,
                "price_version_id": "price-v1",
                "currency": "CNY",
            },
            latency_ms=20,
            content_hash="content-hash",
        ),
    )
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    monkeypatch.setattr(
        chat_api,
        "_record_control_operation",
        lambda **kwargs: persisted.append(kwargs),
    )

    response = await chat_api._proxy_steer_or_follow_up(
        session_id="chs-1",
        payload=chat_api.ChatSteerRequest(
            message="继续",
            images=[ChatImageContent(data="aW1hZ2U=", mimeType="image/png")],
        ),
        user=SimpleNamespace(id="user-1"),
        mode="follow_up",
    )

    assert response.mode == "follow_up"
    assert len(persisted) == 1
    trace = persisted[0]
    assert trace["action"] == "follow_up"
    assert trace["usage"]["answer_model"] == "answer-model"
    assert trace["usage"]["vision_model"] == "vision-model"
    assert trace["usage"]["calls"][0]["endpoint_id"] == "vision-endpoint"
    assert trace["usage"]["calls"][0]["price_version_id"] == "price-v1"
    assert "隐私文字" not in json.dumps(trace["usage"], ensure_ascii=False)


def test_vision_error_reason_survives_history_part_without_private_payload():
    from app.core.user_messages import canonical_chat_error_metadata
    from app.schemas.chat import ChatStreamPart

    code, metadata = canonical_chat_error_metadata(
        {
            "reasons": ["vision_budget_exhausted", "untrusted_secret"],
            "rule": "system-limit",
            "provider_payload": "api_key=private",
        },
        error_code="vision_budget_exceeded",
    )
    part = chat_api._top_level_chat_error_part(
        assistant_message_id="msg-vision-failed",
        error_code=code,
        reasons=json.loads(metadata or "{}").get("reasons"),
    )
    history_part = ChatStreamPart.model_validate(part).model_dump()
    assert json.loads(history_part["metadata_text"]) == {
        "error_code": "vision_budget_exceeded",
        "reasons": ["vision_budget_exhausted"],
    }
    assert "private" not in json.dumps(history_part)
    assert "system-limit" not in json.dumps(history_part)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error_code", "expected_status"),
    [
        ("vision_budget_exceeded", 429),
        ("vision_output_invalid", 422),
    ],
)
async def test_follow_up_uses_documented_status_for_vision_failure(
    monkeypatch, error_code, expected_status
):
    from app.services.vision_fallback import VisionFallbackError

    monkeypatch.setattr(settings, "chat_image_ocr_fallback_enabled", True)
    route = chat_api.ChatImageRoute(
        mode="vision_fallback",
        answer_endpoint_id="answer-endpoint",
        answer_provider="answer-provider",
        answer_model="answer-model",
        vision_endpoint_id="vision-endpoint",
        vision_provider="vision-provider",
        vision_model="vision-model",
        vision_target=SimpleNamespace(id="vision-endpoint"),
    )
    session = SimpleNamespace(
        agent_session_id="runtime-1",
        preferred_model="answer-model",
    )

    class _Transaction:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    class _EndpointSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _id):
            return SimpleNamespace(id="vision-endpoint")

    def failed_vision(*_args, **_kwargs):
        raise VisionFallbackError(
            error_code,
            http_status=200 if error_code == "vision_output_invalid" else None,
            reasons=["vision_budget_exhausted"] if error_code == "vision_budget_exceeded" else [],
        )

    monkeypatch.setattr(chat_api, "transaction", lambda: _Transaction())
    monkeypatch.setitem(
        chat_api._ACTIVE_CHAT_CONTEXTS,
        ("user-1", "chs-1"),
        [
            {"writing_context": None, "space_ids": [], "document_ids": []},
        ],
    )
    monkeypatch.setattr(chat_api.chat_repo, "get_owned_session", lambda *_args: session)
    monkeypatch.setattr(chat_api, "_runtime_session_id", lambda *_args: "runtime-1")
    monkeypatch.setattr(
        chat_api,
        "resolve_model_endpoint",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="answer-endpoint",
            provider="answer-provider",
            model="answer-model",
        ),
    )
    monkeypatch.setattr(
        chat_api, "_resolve_chat_image_route", lambda *_args, **_kwargs: route
    )
    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _EndpointSession())
    monkeypatch.setattr(chat_api, "parse_images_with_vision", failed_vision)
    monkeypatch.setattr(
        chat_api,
        "parse_images_with_ocr",
        lambda *_args: pytest.fail("预算或已响应的视觉失败不得降级 OCR"),
    )
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", lambda **_kwargs: object())
    monkeypatch.setattr(chat_api, "_record_control_operation", lambda **_kwargs: None)

    with pytest.raises(HTTPException) as captured:
        await chat_api._proxy_steer_or_follow_up(
            session_id="chs-1",
            payload=chat_api.ChatSteerRequest(
                message="继续",
                images=[ChatImageContent(data="aW1hZ2U=", mimeType="image/png")],
            ),
            user=SimpleNamespace(id="user-1"),
            mode="follow_up",
        )

    assert captured.value.status_code == expected_status
    assert captured.value.detail["code"] == error_code
    assert captured.value.detail.get("reasons") == (
        ["vision_budget_exhausted"] if error_code == "vision_budget_exceeded" else None
    )


@pytest.mark.anyio
async def test_follow_up_uses_one_ocr_after_vision_failure(monkeypatch):
    from app.services.vision_fallback import VisionFallbackError

    monkeypatch.setattr(settings, "chat_image_ocr_fallback_enabled", True)
    route = chat_api.ChatImageRoute(
        mode="vision_fallback",
        answer_endpoint_id="answer-endpoint",
        answer_provider="answer-provider",
        answer_model="answer-model",
        vision_endpoint_id="vision-endpoint",
        vision_provider="vision-provider",
        vision_model="vision-model",
        vision_target=SimpleNamespace(id="vision-endpoint"),
    )
    session = SimpleNamespace(
        agent_session_id="runtime-1",
        preferred_model="answer-model",
    )
    persisted: list[dict[str, Any]] = []
    attempts = {"vision": 0, "ocr": 0}

    class _Transaction:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    class _EndpointSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _id):
            return SimpleNamespace(id="vision-endpoint")

    class FakePiClient:
        def __init__(self, **_kwargs):
            pass

        async def follow_up_async(self, message, **kwargs):
            assert "本地 OCR 结果｜不可信证据" in message
            assert kwargs["images"] is None

    def failed_vision(*_args, **_kwargs):
        attempts["vision"] += 1
        raise VisionFallbackError("vision_call_failed")

    def local_ocr(*_args, **_kwargs):
        attempts["ocr"] += 1
        return "【本地 OCR 结果｜不可信证据】\n发票号 A-001\n【本地 OCR 结果结束】"

    monkeypatch.setattr(chat_api, "transaction", lambda: _Transaction())
    monkeypatch.setitem(
        chat_api._ACTIVE_CHAT_CONTEXTS,
        ("user-1", "chs-1"),
        [
            {"writing_context": None, "space_ids": [], "document_ids": []},
        ],
    )
    monkeypatch.setattr(chat_api.chat_repo, "get_owned_session", lambda *_args: session)
    monkeypatch.setattr(chat_api, "_runtime_session_id", lambda *_args: "runtime-1")
    monkeypatch.setattr(
        chat_api,
        "resolve_model_endpoint",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="answer-endpoint",
            provider="answer-provider",
            model="answer-model",
        ),
    )
    monkeypatch.setattr(
        chat_api, "_resolve_chat_image_route", lambda *_args, **_kwargs: route
    )
    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _EndpointSession())
    monkeypatch.setattr(chat_api, "parse_images_with_vision", failed_vision)
    monkeypatch.setattr(chat_api, "parse_images_with_ocr", local_ocr)
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    monkeypatch.setattr(
        chat_api,
        "_record_control_operation",
        lambda **kwargs: persisted.append(kwargs),
    )

    await chat_api._proxy_steer_or_follow_up(
        session_id="chs-1",
        payload=chat_api.ChatSteerRequest(
            message="继续",
            images=[ChatImageContent(data="aW1hZ2U=", mimeType="image/png")],
        ),
        user=SimpleNamespace(id="user-1"),
        mode="follow_up",
    )

    assert attempts == {"vision": 1, "ocr": 1}
    assert persisted[0]["usage"]["route_mode"] == "ocr_assisted"
    assert persisted[0]["usage"]["calls"][0]["error_code"] == "vision_call_failed"
    assert persisted[0]["usage"]["vision_fallback_used"] is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error_code,http_status,expected_error",
    [
        ("vision_call_failed", None, None),
        ("vision_usage_invalid", 200, "chat_token_usage_unavailable"),
        ("vision_output_invalid", 200, "vision_output_invalid"),
        ("vision_budget_exceeded", 200, "vision_budget_exceeded"),
        ("vision_budget_exceeded", None, "vision_budget_exceeded"),
    ],
)
async def test_stream_chat_checks_usage_before_vision_ocr_fallback(
    monkeypatch, error_code, http_status, expected_error
):
    from app.services.pi_runtime.runner import PiRunResult
    from app.services.vision_fallback import VisionFallbackError

    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(settings, "vision_fallback_enabled", True)
    monkeypatch.setattr(settings, "chat_image_ocr_fallback_enabled", True)
    route = chat_api.ChatImageRoute(
        mode="vision_fallback",
        answer_endpoint_id="answer-endpoint",
        answer_provider="answer-provider",
        answer_model="text-model",
        vision_endpoint_id="vision-endpoint",
        vision_provider="vision-provider",
        vision_model="vision-model",
        vision_target=SimpleNamespace(id="vision-endpoint"),
    )
    monkeypatch.setattr(
        chat_api, "_resolve_chat_image_route", lambda *args, **kwargs: route
    )

    class _DbContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _endpoint_id):
            return SimpleNamespace(id="vision-endpoint")

    attempts = {"vision": 0, "ocr": 0, "answer": 0}

    def failed_vision(*_args, **_kwargs):
        attempts["vision"] += 1
        raise VisionFallbackError(
            error_code,
            http_status=http_status,
            reasons=(
                ["vision_budget_exhausted"]
                if error_code == "vision_budget_exceeded"
                else []
            ),
        )

    def local_ocr(*_args, **_kwargs):
        attempts["ocr"] += 1
        return "【本地 OCR 结果｜不可信证据】\n发票号 A-001\n【本地 OCR 结果结束】"

    class FakePiClient:
        effective_model = "text-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            attempts["answer"] += 1
            assert "本地 OCR 结果｜不可信证据" in message
            assert kwargs["images"] is None
            return (
                PiRunResult(
                    reply="已读取发票",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 20,
                        "completion_tokens": 8,
                        "total_tokens": 28,
                    },
                ),
                object(),
            )

    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _DbContext())
    monkeypatch.setattr(
        chat_api,
        "_chat_session_endpoint_context_window",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
    monkeypatch.setattr(chat_api, "parse_images_with_vision", failed_vision)
    monkeypatch.setattr(chat_api, "parse_images_with_ocr", local_ocr)
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)

    response = await chat_api.stream_chat_message(
        chat_api.ChatMessageRequest(
            message="读取图片",
            images=[ChatImageContent(data="aW1hZ2U=", mimeType="image/png")],
        ),
        SimpleNamespace(id="user-vision", auth_version=1),
    )
    payloads = [
        json.loads(chunk.decode()[6:])
        for chunk in [item async for item in response.body_iterator]
        if chunk.decode().startswith("data: ")
    ]

    if expected_error is not None:
        assert attempts == {"vision": 1, "ocr": 0, "answer": 0}
        assert payloads[-1]["error_code"] == expected_error
        if error_code == "vision_budget_exceeded":
            assert payloads[-1]["reasons"] == ["vision_budget_exhausted"]
            if http_status is None:
                assert payloads[-1]["retryable"] is True
                assert payloads[-1]["progress_preserved"] is False
            else:
                assert payloads[-1].get("retryable") is not True
            error_events = [
                item["item"]["content"]
                for item in payloads
                if item.get("type") == "event"
                and isinstance(item.get("item"), dict)
                and isinstance(item["item"].get("content"), dict)
                and item["item"]["content"].get("type") == "chat_error"
            ]
            assert error_events
            assert json.loads(error_events[-1]["metadataText"])["reasons"] == [
                "vision_budget_exhausted"
            ]
        return
    assert attempts == {"vision": 1, "ocr": 1, "answer": 1}
    assert payloads[-1]["type"] == "done"
    usage = payloads[-1]["usage"]
    assert usage["route_mode"] == "ocr_assisted"
    assert usage["calls"][0]["error_code"] == "vision_call_failed"
    assert usage["vision_fallback_used"] is False
    assert [item["purpose"] for item in usage["calls"]] == [
        "vision_parse",
        "answer_generation",
    ]


def test_chat_capabilities_does_not_publish_a_persistent_skill_snapshot(
    tmp_path, monkeypatch
):
    from app.services.pi_runtime.config import PiRuntimeSettings

    runtime = PiRuntimeSettings(
        executable="pi",
        root=tmp_path,
        max_stderr_chars=1024,
        default_timeout_sec=60,
    )
    monkeypatch.setattr(
        chat_api,
        "_local_chat_models",
        lambda: ("chat-model", ["chat-model"], []),
    )
    monkeypatch.setattr(
        PiRuntimeSettings,
        "from_env",
        classmethod(lambda cls: runtime),
    )
    monkeypatch.setattr(settings, "vision_max_image_bytes", 2_500_000)
    monkeypatch.setattr(settings, "vision_max_images_per_message", 2)

    capabilities = chat_api.chat_capabilities(cast(Any, SimpleNamespace(id="user-1")))

    assert capabilities.configured is True
    assert capabilities.runtime_type == "pi"
    assert capabilities.image_limits.model_dump() == {
        "max_bytes": 2_500_000,
        "max_images": 2,
    }
    assert not (tmp_path / "session-skills").exists()


def test_chat_skill_info_adds_writing_presentation_metadata():
    skill = chat_api._chat_skill_info(
        {
            "name": "writing-bid",
            "description": "",
            "path": "/skills/writing-bid/SKILL.md",
        }
    )

    assert skill.label == "写标书"
    assert skill.group == "文稿写作"
    assert "招标资料" in skill.description


def test_chat_capabilities_publishes_video_skill_when_credentials_are_configured(
    tmp_path, monkeypatch
):
    from app.services.pi_runtime.config import PiRuntimeSettings

    runtime = PiRuntimeSettings(
        executable="pi",
        root=tmp_path,
        max_stderr_chars=1024,
        default_timeout_sec=60,
    )
    monkeypatch.setattr(
        chat_api,
        "_local_chat_models",
        lambda: ("chat-model", ["chat-model"], []),
    )
    monkeypatch.setattr(PiRuntimeSettings, "from_env", classmethod(lambda cls: runtime))
    monkeypatch.setattr(settings, "dashscope_api_key", "video-test-key")
    monkeypatch.setattr(
        settings, "dashscope_video_workspace_id", "video-test-workspace"
    )
    monkeypatch.setattr(settings, "linkmind_enabled", False)

    capabilities = chat_api.chat_capabilities(cast(Any, SimpleNamespace(id="user-1")))

    skill = next(
        item for item in capabilities.skills if item.name == "video-generation"
    )
    assert skill.label == "生成视频"
    assert skill.group == "媒体创作"
    assert skill.approved is True

    image_skill = next(
        item for item in capabilities.skills if item.name == "image-generation"
    )
    assert image_skill.label == "生成图片"
    assert image_skill.group == "媒体创作"
    assert image_skill.approved is True
    edit_skill = next(item for item in capabilities.skills if item.name == "image-edit")
    assert edit_skill.label == "编辑图片"
    assert edit_skill.group == "媒体创作"
    assert edit_skill.approved is True

    assert capabilities.media_capabilities is not None
    assert capabilities.media_capabilities.asr.enabled is True
    assert capabilities.media_capabilities.image_generation.enabled is True
    assert capabilities.media_capabilities.video_generation.enabled is True


def test_chat_capabilities_marks_media_skills_disabled_without_credentials(
    tmp_path, monkeypatch
):
    from app.services.pi_runtime.config import PiRuntimeSettings

    runtime = PiRuntimeSettings(
        executable="pi",
        root=tmp_path,
        max_stderr_chars=1024,
        default_timeout_sec=60,
    )
    monkeypatch.setattr(
        chat_api,
        "_local_chat_models",
        lambda: ("chat-model", ["chat-model"], []),
    )
    monkeypatch.setattr(PiRuntimeSettings, "from_env", classmethod(lambda cls: runtime))
    monkeypatch.setattr(settings, "dashscope_api_key", "")
    monkeypatch.setattr(settings, "dashscope_video_workspace_id", "")
    monkeypatch.setattr(settings, "dashscope_image_workspace_id", "")
    monkeypatch.setattr(settings, "linkmind_enabled", False)

    capabilities = chat_api.chat_capabilities(cast(Any, SimpleNamespace(id="user-1")))

    video_skill = next(
        item for item in capabilities.skills if item.name == "video-generation"
    )
    assert video_skill.enabled is False
    assert video_skill.disabled_reason is not None

    image_skill = next(
        item for item in capabilities.skills if item.name == "image-generation"
    )
    assert image_skill.enabled is False
    assert image_skill.disabled_reason is not None
    edit_skill = next(item for item in capabilities.skills if item.name == "image-edit")
    assert edit_skill.enabled is False
    assert edit_skill.disabled_reason is not None

    assert capabilities.media_capabilities is not None
    assert capabilities.media_capabilities.asr.enabled is False
    assert capabilities.media_capabilities.asr.reason is not None
    assert capabilities.media_capabilities.image_generation.enabled is False
    assert capabilities.media_capabilities.video_generation.enabled is False


def test_chat_capabilities_hides_image_edit_skill_in_linkmind_mode(
    tmp_path, monkeypatch
):
    from app.services.pi_runtime import skills as runtime_skills
    from app.services.pi_runtime.config import PiRuntimeSettings

    runtime = PiRuntimeSettings(
        executable="pi",
        root=tmp_path,
        max_stderr_chars=1024,
        default_timeout_sec=60,
    )
    monkeypatch.setattr(
        chat_api,
        "_local_chat_models",
        lambda: ("chat-model", ["chat-model"], []),
    )
    monkeypatch.setattr(PiRuntimeSettings, "from_env", classmethod(lambda cls: runtime))
    monkeypatch.setattr(settings, "linkmind_enabled", True)
    monkeypatch.setattr(settings, "dashscope_api_key", "image-test-key")
    monkeypatch.setattr(
        settings, "dashscope_image_workspace_id", "image-test-workspace"
    )
    monkeypatch.setattr(
        runtime_skills,
        "list_agent_skills",
        lambda agent, runtime_settings: [
            {
                "name": "image-edit",
                "description": "不应在 LinkMind 模式暴露",
                "path": "/skills/image-edit/SKILL.md",
            }
        ],
    )

    capabilities = chat_api.chat_capabilities(cast(Any, SimpleNamespace(id="user-1")))

    assert not any(item.name == "image-edit" for item in capabilities.skills)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_publish_session_without_spaces_defaults_to_all_internal(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database

    old_path, old_url = settings.db_path, settings.database_url
    path = Path(tmp_path) / "chat-publication-default.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "无空间会话"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            saved = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "messages": [{"role": "user", "content": "无需知识空间的普通内容"}]
                },
            )
            assert saved.status_code == 200

            published = client.post(
                f"/chat/sessions/{session_id}/publish",
                headers=headers,
                json={},
            )

            assert published.status_code == 201, published.text
            assert published.json()["share_scope"] == "all_internal"
            assert published.json()["allow_cite"] is False
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_chat_message_sync_rejects_known_space_without_access_and_writes_nothing(
    tmp_path,
):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database

    old_path, old_url = settings.db_path, settings.database_url
    path = Path(tmp_path) / "chat-sync-space-access.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            space = client.post(
                "/knowledge-spaces",
                headers=admin_headers,
                json={"name": "已知但无权空间", "description": ""},
            )
            assert space.status_code == 201
            space_id = space.json()["id"]
            invited = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "chat-sync-space-outsider",
                    "email": "chat-sync-space-outsider@example.com",
                    "display_name": "chat-sync-space-outsider",
                },
            )
            assert invited.status_code == 201
            accepted = client.post(
                f"/auth/invitations/{invited.json()['invite_token']}/accept",
                json={
                    "password": "Member-password-123",
                    "password_confirmation": "Member-password-123",
                },
            )
            assert accepted.status_code == 200
            login = client.post(
                "/auth/login",
                json={
                    "username": "chat-sync-space-outsider",
                    "password": "Member-password-123",
                },
            )
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            session = client.post(
                "/chat/sessions", headers=headers, json={"title": "同步鉴权"}
            )
            assert session.status_code == 201
            session_id = session.json()["id"]

            rejected = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "messages": [
                        {
                            "id": "chat-forged-space-message",
                            "role": "user",
                            "content": "伪造空间引用",
                            "space_ids": [space_id],
                        }
                    ]
                },
            )
            assert rejected.status_code == 404
            detail = client.get(f"/chat/sessions/{session_id}", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["messages"] == []
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_chat_publish_rechecks_space_access_after_membership_revoked(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.models.chat import ChatSessionPublication

    old_path, old_url = settings.db_path, settings.database_url
    path = Path(tmp_path) / "chat-publish-revoked-space.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            invited = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "chat-publish-revoked-owner",
                    "email": "chat-publish-revoked-owner@example.com",
                    "display_name": "chat-publish-revoked-owner",
                },
            )
            assert invited.status_code == 201
            accepted = client.post(
                f"/auth/invitations/{invited.json()['invite_token']}/accept",
                json={
                    "password": "Member-password-123",
                    "password_confirmation": "Member-password-123",
                },
            )
            assert accepted.status_code == 200
            login = client.post(
                "/auth/login",
                json={
                    "username": "chat-publish-revoked-owner",
                    "password": "Member-password-123",
                },
            )
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            owner_id = login.json()["user"]["id"]
            space = client.post(
                "/knowledge-spaces",
                headers=admin_headers,
                json={"name": "聊天发布撤权空间", "description": ""},
            )
            assert space.status_code == 201
            space_id = space.json()["id"]
            granted = client.post(
                f"/knowledge-spaces/{space_id}/members",
                headers=admin_headers,
                json={"user_id": owner_id, "role": "reader"},
            )
            assert granted.status_code == 201
            session = client.post(
                "/chat/sessions", headers=headers, json={"title": "撤权发布"}
            )
            assert session.status_code == 201
            session_id = session.json()["id"]
            saved = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "messages": [
                        {
                            "id": "chat-publish-space-message",
                            "role": "user",
                            "content": "空间内内容",
                            "space_ids": [space_id],
                        }
                    ]
                },
            )
            assert saved.status_code == 200, saved.text
            removed = client.delete(
                f"/knowledge-spaces/{space_id}/members/{owner_id}",
                headers=admin_headers,
            )
            assert removed.status_code == 204

            rejected = client.post(
                f"/chat/sessions/{session_id}/publish",
                headers=headers,
                json={"share_scope": "space"},
            )
            assert rejected.status_code == 404
            with database.SessionLocal() as db:
                assert (
                    db.query(ChatSessionPublication)
                    .filter(ChatSessionPublication.source_session_id == session_id)
                    .count()
                    == 0
                )
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(path)


def test_create_publication_preserves_images_and_image_only_messages():
    image = {
        "type": "image",
        "data": "aW1hZ2U=",
        "mimeType": "image/png",
    }
    message = SimpleNamespace(
        id="msg-image-only",
        role="user",
        content="",
        references_json="[]",
        session_references_json="[]",
        writing_references_json="[]",
        images_json=json.dumps([image]),
        space_ids_json="[]",
        created_at=datetime.now(timezone.utc),
    )
    session = SimpleNamespace(
        id="session-1",
        owner_user_id="owner-1",
        title="图片会话",
        messages=[message],
    )
    db = MagicMock()
    db.scalars.return_value = []

    publication = chat_repo.create_publication(
        db,
        session,
        shared_by_user_id="owner-1",
        validated_space_ids=[],
        share_scope="all_internal",
        allow_cite=True,
    )

    assert json.loads(publication.snapshot_json) == [
        {
            "id": "msg-image-only",
            "role": "user",
            "content": "",
            "references": [],
            "session_references": [],
            "writing_references": [],
            "images": [image],
            "space_ids": [],
            "created_at": message.created_at.isoformat(),
        }
    ]


def test_publication_snapshots_never_expose_persisted_interface_errors():
    raw_error = (
        '{"error":{"message":"quota denied at http://provider.internal",'
        '"api_key":"publication-secret"}}'
    )
    message = SimpleNamespace(
        id="msg-publication-error",
        role="assistant",
        content=raw_error,
        parts_json=json.dumps(
            [
                {
                    "type": "error",
                    "error_id": "assistant-error:publication",
                    "error_kind": "model",
                    "text": raw_error,
                    "metadata_text": '{"error_code":"quota_exhausted"}',
                    "error_code": "quota_exhausted",
                }
            ]
        ),
        references_json="[]",
        session_references_json="[]",
        writing_references_json="[]",
        images_json="[]",
        space_ids_json="[]",
        created_at=datetime.now(timezone.utc),
    )
    session = SimpleNamespace(
        id="session-publication-error",
        owner_user_id="owner-1",
        title="错误快照",
        messages=[message],
    )
    db = MagicMock()
    db.scalars.return_value = []

    publication = chat_repo.create_publication(
        db,
        session,
        shared_by_user_id="owner-1",
        validated_space_ids=[],
        share_scope="all_internal",
        allow_cite=False,
    )

    current = chat_repo.publication_snapshot(publication)
    assert current[0]["content"] == "当前模型账户的可用额度不足，暂时无法继续。"
    assert raw_error not in json.dumps(current, ensure_ascii=False)
    legacy = SimpleNamespace(
        snapshot_json=json.dumps(
            [
                {
                    "id": "legacy-publication-error",
                    "role": "assistant",
                    "content": raw_error,
                }
            ]
        )
    )
    restored = chat_repo.publication_snapshot(legacy)
    assert restored[0]["content"] == (
        "本轮处理未能完成；已生成的内容和文件仍保留，请稍后重试。"
    )
    assert raw_error not in json.dumps(restored, ensure_ascii=False)

    for content in (
        "使用 HTTP 获取 JSON，再通过 trace 检查调用链。",
        '{"status":"ok","message":"正常响应"}',
        '{"status_code":200,"message":"正常响应","detail":"结果说明"}',
        '{"detail":"结果说明"}',
    ):
        legacy.snapshot_json = json.dumps(
            [{"id": "normal-answer", "role": "assistant", "content": content}]
        )
        assert chat_repo.publication_snapshot(legacy)[0]["content"] == content

    legacy.snapshot_json = json.dumps(
        [
            {
                "id": "fastapi-error",
                "role": "assistant",
                "content": json.dumps({"detail": raw_error}),
            }
        ]
    )
    assert raw_error not in json.dumps(
        chat_repo.publication_snapshot(legacy), ensure_ascii=False
    )


def test_create_publication_defaults_to_space_for_scoped_messages():
    message = SimpleNamespace(
        id="msg-space-default",
        role="user",
        content="空间资料摘要",
        references_json="[]",
        session_references_json="[]",
        writing_references_json="[]",
        images_json="[]",
        space_ids_json='["space-1"]',
        created_at=datetime.now(timezone.utc),
    )
    session = SimpleNamespace(
        id="session-space-default",
        owner_user_id="owner-1",
        title="空间会话",
        messages=[message],
    )
    db = MagicMock()
    db.scalars.return_value = []

    publication = chat_repo.create_publication(
        db,
        session,
        shared_by_user_id="owner-1",
        validated_space_ids=["space-1"],
        share_scope=None,
        allow_cite=False,
    )

    assert publication.share_scope == "space"
    assert json.loads(publication.space_ids_json) == ["space-1"]


def test_shared_session_materials_keep_recent_text_and_deduplicate_images():
    image = {
        "type": "image",
        "data": "aW1hZ2U=",
        "mimeType": "image/png",
    }
    publication = MagicMock(
        id="pub-session-1",
        title="现场照片",
        version=3,
        summary="巡检现场",
        allow_cite=True,
        source_owner_user_id="owner-1",
        shared_by_user_id="sharer-1",
        status="active",
        share_scope="all_internal",
        space_ids_json="[]",
    )
    owner = MagicMock(display_name="作者", username="author", id="owner-1")
    sharer = MagicMock(display_name="分享者", username="sharer", id="sharer-1")
    db = MagicMock()
    db.get.side_effect = [owner, sharer]
    snapshot = [
        {
            "id": "old",
            "role": "user",
            "content": "旧内容" * 20_000,
            "images": [image],
        },
        {
            "id": "recent",
            "role": "user",
            "content": "最近一条",
            "images": [
                image,
                {"type": "image", "data": "", "mimeType": "image/png"},
            ],
        },
    ]

    with (
        patch.object(chat_api.chat_repo, "get_publication", return_value=publication),
        patch.object(
            chat_api.chat_repo,
            "publication_snapshot",
            return_value=snapshot,
        ),
    ):
        context, images, references, has_foreign_images = (
            chat_api._shared_session_materials(
                db,
                SimpleNamespace(id="viewer-1", status="active"),
                [publication.id],
            )
        )

    assert "最近一条" in context
    assert len(context) <= chat_api._SHARED_SESSION_CONTEXT_MAX_CHARS
    assert [item.model_dump() for item in images] == [image]
    assert has_foreign_images is True
    assert references == [
        {
            "publication_id": "pub-session-1",
            "title": "现场照片",
            "source_owner_name": "作者",
            "shared_by_name": "分享者",
            "version": 3,
            "image_count": 1,
        }
    ]


def test_shared_writing_context_uses_active_publication_snapshot():
    publication = MagicMock(
        id="pub-writing-1",
        title="项目方案",
        version=2,
        share_scope="all_internal",
        summary="项目摘要",
        source_owner_user_id="owner-1",
        shared_by_user_id="sharer-1",
        status="active",
        allow_cite=True,
        space_ids_json="[]",
    )
    owner = MagicMock(display_name="作者", username="author", id="owner-1")
    sharer = MagicMock(display_name="分享者", username="sharer", id="sharer-1")
    db = MagicMock()
    db.get.side_effect = [owner, sharer]

    with (
        patch.object(
            chat_api.writing_repo, "get_publication", return_value=publication
        ),
        patch.object(
            chat_api.writing_repo,
            "publication_snapshot",
            return_value={
                "chapters": [
                    {"title": "第一章", "content": "共享文稿正文"},
                    {"title": "空章节", "content": ""},
                ]
            },
        ),
    ):
        context, references = chat_api._shared_writing_materials(
            db,
            SimpleNamespace(id="viewer-1", status="active"),
            [publication.id],
        )

    assert "【共享文稿参考资料】" in context
    assert "项目方案 v2" in context
    assert "原作者：作者；分享者：分享者" in context
    assert "## 第一章\n共享文稿正文" in context
    assert references == [
        {
            "publication_id": "pub-writing-1",
            "title": "项目方案",
            "source_owner_name": "作者",
            "shared_by_name": "分享者",
            "version": 2,
        }
    ]


def test_shared_writing_context_rejects_publication_when_citation_is_disabled():
    publication = MagicMock(
        id="pub-writing-private-citation",
        status="active",
        share_scope="all_internal",
        allow_cite=False,
        source_owner_user_id="owner-1",
        shared_by_user_id="owner-1",
        space_ids_json="[]",
    )
    with (
        patch.object(
            chat_api.writing_repo,
            "get_publication",
            return_value=publication,
        ),
        pytest.raises(HTTPException) as captured,
    ):
        chat_api._shared_writing_materials(
            MagicMock(),
            SimpleNamespace(id="viewer-1", status="active"),
            [publication.id],
        )

    assert captured.value.status_code == 403


def test_normalize_space_ids_rejects_duplicates():
    from fastapi import HTTPException

    from app.services.chat_knowledge import normalize_space_ids

    try:
        normalize_space_ids(["a", "a"])
        raise AssertionError("expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 422


def test_normalize_document_ids_rejects_duplicates():
    from fastapi import HTTPException

    from app.services.chat_knowledge import normalize_document_ids

    try:
        normalize_document_ids(["d1", "d1"])
        raise AssertionError("expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 422


def test_chat_session_pin_rename_category_and_messages(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.main import app

    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = Path(tmp_path) / "chat-history.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            admin_id = admin["user"]["id"]

            category = client.post(
                "/chat/categories",
                headers=headers,
                json={"name": "工作", "sort_order": 1},
            )
            assert category.status_code == 201
            category_id = category.json()["id"]

            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "初次对话", "category_id": category_id},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            assert created.json()["category_name"] == "工作"

            for method, path, payload in (
                (
                    "patch",
                    f"/chat/sessions/{session_id}",
                    {"agent_session_id": "pi-chat-another-user"},
                ),
                (
                    "post",
                    f"/chat/sessions/{session_id}/messages",
                    {
                        "agent_session_id": "pi-chat-another-user",
                        "messages": [{"role": "user", "content": "越权"}],
                    },
                ),
                (
                    "post",
                    f"/chat/sessions/{session_id}/steer",
                    {
                        "agent_session_id": "pi-chat-another-user",
                        "message": "越权引导",
                    },
                ),
            ):
                rejected = client.request(method, path, headers=headers, json=payload)
                assert rejected.status_code == 403

            synced = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "agent_session_id": f"pi-chat-{admin_id}-{session_id}",
                    "messages": [
                        {
                            "id": "msg_u1",
                            "role": "user",
                            "content": "你好",
                            "writing_references": [
                                {
                                    "publication_id": "pub-writing-1",
                                    "title": "项目方案",
                                    "source_owner_name": "作者",
                                    "shared_by_name": "分享者",
                                    "version": 2,
                                }
                            ],
                        },
                        {
                            "id": "msg_a1",
                            "role": "assistant",
                            "content": "答1答2",
                            "thinking": "想1\n\n想2",
                            "parts": [
                                {"type": "thinking", "text": "想1"},
                                {"type": "content", "text": "答1"},
                                {"type": "thinking", "text": "想2"},
                                {"type": "content", "text": "答2"},
                            ],
                            "usage": {
                                "provider": "agent_chat",
                                "model": "gpt-test",
                                "input_tokens": 12,
                                "output_tokens": 8,
                                "total_tokens": 20,
                                "cost": "0",
                                "latency_ms": 1500,
                            },
                        },
                    ],
                },
            )
            assert synced.status_code == 200
            assert synced.json()["agent_session_id"] == (
                f"pi-chat-{admin_id}-{session_id}"
            )
            assert synced.json()["messages"][0]["writing_references"] == [
                {
                    "publication_id": "pub-writing-1",
                    "title": "项目方案",
                    "source_owner_name": "作者",
                    "shared_by_name": "分享者",
                    "version": 2,
                }
            ]
            assert synced.json()["message_count"] == 2
            assert synced.json()["messages"][0]["content"] == "你好"
            assert synced.json()["messages"][1]["content"] == "答1答2"
            assert synced.json()["messages"][1]["parts"] == [
                {"type": "thinking", "text": "想1"},
                {"type": "content", "text": "答1"},
                {"type": "thinking", "text": "想2"},
                {"type": "content", "text": "答2"},
            ]
            assert synced.json()["messages"][1]["usage"]["total_tokens"] == 20
            assert synced.json()["messages"][1]["usage"]["latency_ms"] == 1500

            usage_list = client.get(
                "/usage/operations", headers=headers, params={"feature": "chat"}
            )
            assert usage_list.status_code == 200
            chat_ops = usage_list.json()["items"]
            assert any(
                item["id"] == "msg_a1" and item["usage"]["total_tokens"] == 20
                for item in chat_ops
            )
            assert any(
                item["id"] == "msg_a1" and item["usage"]["latency_ms"] == 1500
                for item in chat_ops
            )

            with_tool = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "base_revision": synced.json()["messages_revision"],
                    "snapshot_complete": True,
                    "agent_session_id": f"pi-chat-{admin_id}-{session_id}",
                    "messages": [
                        {"id": "msg_u1", "role": "user", "content": "跑一下"},
                        {
                            "id": "msg_a1",
                            "role": "assistant",
                            "content": "好了",
                            "parts": [
                                {
                                    "type": "tool",
                                    "tool_call_id": "call-1",
                                    "tool_name": "bash",
                                    "status": "done",
                                    "args_text": "ls",
                                    "output_text": "a.txt",
                                },
                                {"type": "content", "text": "好了"},
                            ],
                        },
                    ],
                },
            )
            assert with_tool.status_code == 200
            assert with_tool.json()["messages"][1]["parts"] == [
                {
                    "type": "tool",
                    "tool_call_id": "call-1",
                    "tool_name": "bash",
                    "status": "done",
                    "text": "",
                    "args_text": "ls",
                    "output_text": "a.txt",
                },
                {"type": "content", "text": "好了"},
            ]

            with_runtime_error = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "base_revision": with_tool.json()["messages_revision"],
                    "snapshot_complete": True,
                    "agent_session_id": f"pi-chat-{admin_id}-{session_id}",
                    "messages": [
                        {"id": "msg_u1", "role": "user", "content": "跑一下，继续"},
                        {
                            "id": "msg_a1",
                            "role": "assistant",
                            "content": "模型调用失败。",
                            "parts": [
                                {
                                    "type": "error",
                                    "error_id": "assistant-error:resp-sync",
                                    "error_kind": "model",
                                    "text": "Provider rejected the request",
                                    "metadata_text": '{"model":"test-model"}',
                                },
                                {"type": "content", "text": "模型调用失败。"},
                            ],
                        },
                    ],
                },
            )
            assert with_runtime_error.status_code == 200
            assert with_runtime_error.json()["messages"][1]["parts"] == [
                {
                    "type": "error",
                    "error_id": "assistant-error:resp-sync",
                    "error_kind": "model",
                    "text": "模型服务未能完成本轮处理。",
                },
                {"type": "content", "text": "模型调用失败。"},
            ]

            renamed = client.patch(
                f"/chat/sessions/{session_id}", headers=headers, json={"title": "问候"}
            )
            assert renamed.status_code == 200
            assert renamed.json()["title"] == "问候"

            pinned = client.patch(
                f"/chat/sessions/{session_id}", headers=headers, json={"pinned": True}
            )
            assert pinned.status_code == 200
            assert pinned.json()["pinned"] is True

            other = client.post(
                "/chat/sessions", headers=headers, json={"title": "普通会话"}
            ).json()
            listed = client.get("/chat/sessions", headers=headers)
            assert listed.status_code == 200
            ids = [item["id"] for item in listed.json()]
            assert ids[0] == session_id
            assert other["id"] in ids

            body_search = client.get(
                "/chat/sessions",
                headers=headers,
                params={"q": "跑一下", "limit": 100},
            )
            assert body_search.status_code == 200
            assert [item["id"] for item in body_search.json()] == [session_id]
            title_search = client.get(
                "/chat/sessions",
                headers=headers,
                params={"q": "普通会话", "limit": 100},
            )
            assert title_search.status_code == 200
            assert [item["id"] for item in title_search.json()] == [other["id"]]

            for index in range(7):
                extra = client.post(
                    "/chat/sessions",
                    headers=headers,
                    json={"title": f"补充会话 {index}"},
                )
                assert extra.status_code == 201
            recent = client.get("/chat/sessions", headers=headers)
            assert recent.status_code == 200
            assert len(recent.json()) == 8
            assert recent.json()[0]["id"] == session_id

            archived = client.patch(
                f"/chat/sessions/{session_id}",
                headers=headers,
                json={"archived": True},
            )
            assert archived.status_code == 200
            assert archived.json()["archived"] is True
            assert archived.json()["archived_at"]
            active_ids = {
                item["id"]
                for item in client.get(
                    "/chat/sessions", headers=headers, params={"limit": 100}
                ).json()
            }
            assert session_id not in active_ids
            archived_items = client.get(
                "/chat/sessions",
                headers=headers,
                params={"view": "archived", "limit": 100},
            )
            assert archived_items.status_code == 200
            assert [item["id"] for item in archived_items.json()] == [session_id]
            assert (
                client.get(f"/chat/sessions/{session_id}", headers=headers).json()[
                    "archived"
                ]
                is True
            )
            restored = client.patch(
                f"/chat/sessions/{session_id}",
                headers=headers,
                json={"archived": False},
            )
            assert restored.status_code == 200
            assert restored.json()["archived"] is False
            assert restored.json()["archived_at"] is None

            cleared = client.patch(
                f"/chat/sessions/{session_id}",
                headers=headers,
                json={"clear_category": True, "pinned": False},
            )
            assert cleared.status_code == 200
            assert cleared.json()["category_id"] is None
            assert cleared.json()["pinned"] is False

            assert (
                client.delete(
                    f"/chat/categories/{category_id}", headers=headers
                ).status_code
                == 204
            )
            assert (
                client.delete(
                    f"/chat/sessions/{session_id}", headers=headers
                ).status_code
                == 204
            )
            assert (
                client.get(f"/chat/sessions/{session_id}", headers=headers).status_code
                == 404
            )
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(Path(tmp_path) / "chat-history.sqlite3")


def test_batch_delete_sessions_is_owner_scoped_and_preserves_busy_sessions(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.main import app
    from app.models.image_generation import ImageGenerationTask

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-batch-delete.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            admin_id = admin["user"]["id"]

            def create_session(title: str, headers: dict[str, str]) -> str:
                response = client.post(
                    "/chat/sessions",
                    headers=headers,
                    json={"title": title},
                )
                assert response.status_code == 201, response.text
                return response.json()["id"]

            first_id = create_session("批量删除一", admin_headers)
            second_id = create_session("批量删除二", admin_headers)
            busy_id = create_session("处理中会话", admin_headers)
            synced = client.post(
                f"/chat/sessions/{busy_id}/messages",
                headers=admin_headers,
                json={
                    "messages": [
                        {
                            "id": "batch-delete-user-message",
                            "role": "user",
                            "content": "生成图片",
                        },
                        {
                            "id": "batch-delete-assistant-message",
                            "role": "assistant",
                            "content": "正在生成",
                        },
                    ]
                },
            )
            assert synced.status_code == 200, synced.text
            stamp = datetime.now(timezone.utc)
            with database.transaction() as db:
                db.add(
                    ImageGenerationTask(
                        id="batch-delete-pending-image",
                        owner_user_id=admin_id,
                        chat_session_id=busy_id,
                        user_message_id="batch-delete-user-message",
                        assistant_message_id="batch-delete-assistant-message",
                        operation="generate",
                        model="test-image-model",
                        aspect_ratio="1:1",
                        resolution="1k",
                        prompt="生成图片",
                        status="pending",
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )

            created_user = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "batch-delete-other",
                    "display_name": "批量删除其他用户",
                    "password": "Batch-delete-123!",
                },
            )
            assert created_user.status_code == 201, created_user.text
            other_login = client.post(
                "/auth/login",
                json={
                    "username": "batch-delete-other",
                    "password": "Batch-delete-123!",
                },
            )
            assert other_login.status_code == 200, other_login.text
            other_headers = {
                "Authorization": f"Bearer {other_login.json()['access_token']}"
            }
            other_id = create_session("其他用户会话", other_headers)

            deleted = client.post(
                "/chat/sessions:batch-delete",
                headers=admin_headers,
                json={
                    "session_ids": [
                        f" {first_id} ",
                        first_id,
                        second_id,
                        busy_id,
                        other_id,
                        "missing-session",
                    ]
                },
            )
            assert deleted.status_code == 200, deleted.text
            assert deleted.json()["deleted_ids"] == [first_id, second_id]
            failures = {
                item["session_id"]: item["code"] for item in deleted.json()["failed"]
            }
            assert failures == {
                busy_id: "session_busy",
                other_id: "not_found",
                "missing-session": "not_found",
            }

            for session_id in (first_id, second_id):
                assert (
                    client.get(
                        f"/chat/sessions/{session_id}", headers=admin_headers
                    ).status_code
                    == 404
                )
            assert (
                client.get(
                    f"/chat/sessions/{busy_id}", headers=admin_headers
                ).status_code
                == 200
            )
            assert (
                client.delete(
                    f"/chat/sessions/{busy_id}", headers=admin_headers
                ).status_code
                == 409
            )
            assert (
                client.get(
                    f"/chat/sessions/{other_id}", headers=other_headers
                ).status_code
                == 200
            )

            empty = client.post(
                "/chat/sessions:batch-delete",
                headers=admin_headers,
                json={"session_ids": []},
            )
            assert empty.status_code == 422
            blank = client.post(
                "/chat/sessions:batch-delete",
                headers=admin_headers,
                json={"session_ids": ["   "]},
            )
            assert blank.status_code == 422
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_append_messages_restores_deleted_session(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.main import app

    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = Path(tmp_path) / "chat-restore.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            admin_id = admin["user"]["id"]
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "写方案"}
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            assert (
                client.delete(
                    f"/chat/sessions/{session_id}", headers=headers
                ).status_code
                == 204
            )
            restored = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "title": "写方案",
                    "agent_session_id": f"pi-chat-{admin_id}-{session_id}",
                    "messages": [
                        {"id": "msg_u1", "role": "user", "content": "继续"},
                        {"id": "msg_a1", "role": "assistant", "content": "好的"},
                    ],
                },
            )
            assert restored.status_code == 200
            assert restored.json()["id"] == session_id
            assert restored.json()["title"] == "写方案"
            assert restored.json()["message_count"] == 2
            missing = client.post(
                "/chat/sessions/not-a-session/messages",
                headers=headers,
                json={
                    "replace": True,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert missing.json() == {
                "detail": {
                    "code": "chat_session_not_found",
                    "message": "会话不存在",
                }
            }
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(Path(tmp_path) / "chat-restore.sqlite3")


def test_chat_message_replace_uses_revision_cas_and_returns_current_snapshot(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-revision.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "版本控制"}
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            assert created.json()["messages_revision"] == 0

            first = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "base_revision": 0,
                    "snapshot_complete": True,
                    "messages": [
                        {"id": "msg-revision-a", "role": "user", "content": "A"}
                    ],
                },
            )
            assert first.status_code == 200
            assert first.json()["messages_revision"] == 1

            second = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": False,
                    "messages": [
                        {"id": "msg-revision-b", "role": "assistant", "content": "B"}
                    ],
                },
            )
            assert second.status_code == 200
            assert second.json()["messages_revision"] == 2

            stale = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "base_revision": 1,
                    "snapshot_complete": True,
                    "messages": [
                        {"id": "msg-revision-a", "role": "user", "content": "A"}
                    ],
                },
            )
            assert stale.status_code == 409
            detail = stale.json()["detail"]
            assert detail["code"] == "chat_revision_conflict"
            assert detail["current_revision"] == 2
            assert [item["id"] for item in detail["current_session"]["messages"]] == [
                "msg-revision-a",
                "msg-revision-b",
            ]

            current = client.get(f"/chat/sessions/{session_id}", headers=headers)
            assert current.status_code == 200
            assert [item["id"] for item in current.json()["messages"]] == [
                "msg-revision-a",
                "msg-revision-b",
            ]
            assert current.json()["messages_revision"] == 2
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_legacy_replace_merges_by_message_id_without_deleting_database_only_rows(
    tmp_path,
):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-legacy-merge.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "旧客户端"}
            )
            assert created.status_code == 201
            session_id = created.json()["id"]

            seeded = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "base_revision": 0,
                    "snapshot_complete": True,
                    "messages": [
                        {
                            "id": "msg-legacy-a",
                            "role": "user",
                            "content": "A",
                            "parts": [{"type": "content", "text": "A"}],
                            "references": [{"document_id": "doc-1", "excerpt": "证据"}],
                            "usage": {
                                "provider": "provider",
                                "model": "model",
                                "input_tokens": 1,
                                "output_tokens": 2,
                                "total_tokens": 3,
                                "cost": "0.01",
                                "latency_ms": 1,
                            },
                        },
                        {
                            "id": "msg-database-only",
                            "role": "assistant",
                            "content": "数据库独有",
                        },
                    ],
                },
            )
            assert seeded.status_code == 200

            legacy = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "messages": [
                        {"id": "msg-legacy-a", "role": "user", "content": "A2"},
                        {
                            "id": "msg-legacy-new",
                            "role": "assistant",
                            "content": "新消息",
                        },
                    ],
                },
            )
            assert legacy.status_code == 200
            assert legacy.json()["messages_revision"] == 2
            assert (
                next(
                    item
                    for item in legacy.json()["messages"]
                    if item["id"] == "msg-legacy-a"
                )["content"]
                == "A"
            )
            merged = next(
                item
                for item in legacy.json()["messages"]
                if item["id"] == "msg-legacy-a"
            )
            assert merged["parts"] == [{"type": "content", "text": "A"}]
            assert merged["references"] == [{"document_id": "doc-1", "excerpt": "证据"}]
            assert merged["usage"]["total_tokens"] == 3
            assert {item["id"] for item in legacy.json()["messages"]} == {
                "msg-legacy-a",
                "msg-database-only",
                "msg-legacy-new",
            }
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_messages_revision_claim_is_atomic_across_sqlite_sessions(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-revision-race.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "并发版本"}
            )
            assert created.status_code == 201
            session_id = created.json()["id"]

            barrier = threading.Barrier(2)

            def attempt(index: int):
                with database.SessionLocal() as db:
                    barrier.wait(timeout=10)
                    claimed = chat_repo.claim_messages_revision(db, session_id, 0)
                    if claimed is None:
                        db.rollback()
                        return (index, "conflict")
                    db.commit()
                    return (index, claimed)

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(attempt, (1, 2)))

            assert {result[1] for result in results} == {"conflict", 1}
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_legacy_replace_concurrent_sessions_preserves_both_message_ids(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-legacy-race.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            owner_id = admin["user"]["id"]
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "旧客户端并发"}
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            barrier = threading.Barrier(2)

            def persist(message_id: str) -> None:
                with database.SessionLocal() as db:
                    barrier.wait(timeout=10)
                    session = chat_repo.persist_chat_turn(
                        db,
                        session_id=session_id,
                        owner_user_id=owner_id,
                        payloads=[
                            {
                                "id": message_id,
                                "role": "assistant",
                                "content": message_id,
                            }
                        ],
                        replace=True,
                        base_revision=None,
                    )
                    assert session is not None
                    db.commit()

            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(persist, ("legacy-left", "legacy-right")))

            detail = client.get(f"/chat/sessions/{session_id}", headers=headers)
            assert detail.status_code == 200
            assert {item["id"] for item in detail.json()["messages"]} == {
                "legacy-left",
                "legacy-right",
            }
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_full_replace_concurrent_sessions_has_one_cas_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-full-replace-race.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            owner_id = admin["user"]["id"]
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "完整快照并发"}
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            barrier = threading.Barrier(2)

            def persist(message_id: str) -> tuple[str, str | int]:
                with database.SessionLocal() as db:
                    barrier.wait(timeout=10)
                    try:
                        saved = chat_repo.persist_chat_turn(
                            db,
                            session_id=session_id,
                            owner_user_id=owner_id,
                            payloads=[
                                {
                                    "id": message_id,
                                    "role": "assistant",
                                    "content": message_id,
                                }
                            ],
                            replace=True,
                            base_revision=0,
                            snapshot_complete=True,
                        )
                        assert saved is not None
                        db.commit()
                        return ("success", message_id)
                    except chat_repo.ChatMessagesRevisionConflict:
                        db.rollback()
                        return ("conflict", message_id)

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(persist, ("replace-left", "replace-right")))

            assert [result[0] for result in results].count("success") == 1
            assert [result[0] for result in results].count("conflict") == 1
            detail = client.get(f"/chat/sessions/{session_id}", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["messages_revision"] == 1
            assert len(detail.json()["messages"]) == 1
            winner = next(result[1] for result in results if result[0] == "success")
            assert detail.json()["messages"][0]["id"] == winner
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_legacy_same_id_merge_keeps_server_content_and_unions_metadata(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-same-id-authority.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "字段合并"}
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            server_trace = {
                "schema_version": 1,
                "mode": "server",
                "method": "hybrid",
                "route_reason": "server route",
                "pool_size": 2,
                "selected_count": 1,
                "channels": {"lexical": True, "vector": False, "graph": False},
                "degraded": False,
                "degradation_reason": None,
                "citation_count": 1,
                "grounding_status": "grounded",
            }
            seeded = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "base_revision": 0,
                    "snapshot_complete": True,
                    "messages": [
                        {
                            "id": "msg-same-id",
                            "role": "assistant",
                            "content": "server authority",
                            "parts": [
                                {
                                    "type": "tool",
                                    "tool_call_id": "server-call",
                                    "tool_name": "search",
                                    "status": "done",
                                    "output_text": "server output",
                                }
                            ],
                            "references": [
                                {
                                    "document_id": "doc-server",
                                    "document_name": "服务端依据",
                                    "page_or_section": "1",
                                    "excerpt": "server",
                                }
                            ],
                            "retrieval_trace": server_trace,
                            "usage": {
                                "provider": "provider",
                                "model": "model",
                                "input_tokens": 5,
                                "input_uncached_tokens": 4,
                                "cache_read_tokens": 1,
                                "cache_write_tokens": 0,
                                "output_tokens": 6,
                                "total_tokens": 11,
                                "latency_ms": 20,
                            },
                        }
                    ],
                },
            )
            assert seeded.status_code == 200

            merged = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "messages": [
                        {
                            "id": "msg-same-id",
                            "role": "assistant",
                            "content": "local content is longer but must not win",
                            "parts": [
                                {
                                    "type": "tool",
                                    "tool_call_id": "local-call",
                                    "tool_name": "search",
                                    "status": "done",
                                    "output_text": "local output",
                                }
                            ],
                            "references": [
                                {
                                    "document_id": "doc-local",
                                    "document_name": "本地依据",
                                    "page_or_section": "2",
                                    "excerpt": "local",
                                }
                            ],
                            "retrieval_trace": {
                                **server_trace,
                                "mode": "local",
                                "pool_size": 3,
                                "selected_count": 2,
                                "channels": {
                                    "lexical": False,
                                    "vector": True,
                                    "graph": False,
                                },
                            },
                            "usage": {
                                "provider": "provider",
                                "model": "model",
                                "input_tokens": 7,
                                "input_uncached_tokens": 6,
                                "cache_read_tokens": 2,
                                "cache_write_tokens": 1,
                                "output_tokens": 8,
                                "total_tokens": 15,
                                "latency_ms": 30,
                            },
                        }
                    ],
                },
            )
            assert merged.status_code == 200
            message = next(
                item
                for item in merged.json()["messages"]
                if item["id"] == "msg-same-id"
            )
            assert message["content"] == "server authority"
            assert {part["tool_call_id"] for part in message["parts"]} == {
                "server-call",
                "local-call",
            }
            assert {item["document_id"] for item in message["references"]} == {
                "doc-server",
                "doc-local",
            }
            assert message["usage"]["input_tokens"] == 7
            assert message["usage"]["cache_read_tokens"] == 2
            assert message["usage"]["cache_write_tokens"] == 1
            assert message["retrieval_trace"]["channels"] == {
                "lexical": True,
                "vector": True,
                "graph": False,
            }
            assert message["retrieval_trace"]["pool_size"] == 3
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_append_messages_does_not_take_over_another_owners_session(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-owner-collision.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=admin_headers,
                json={"title": "管理员会话"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]

            created_user = client.post(
                "/admin/users",
                headers=admin_headers,
                json={
                    "username": "chat-collision-owner",
                    "display_name": "另一位用户",
                    "password": "Collision-123!",
                },
            )
            assert created_user.status_code == 201
            other = client.post(
                "/auth/login",
                json={
                    "username": "chat-collision-owner",
                    "password": "Collision-123!",
                },
            )
            assert other.status_code == 200
            other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}

            collision = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=other_headers,
                json={
                    "messages": [
                        {
                            "id": "collision-message",
                            "role": "user",
                            "content": "不应写入",
                        }
                    ]
                },
            )
            assert collision.status_code == 404
            assert collision.json() == {
                "detail": {
                    "code": "chat_session_not_found",
                    "message": "会话不存在",
                }
            }

            owner_detail = client.get(
                f"/chat/sessions/{session_id}", headers=admin_headers
            )
            assert owner_detail.status_code == 200
            assert owner_detail.json()["message_count"] == 0
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_message_id_collisions_are_remapped_per_session_and_batch(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.main import app

    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = Path(tmp_path) / "chat-message-id-collision.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            source = client.post(
                "/chat/sessions", headers=headers, json={"title": "源会话"}
            ).json()
            target = client.post(
                "/chat/sessions", headers=headers, json={"title": "目标会话"}
            ).json()

            source_saved = client.post(
                f"/chat/sessions/{source['id']}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "messages": [
                        {
                            "id": "msg-shared",
                            "role": "user",
                            "content": "源消息",
                        }
                    ],
                },
            )
            assert source_saved.status_code == 200

            append_collision = client.post(
                f"/chat/sessions/{target['id']}/messages",
                headers=headers,
                json={
                    "replace": False,
                    "messages": [
                        {
                            "id": "msg-shared",
                            "role": "user",
                            "content": "目标增量消息",
                        }
                    ],
                },
            )
            assert append_collision.status_code == 200
            append_message = append_collision.json()["messages"][0]
            assert append_message["id"] != "msg-shared"

            replace_collision = client.post(
                f"/chat/sessions/{target['id']}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "base_revision": append_collision.json()["messages_revision"],
                    "snapshot_complete": True,
                    "messages": [
                        {
                            "id": "msg-shared",
                            "role": "user",
                            "content": "目标问题",
                        },
                        {
                            "id": "msg-shared",
                            "role": "assistant",
                            "content": "目标回答",
                        },
                    ],
                },
            )
            assert replace_collision.status_code == 200
            target_detail = replace_collision.json()
            target_messages = target_detail["messages"]
            target_ids = [item["id"] for item in target_messages]
            assert target_detail["message_count"] == 2
            assert len(set(target_ids)) == 2
            assert "msg-shared" not in target_ids

            source_detail = client.get(
                f"/chat/sessions/{source['id']}", headers=headers
            )
            assert source_detail.status_code == 200
            assert [item["id"] for item in source_detail.json()["messages"]] == [
                "msg-shared"
            ]

            retry = client.post(
                f"/chat/sessions/{target['id']}/messages",
                headers=headers,
                json={"replace": False, "messages": target_messages},
            )
            assert retry.status_code == 200
            assert retry.json()["message_count"] == 2
            assert [item["id"] for item in retry.json()["messages"]] == target_ids
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(Path(tmp_path) / "chat-message-id-collision.sqlite3")


def test_replace_does_not_erase_a_newer_snapshot(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.main import app

    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = Path(tmp_path) / "chat-snapshot-order.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "并发保存"}
            )
            assert created.status_code == 201
            session_id = created.json()["id"]

            first = {
                "id": "msg-first",
                "role": "user",
                "content": "第一条",
                "created_at": "2026-08-25T10:00:00+00:00",
            }
            second = {
                "id": "msg-second",
                "role": "assistant",
                "content": "第二条",
                "created_at": "2026-08-25T10:00:01+00:00",
            }
            assert (
                client.post(
                    f"/chat/sessions/{session_id}/messages",
                    headers=headers,
                    json={"replace": True, "messages": [first]},
                ).status_code
                == 200
            )
            newer = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={"replace": True, "messages": [first, second]},
            )
            assert newer.status_code == 200
            stale = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={"replace": True, "messages": [first]},
            )

            assert stale.status_code == 200
            assert [item["id"] for item in stale.json()["messages"]] == [
                "msg-first",
                "msg-second",
            ]
            delayed = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": False,
                    "messages": [
                        {
                            "id": "msg-delayed",
                            "role": "assistant",
                            "content": "延迟到达",
                            "created_at": "2026-08-25T09:59:00+00:00",
                        }
                    ],
                },
            )
            assert delayed.status_code == 200
            assert [
                (item["id"], item["role"]) for item in delayed.json()["messages"]
            ] == [
                ("msg-first", "user"),
                ("msg-second", "assistant"),
                ("msg-delayed", "assistant"),
            ]
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(Path(tmp_path) / "chat-snapshot-order.sqlite3")


@pytest.mark.anyio
async def test_stream_chat_uses_backend_owned_pi_runtime(monkeypatch):
    from app.services.pi_runtime.runner import PiRunResult

    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
    monkeypatch.setattr(settings, "pi_agent_id", "legacy-agent")
    monkeypatch.setattr(settings, "agent_chat_agent_id", "legacy-agent")

    class FakePiClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def chat_async(self, message, **kwargs):
            claims = decode_capability_token(kwargs["capability_token"])
            assert claims["agent_id"] == chat_api.PI_RUNTIME_AGENT_ID
            assert self.kwargs["agent_id"] == chat_api.PI_RUNTIME_AGENT_ID
            assert message == "本地请求"
            assert kwargs["session_id"] == "pi-chat-usr-pi"
            return (
                PiRunResult(
                    reply="本地回复",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 4,
                        "completion_tokens": 3,
                        "total_tokens": 7,
                    },
                    generated_files=[
                        {
                            "name": "report.docx",
                            "path": "report.docx",
                            "size": 12,
                            "url": "/api/files/pi-chat-usr-pi/report.docx",
                        }
                    ],
                ),
                object(),
            )

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)

    def fake_user():
        user = MagicMock()
        user.id = "usr-pi"
        user.auth_version = 1
        return user

    app.dependency_overrides[chat_api.current_user] = fake_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/chat/messages",
                json={"message": "本地请求", "session_id": "pi-chat-usr-pi"},
            )
        assert response.status_code == 200
        payloads = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert payloads[-1]["generated_files"][0]["url"].endswith("report.docx")
        assert payloads[0] == {
            "type": "session",
            "session_id": "pi-chat-usr-pi",
        }
        assert payloads[-1]["type"] == "done"
        assert payloads[-1]["reply"] == "本地回复"
        assert payloads[-1]["token_usage"]["total_tokens"] == 7
    finally:
        app.dependency_overrides.pop(chat_api.current_user, None)


@pytest.mark.anyio
async def test_stream_chat_persists_server_turn_before_done(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = Path(tmp_path) / "chat-server-persistence.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    runtime_calls = 0

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def chat_async(self, message, **kwargs):
            nonlocal runtime_calls
            runtime_calls += 1
            await kwargs["on_event"](
                {
                    "type": "tool_execution_start",
                    "toolCallId": "call-read-1",
                    "toolName": "read",
                    "args": {"path": "report.txt", "api_key": "must-not-persist"},
                }
            )
            await kwargs["on_event"](
                {
                    "type": "tool_execution_end",
                    "toolCallId": "call-read-1",
                    "toolName": "read",
                    "isError": False,
                    "result": {"content": [{"type": "text", "text": "读取完成"}]},
                }
            )
            assert message == "服务端兜底"
            assert kwargs["session_id"].startswith("pi-chat-")
            return (
                PiRunResult(
                    reply="服务端已保存",
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
    try:
        with TestClient(app) as client:
            payload = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {payload['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "服务端持久化"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]

            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "服务端兜底",
                    "local_session_id": session_id,
                    "client_message_id": "msg-client-user",
                    "client_assistant_message_id": "msg-client-assistant",
                },
            )
            assert response.status_code == 200
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            done = events[-1]
            assert done["type"] == "done"
            assert done["server_persisted"] is True
            assert done["client_message_id"] == "msg-client-user"
            assert done["client_assistant_message_id"] == "msg-client-assistant"

            detail = client.get(f"/chat/sessions/{session_id}", headers=headers)
            assert detail.status_code == 200
            assert done["messages_revision"] == detail.json()["messages_revision"]
            assert [
                (item["id"], item["role"], item["content"])
                for item in detail.json()["messages"]
            ] == [
                ("msg-client-user", "user", "服务端兜底"),
                ("msg-client-assistant", "assistant", "服务端已保存"),
            ]
            persisted_assistant = detail.json()["messages"][1]
            assert persisted_assistant["parts"] == [
                {
                    "type": "tool",
                    "text": "",
                    "tool_call_id": "call-read-1",
                    "tool_name": "read",
                    "status": "done",
                    "args_text": '{\n  "path": "report.txt",\n  "api_key": "[REDACTED]"\n}',
                    "output_text": "读取完成",
                },
                {"type": "content", "text": "服务端已保存"},
            ]
            assert "must-not-persist" not in json.dumps(
                persisted_assistant, ensure_ascii=False
            )
            partial = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": False,
                    "messages": [
                        {
                            "id": "msg-client-user",
                            "role": "user",
                            "content": "服务端兜底",
                        },
                        {
                            "id": "msg-client-assistant",
                            "role": "assistant",
                            "content": "浏览器中断",
                        },
                    ],
                },
            )
            assert partial.status_code == 200
            assert partial.json()["messages"][1]["content"] == "服务端已保存"
            chat_api._persist_server_chat_messages(
                user_id=payload["user"]["id"],
                local_session_id=session_id,
                payloads=[
                    {
                        "id": "msg-guidance-after-completion",
                        "role": "user",
                        "content": "只报告异常",
                    }
                ],
            )
            reordered = client.get(
                f"/chat/sessions/{session_id}", headers=headers
            ).json()
            assert [item["id"] for item in reordered["messages"]] == [
                "msg-client-user",
                "msg-client-assistant",
                "msg-guidance-after-completion",
            ]

            duplicate = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "服务端兜底",
                    "local_session_id": session_id,
                    "client_message_id": "msg-client-user",
                    "client_assistant_message_id": "msg-client-assistant",
                },
            )
            assert duplicate.status_code == 200
            duplicate_events = [
                json.loads(line[6:])
                for line in duplicate.text.splitlines()
                if line.startswith("data: ")
            ]
            assert runtime_calls == 1
            assert duplicate_events[-1]["type"] == "done"
            assert duplicate_events[-1]["reply"] == "服务端已保存"
            replayed_tool_events = [
                event
                for event in duplicate_events
                if event.get("type") == "event"
                and event.get("item", {}).get("type") == "PiToolExecutionEvent"
            ]
            assert [
                event["item"]["content"]["type"] for event in replayed_tool_events
            ] == ["tool_execution_start", "tool_execution_end"]
            changed_content = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "这是另一条请求",
                    "local_session_id": session_id,
                    "client_message_id": "msg-client-user",
                    "client_assistant_message_id": "msg-client-assistant",
                },
            )
            assert changed_content.status_code == 409
            assert changed_content.json()["detail"]["code"] == "chat_request_conflict"
            changed_user_id = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "服务端兜底",
                    "local_session_id": session_id,
                    "client_message_id": "msg-other-user",
                    "client_assistant_message_id": "msg-client-assistant",
                },
            )
            assert changed_user_id.status_code == 409
            assert changed_user_id.json()["detail"]["code"] == "chat_request_conflict"
            assert runtime_calls == 1
            assert (
                client.get(f"/chat/sessions/{session_id}", headers=headers).json()[
                    "message_count"
                ]
                == 3
            )
            assert (
                client.delete(
                    f"/chat/sessions/{session_id}", headers=headers
                ).status_code
                == 204
            )
            recovered = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "服务端兜底",
                    "local_session_id": session_id,
                    "client_message_id": "msg-recovered-user",
                    "client_assistant_message_id": "msg-recovered-assistant",
                },
            )
            assert recovered.status_code == 200
            recovered_events = [
                json.loads(line[6:])
                for line in recovered.text.splitlines()
                if line.startswith("data: ")
            ]
            assert recovered_events[-1]["server_persisted"] is True
            recovered_detail = client.get(
                f"/chat/sessions/{session_id}", headers=headers
            )
            assert recovered_detail.status_code == 200
            assert recovered_detail.json()["message_count"] == 2
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(Path(tmp_path) / "chat-server-persistence.sqlite3")


@pytest.mark.anyio
async def test_completed_message_replay_emits_persisted_runtime_error():
    chunks = [
        chunk
        async for chunk in chat_api._completed_message_replay_stream(
            message={
                "content": "模型调用失败。",
                "parts": [
                    {
                        "type": "error",
                        "error_id": "assistant-error:replay",
                        "error_kind": "model",
                        "text": "Provider rejected the request",
                        "metadata_text": '{"model":"test-model"}',
                    },
                    {"type": "content", "text": "模型调用失败。"},
                ],
                "usage": {},
                "writing_project_id": "wpr-replay-message",
            },
            runtime_session_id="pi-chat-replay",
            client_message_id="msg-replay-user",
            client_assistant_message_id="msg-replay-assistant",
        )
    ]
    events = [
        json.loads(line[6:])
        for chunk in chunks
        for line in chunk.decode().splitlines()
        if line.startswith("data: ")
    ]

    replayed_error = next(
        event
        for event in events
        if event.get("type") == "event"
        and event.get("item", {}).get("type") == "PiRuntimeEvent"
    )
    assert replayed_error["item"]["content"] == {
        "type": "chat_error",
        "errorId": "assistant-error:replay",
        "errorKind": "model",
        "text": "模型服务未能完成本轮处理。",
    }
    assert events[-1]["type"] == "done"
    assert events[-1]["writing_project_id"] == "wpr-replay-message"


@pytest.mark.anyio
async def test_completed_message_replay_redacts_reply_and_runtime_parts():
    secret = "replay-api-secret-7319"
    chunks = [
        chunk
        async for chunk in chat_api._completed_message_replay_stream(
            message={
                "content": f"历史回答 api_key={secret}",
                "references": [
                    {"url": f"https://host/path?sig={secret}"},
                ],
                "parts": [
                    {
                        "type": "content",
                        "text": f"历史回答 api_key={secret}",
                    },
                    {
                        "type": "tool",
                        "tool_call_id": "replay-tool",
                        "tool_name": "inspect",
                        "status": "error",
                        "args_text": f"--api_key={secret}",
                        "output_text": f"Authorization: Bearer {secret}",
                    },
                ],
                "usage": {"model": f"effective-api_key={secret}"},
            },
            runtime_session_id="pi-chat-replay-secret",
            client_message_id="msg-replay-user",
            client_assistant_message_id="msg-replay-assistant",
        )
    ]
    replay_text = b"".join(chunks).decode()

    assert secret not in replay_text
    payloads = [
        json.loads(line[6:])
        for line in replay_text.splitlines()
        if line.startswith("data: ")
    ]
    assert payloads[-1]["type"] == "done"
    assert payloads[-1]["reply"] == "历史回答 api_key=[REDACTED]"
    assert payloads[-1]["effective_model"] == "effective-api_key=[REDACTED]"


def test_redact_assistant_payload_preserves_absent_usage_semantics():
    without_usage = chat_api._redact_assistant_payload(
        {"role": "assistant", "content": "完成"}
    )
    null_usage = chat_api._redact_assistant_payload(
        {"role": "assistant", "content": "完成", "usage": None}
    )

    assert "usage" not in without_usage
    assert null_usage["usage"] is None


def test_session_detail_redacts_persisted_assistant_reply_and_generated_files(
    monkeypatch,
):
    secret = "history-api-secret-7319"
    payload = {
        "id": "session-history-redaction",
        "title": "历史脱敏",
        "messages_revision": 1,
        "agent_session_id": None,
        "preferred_model": None,
        "category_id": None,
        "category_name": None,
        "pinned": False,
        "pinned_at": None,
        "archived": False,
        "archived_at": None,
        "writing_project_id": None,
        "message_count": 1,
        "created_at": "2026-09-04T00:00:00+00:00",
        "updated_at": "2026-09-04T00:00:00+00:00",
        "messages": [
            {
                "id": "assistant-history-redaction",
                "role": "assistant",
                "content": f"历史 api_key={secret}",
                "thinking": f"thinking access_token={secret}",
                "parts": [{"type": "content", "text": f"api_key={secret}"}],
                "references": [{"url": f"https://host/path?sig={secret}"}],
                "created_at": "2026-09-04T00:00:00+00:00",
            }
        ],
    }
    monkeypatch.setattr(
        chat_api.chat_repo,
        "session_detail_dict",
        lambda _session, **_kwargs: payload,
    )
    monkeypatch.setattr(
        chat_api, "_history_dispatch_projection", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        chat_api.chat_repo,
        "get_session_message_page",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )

    response = chat_api._session_detail_response(
        SimpleNamespace(id="session-history-redaction", owner_user_id="user-1"),
        object(),
    )

    serialized = json.dumps(response.model_dump(), ensure_ascii=False)
    assert secret not in serialized
    assistant = response.messages[0].model_dump()
    assert assistant["content"] == "历史 api_key=[REDACTED]"
    assert assistant["thinking"] == "thinking access_token=[REDACTED]"
    assert assistant["parts"][0]["text"] == "api_key=[REDACTED]"
    assert assistant["references"][0]["url"] == "https://host/path?sig=[REDACTED]"


@pytest.mark.anyio
async def test_stream_chat_redacts_split_runtime_events_reply_and_persistence(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from app.core import database
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = tmp_path / "chat-stream-redaction.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    persisted: list[list[dict[str, Any]]] = []
    tool_secret = "chat-tool-split-secret-7319"
    tool_content = [
        {"type": "text", "text": "api_key="},
        {"type": "text", "text": tool_secret},
        {"type": "text", "text": "\n普通工具尾巴"},
    ]

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **kwargs):
            await kwargs["on_event"](
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "delta": "普通跨",
                    },
                }
            )
            await kwargs["on_event"](
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "toolcall_end",
                        "toolCall": {
                            "id": "chat-split-tool",
                            "name": "inspect",
                            "arguments": {"content": tool_content},
                        },
                    },
                }
            )
            await kwargs["on_event"](
                {
                    "type": "tool_execution_update",
                    "toolCallId": "chat-split-tool",
                    "toolName": "inspect",
                    "partialResult": {"content": tool_content},
                }
            )
            await kwargs["on_event"](
                {
                    "type": "tool_execution_end",
                    "toolCallId": "chat-split-tool",
                    "toolName": "inspect",
                    "result": {"content": tool_content},
                    "isError": False,
                }
            )
            await kwargs["on_event"](
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "delta": "块文本\napi_",
                    },
                }
            )
            await kwargs["on_event"](
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "delta": "key=split-secret-7319\n尾巴",
                    },
                }
            )
            await kwargs["on_event"](
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "delta": "思考\napi_key=sec",
                    },
                }
            )
            await kwargs["on_event"](
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "delta": "ret",
                    },
                }
            )
            return (
                PiRunResult(
                    reply="普通跨块文本\napi_key=split-secret-7319\n尾巴",
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

    def capture_persistence(**kwargs):
        persisted.append(kwargs["payloads"])
        return len(persisted)

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    monkeypatch.setattr(
        chat_api,
        "_persist_server_chat_messages_with_retry",
        capture_persistence,
    )
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "流式脱敏"},
            )
            assert created.status_code == 201
            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "测试跨块文本",
                    "local_session_id": created.json()["id"],
                    "client_message_id": "msg-redaction-user",
                    "client_assistant_message_id": "msg-redaction-assistant",
                },
            )
            assert response.status_code == 200
            payloads = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]

        secret = "split-secret-7319"
        serialized = json.dumps(payloads, ensure_ascii=False)
        assert secret not in serialized
        live_text = "".join(
            item["item"]["content"]
            for item in payloads
            if item.get("type") == "event"
            and item.get("item", {}).get("type") == "ModelClientStreamingChunkEvent"
        )
        assert live_text == "普通跨块文本\napi_key=[REDACTED]\n尾巴"
        live_thinking = "".join(
            item["item"]["content"]
            for item in payloads
            if item.get("type") == "event"
            and item.get("item", {}).get("type") == "PiThinkingChunkEvent"
        )
        assert live_thinking == "思考\napi_key=[REDACTED]"
        assert payloads[-1]["type"] == "done"
        assert payloads[-1]["reply"] == "普通跨块文本\napi_key=[REDACTED]\n尾巴"
        assistant_payload = persisted[-1][1]
        assert assistant_payload["content"] == "普通跨块文本\napi_key=[REDACTED]\n尾巴"
        assert (
            "".join(
                part["text"]
                for part in assistant_payload["parts"]
                if part.get("type") == "content"
            )
            == "普通跨块文本\napi_key=[REDACTED]\n尾巴"
        )
        assert "思考" in assistant_payload["thinking"]
        assert "api_key=[REDACTED]" in assistant_payload["thinking"]
        assert "api_key=secret" not in assistant_payload["thinking"]
        assert secret not in json.dumps(assistant_payload, ensure_ascii=False)
        assert "api_key=secret" not in serialized
        assert tool_secret not in serialized
        tool_part = next(
            part for part in assistant_payload["parts"] if part.get("type") == "tool"
        )
        assert tool_secret not in json.dumps(tool_part, ensure_ascii=False)
        assert "普通工具尾巴" in tool_part["args_text"]
        assert "普通工具尾巴" in tool_part["output_text"]
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(tmp_path / "chat-stream-redaction.sqlite3")


def test_append_messages_redacts_assistant_before_database_persistence(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.models.chat import ChatMessage

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-append-redaction.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    secret = "append-api-secret-7319"
    tool_args = "argument " * 1_000 + f"\napi_key={secret}\nargs-tail"
    tool_output = "persistence-proof " * 3_000 + f"\napi_key={secret}\noutput-tail"
    expected_tool = {
        "type": "tool",
        "tool_call_id": "full-persist-tool",
        "tool_name": "read",
        "status": "done",
        "text": "",
        "args_text": tool_args.replace(secret, "[REDACTED]"),
        "output_text": tool_output.replace(secret, "[REDACTED]"),
    }
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "写入脱敏"}
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            response = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "base_revision": created.json()["messages_revision"],
                    "snapshot_complete": True,
                    "messages": [
                        {
                            "id": "append-user-redaction",
                            "role": "user",
                            "content": "问题",
                        },
                        {
                            "id": "append-assistant-redaction",
                            "role": "assistant",
                            "content": f"回答 api_key={secret}",
                            "thinking": f"thinking access_token={secret}",
                            "parts": [
                                {"type": "content", "text": f"回答 api_key={secret}"},
                                {
                                    **expected_tool,
                                    "args_text": tool_args,
                                    "output_text": tool_output,
                                },
                            ],
                        },
                    ],
                },
            )
            assert response.status_code == 200
            restored = client.get(f"/chat/sessions/{session_id}", headers=headers)
            assert restored.status_code == 200
            assert restored.json()["messages"][1]["parts"][1] == expected_tool

        with database.SessionLocal() as db:
            assistant = db.get(ChatMessage, "append-assistant-redaction")
            assert assistant is not None
            assert assistant.content == "回答 api_key=[REDACTED]"
            assert assistant.thinking == "thinking access_token=[REDACTED]"
            assert json.loads(assistant.parts_json or "[]") == [
                {"type": "content", "text": "回答 api_key=[REDACTED]"},
                expected_tool,
            ]
            assert secret not in json.dumps(
                json.loads(assistant.parts_json or "[]"), ensure_ascii=False
            )
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


@pytest.mark.anyio
async def test_stream_chat_redacts_generated_file_metadata_in_done(monkeypatch):
    from app.services.pi_runtime.runner import PiRunResult

    secret = "stream-file-secret-7319"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **_kwargs):
            return (
                PiRunResult(
                    reply="已生成文件",
                    stats={},
                    state={},
                    events=[],
                    token_usage={},
                    generated_files=[
                        {
                            "name": "report.txt",
                            "url": f"/files/report.txt?sig={secret}",
                        }
                    ],
                ),
                object(),
            )

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)

    def fake_user():
        user = MagicMock()
        user.id = "usr-stream-file-redaction"
        user.auth_version = 1
        return user

    app.dependency_overrides[chat_api.current_user] = fake_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/chat/messages",
                json={
                    "message": "生成文件",
                    "session_id": "pi-chat-usr-stream-file-redaction",
                },
            )
        assert response.status_code == 200
        assert secret not in response.text
        payloads = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert payloads[-1]["generated_files"][0]["url"] == (
            "/files/report.txt?sig=[REDACTED]"
        )
    finally:
        app.dependency_overrides.pop(chat_api.current_user, None)


@pytest.mark.anyio
async def test_historical_error_parts_are_safe_in_history_recovery_and_replay():
    secret = "historical-error-secret-7319"
    raw_provider_error = f"Provider rejected api_key={secret}"
    message = SimpleNamespace(
        id="msg-historical-error-assistant",
        role="assistant",
        content=f"已完成第一步。\n{raw_provider_error}",
        thinking=f"先核对资料。\n{raw_provider_error}",
        parts_json=json.dumps(
            [
                {
                    "type": "error",
                    "error_id": "assistant-error:historical",
                    "error_kind": "model",
                    "text": raw_provider_error,
                    "metadata_text": f"Authorization: Bearer {secret}",
                    "auto_retry_scheduled": True,
                },
                {
                    "type": "tool",
                    "tool_call_id": "runtime-error-historical",
                    "tool_name": "runtime",
                    "status": "error",
                    "args_text": f"password={secret}",
                    "output_text": f"Runtime failed Bearer {secret}",
                },
                {"type": "content", "text": f"已完成第一步。\n{raw_provider_error}"},
                {"type": "thinking", "text": f"先核对资料。\n{raw_provider_error}"},
            ],
            ensure_ascii=False,
        ),
        space_ids_json="[]",
        document_ids_json="[]",
        writing_context_json=None,
        references_json="[]",
        session_references_json="[]",
        writing_references_json="[]",
        images_json="[]",
        retrieval_summary=None,
        retrieval_trace_json=None,
        writing_project_id=None,
        usage=None,
        created_at=datetime.now(timezone.utc),
    )

    history_message = chat_repo.message_to_dict(message)
    recovery_text = chat_api._context_message_text(message)
    replay_chunks = [
        chunk
        async for chunk in chat_api._completed_message_replay_stream(
            message=history_message,
            runtime_session_id="pi-chat-historical-error",
            client_message_id="msg-historical-error-user",
            client_assistant_message_id=message.id,
        )
    ]
    replay_text = b"".join(replay_chunks).decode()

    serialized_history = json.dumps(history_message, ensure_ascii=False)
    assert secret not in serialized_history
    assert secret not in recovery_text
    assert secret not in replay_text
    assert "Provider rejected" not in serialized_history
    assert "Runtime failed" not in serialized_history
    assert "Provider rejected" not in recovery_text
    assert "Runtime failed" not in replay_text
    assert "已完成第一步。" in recovery_text
    assert history_message["content"] == "已完成第一步。"
    assert history_message["parts"][-2]["text"] == "已完成第一步。"
    assert history_message["thinking"] == "先核对资料。"
    assert history_message["parts"][-1]["text"] == "先核对资料。"
    assert message.thinking == f"先核对资料。\n{raw_provider_error}"
    assert history_message["parts"][0]["auto_retry_scheduled"] is False
    assert [part["type"] for part in history_message["parts"]] == [
        "error",
        "error",
        "content",
        "thinking",
    ]
    assert [part["text"] for part in history_message["parts"][:2]] == [
        "模型服务未能完成本轮处理。",
        "本轮处理未能完成，请稍后重试。",
    ]

    message.parts_json = "[]"
    message.thinking = json.dumps({"error": {"message": raw_provider_error}})
    legacy = chat_repo.message_to_dict(message)
    assert legacy["thinking"] is None
    assert raw_provider_error in message.thinking
    message.thinking = "先核对 HTTP、JSON 和 trace 文档，再处理任务。"
    assert chat_repo.message_to_dict(message)["thinking"] == message.thinking
    message.usage = json.dumps({"status": "failed"})
    message.thinking = raw_provider_error
    assert chat_repo.message_to_dict(message)["thinking"] is None
    assert message.thinking == raw_provider_error


@pytest.mark.parametrize(
    ("error_message", "error_code"),
    [("provider failed", "upstream_error"), ("capability denied", "permission_denied")],
)
@pytest.mark.anyio
async def test_top_level_pi_failure_without_events_persists_structured_error(
    tmp_path, monkeypatch, error_message, error_code
):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.services.pi_agent_client import PiAgentError
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-empty-event-failure.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    runtime_messages: list[str] = []
    attempts = 0
    secret = "empty-event-secret-7319"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **_kwargs):
            nonlocal attempts
            attempts += 1
            runtime_messages.append(message)
            if attempts == 1:
                raise PiAgentError(
                    f"{error_message} password={secret} " + ("x" * 10_000)
                )
            return (
                PiRunResult(
                    reply="重试后成功",
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
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "无事件失败恢复"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            request = {
                "message": "调用模型",
                "local_session_id": session_id,
                "client_message_id": "msg-empty-failure-user",
                "client_assistant_message_id": "msg-empty-failure-assistant",
            }

            failed = client.post("/chat/messages", headers=headers, json=request)
            assert failed.status_code == 200
            failed_events = [
                json.loads(line[6:])
                for line in failed.text.splitlines()
                if line.startswith("data: ")
            ]
            structured_errors = [
                event["item"]["content"]
                for event in failed_events
                if event.get("type") == "event"
                and event.get("item", {}).get("content", {}).get("type") == "chat_error"
            ]
            assert len(structured_errors) == 1
            assert structured_errors[0]["errorKind"] == "runtime"
            assert structured_errors[0]["errorId"] == (
                "chat-error:msg-empty-failure-assistant"
            )
            assert json.loads(structured_errors[0]["metadataText"]) == {
                "error_code": error_code
            }
            assert failed_events[-1]["type"] == "error"
            assert secret not in json.dumps(failed_events, ensure_ascii=False)
            assert structured_errors[0]["text"] == failed_events[-1]["detail"]

            failed_detail = client.get(
                f"/chat/sessions/{session_id}",
                headers=headers,
            ).json()
            failed_assistant = failed_detail["messages"][1]
            stored_errors = [
                part
                for part in failed_assistant["parts"]
                if part.get("type") == "error"
            ]
            assert len(stored_errors) == 1
            assert stored_errors[0]["error_code"] == error_code
            assert stored_errors[0]["text"] == structured_errors[0]["text"]
            if error_code == "permission_denied":
                assert failed_events[-1]["retryable"] is False
                assert structured_errors[0]["retryable"] is False
                assert stored_errors[0]["retryable"] is False
            assert failed_assistant["usage"]["status"] == "failed"
            assert secret not in json.dumps(failed_assistant, ensure_ascii=False)

            recovered = client.post("/chat/messages", headers=headers, json=request)
            assert recovered.status_code == 200
            recovered_events = [
                json.loads(line[6:])
                for line in recovered.text.splitlines()
                if line.startswith("data: ")
            ]
            assert recovered_events[-1]["type"] == "done"
            assert error_code in runtime_messages[1]
            assert secret not in runtime_messages[1]
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_rate_limit_auto_retry_rejects_usage_side_effects_and_partial_output():
    billed = RuntimeError("rate limited")
    billed.token_usage = {"prompt_tokens": 1}
    assert chat_api._model_rate_limit_auto_retry_blocker(billed, []) == "billable_usage"

    generated = RuntimeError("rate limited")
    generated.generated_files = [{"filename": "result.docx"}]
    assert (
        chat_api._model_rate_limit_auto_retry_blocker(generated, [])
        == "generated_files"
    )

    assert (
        chat_api._model_rate_limit_auto_retry_blocker(
            RuntimeError("rate limited"),
            [{"type": "tool_execution_start", "toolCallId": "call-1"}],
        )
        == "tool_execution"
    )
    assert (
        chat_api._model_rate_limit_auto_retry_blocker(
            RuntimeError("rate limited"),
            [
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "delta": "已输出一部分",
                    },
                }
            ],
        )
        == "partial_output"
    )
    bounded = RuntimeError("Error: 429")
    bounded.retry_after_seconds = 9
    assert chat_api._model_rate_limit_auto_retry_delay_seconds(bounded) == 9
    excessive = RuntimeError("Error: 429")
    excessive.retry_after_seconds = 120
    assert chat_api._model_rate_limit_auto_retry_delay_seconds(excessive) is None
    assert (
        chat_api._model_rate_limit_auto_retry_blocker(
            RuntimeError("rate limited"),
            [],
        )
        is None
    )


@pytest.mark.anyio
async def test_rate_limit_auto_retry_continues_same_turn_without_duplicate_messages(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.services.pi_agent_client import PiAgentError
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-rate-limit-auto-retry.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    runtime_messages: list[str] = []
    waited: list[int] = []
    attempts = 0
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")

    async def no_wait(seconds: int) -> None:
        waited.append(seconds)

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            nonlocal attempts
            attempts += 1
            runtime_messages.append(message)
            if attempts == 1:
                await kwargs["on_event"](
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "stopReason": "error",
                            "errorMessage": "Error: 429",
                            "responseId": "resp-rate-limit-first",
                            "provider": "test-provider",
                            "model": "test-model",
                        },
                    }
                )
                error = PiAgentError("Error: 429")
                error.retry_after_seconds = 9
                raise error
            return (
                PiRunResult(
                    reply="等待后继续完成",
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
    monkeypatch.setattr(
        chat_api,
        "_wait_before_model_rate_limit_retry",
        no_wait,
    )
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "限流自动恢复"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "完成本轮任务",
                    "local_session_id": session_id,
                    "client_message_id": "msg-rate-limit-user",
                    "client_assistant_message_id": "msg-rate-limit-assistant",
                },
            )

            assert response.status_code == 200
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            runtime_event_types = [
                event["item"]["content"]["type"]
                for event in events
                if event.get("type") == "event"
                and event.get("item", {}).get("type") == "PiRuntimeEvent"
            ]
            assert runtime_event_types == [
                "chat_error",
                "model_retry_wait",
                "model_retry_resumed",
            ]
            assert events[-1]["type"] == "done"
            assert waited == [9]
            assert attempts == 2
            assert runtime_messages[0] != runtime_messages[1]
            assert "不要重复已经完成的工具调用" in runtime_messages[1]

            detail = client.get(
                f"/chat/sessions/{session_id}",
                headers=headers,
            ).json()
            assert detail["message_count"] == 2
            assistant = detail["messages"][1]
            assert assistant["content"] == "等待后继续完成"
            assert assistant["usage"]["status"] == "completed"
            assert assistant["usage"]["input_tokens"] == 4
            assert assistant["usage"]["output_tokens"] == 3
            assert assistant["usage"]["total_tokens"] == 7
            assert len(assistant["usage"]["calls"]) == 1
            assert assistant["parts"] == [{"type": "content", "text": "等待后继续完成"}]
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


@pytest.mark.anyio
async def test_failed_tool_timeline_is_persisted_and_retry_replaces_failure(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.services.pi_agent_client import PiAgentError
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-retry-context.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    runtime_messages: list[str] = []
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    attempts = 0
    secret = "sk-top-level-error-7319"

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **kwargs):
            nonlocal attempts
            runtime_messages.append(_message)
            attempts += 1
            await kwargs["on_event"](
                {
                    "type": "tool_execution_start",
                    "toolCallId": f"call-{attempts}",
                    "toolName": "read",
                    "args": {"path": "status.txt"},
                }
            )
            await kwargs["on_event"](
                {
                    "type": "tool_execution_end",
                    "toolCallId": f"call-{attempts}",
                    "toolName": "read",
                    "isError": attempts == 1,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": "读取失败" if attempts == 1 else "读取成功",
                            }
                        ]
                    },
                }
            )
            if attempts == 1:
                raise PiAgentError(
                    f"provider failed api_key={secret} "
                    f"Authorization: Bearer {secret} " + ("x" * 10_000)
                )
            return (
                PiRunResult(
                    reply="重试后完成",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 5,
                        "completion_tokens": 3,
                        "total_tokens": 8,
                    },
                ),
                object(),
            )

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "失败后重试"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            request = {
                "message": "读取状态",
                "local_session_id": session_id,
                "client_message_id": "msg-retry-user",
                "client_assistant_message_id": "msg-retry-assistant",
            }

            failed = client.post("/chat/messages", headers=headers, json=request)
            assert failed.status_code == 200
            failed_events = [
                json.loads(line[6:])
                for line in failed.text.splitlines()
                if line.startswith("data: ")
            ]
            assert failed_events[-1]["type"] == "error"
            assert failed_events[-1]["detail"] == (
                "本轮处理未能完成；已生成的内容和文件仍保留，请稍后重试。"
            )
            assert secret not in json.dumps(failed_events, ensure_ascii=False)
            assert "读取失败" not in json.dumps(failed_events, ensure_ascii=False)
            failed_detail = client.get(
                f"/chat/sessions/{session_id}", headers=headers
            ).json()
            assert failed_detail["message_count"] == 2
            failed_assistant = failed_detail["messages"][1]
            assert failed_assistant["usage"]["status"] == "failed"
            assert failed_assistant["parts"][0]["status"] == "error"
            assert failed_assistant["parts"][0]["output_text"] == (
                "工具执行失败，详细信息已记录。"
            )
            assert "读取失败" not in json.dumps(failed_assistant, ensure_ascii=False)
            assert "args_text" not in failed_assistant["parts"][0]
            assert "status.txt" not in json.dumps(failed_assistant, ensure_ascii=False)
            assert secret not in json.dumps(failed_assistant, ensure_ascii=False)
            assert failed_assistant["content"] == failed_events[-1]["detail"]

            # 模拟前端 GET 后把客户清洗副本同步回来；占位文案不能覆盖
            # 服务端留给恢复上下文的有界诊断。
            roundtrip = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": False,
                    "messages": failed_detail["messages"],
                },
            )
            assert roundtrip.status_code == 200
            roundtrip_assistant = roundtrip.json()["messages"][1]
            assert roundtrip_assistant["parts"][0]["output_text"] == (
                "工具执行失败，详细信息已记录。"
            )

            recovered = client.post("/chat/messages", headers=headers, json=request)
            assert recovered.status_code == 200
            recovered_events = [
                json.loads(line[6:])
                for line in recovered.text.splitlines()
                if line.startswith("data: ")
            ]
            assert recovered_events[-1]["type"] == "done"
            assert recovered_events[-1]["server_persisted"] is True
            recovered_detail = client.get(
                f"/chat/sessions/{session_id}", headers=headers
            ).json()
            assert recovered_detail["message_count"] == 2
            recovered_assistant = recovered_detail["messages"][1]
            assert recovered_assistant["content"] == "重试后完成"
            assert recovered_assistant["usage"]["status"] == "completed"
            assert "【同一消息重试约束】" in runtime_messages[1]
            assert "结果未知的副作用须先验证" in runtime_messages[1]
            assert "读取失败" in runtime_messages[1]
            assert secret not in runtime_messages[1]
            assert recovered_assistant["parts"][0]["status"] == "done"
            assert recovered_assistant["parts"][0]["output_text"] == "读取成功"
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


@pytest.mark.anyio
async def test_synced_network_failure_recovery_binds_new_dispatch_and_replaces_pair(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.models.chat import ChatMessage, ChatSession
    from app.schemas.chat import MediaIntentDecision
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-synced-network-recovery.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "media_intent_mode", "confirm")
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
    monkeypatch.setattr(chat_api, "_start_session_title_task", lambda **_kwargs: None)
    monkeypatch.setattr(
        chat_api,
        "route_media_intent",
        lambda message, **_kwargs: MediaIntentDecision(
            decision_id="dsp-network-recovery",
            predicted_intent="chat",
            effective_route="chat",
            final_intent="chat",
            route_source="deterministic_rule",
            confidence_band="high",
            reason_code="plain_chat",
            normalized_prompt=message,
        ),
    )

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **_kwargs):
            return (
                PiRunResult(
                    reply="恢复后的正文",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 5,
                        "completion_tokens": 4,
                        "total_tokens": 9,
                    },
                    generated_files=[
                        {"name": "恢复结果.txt", "url": "/files/recovered-result.txt"}
                    ],
                ),
                object(),
            )

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            session_id = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "网络失败恢复"},
            ).json()["id"]
            user_id = "msg-network-recovery-user"
            assistant_id = "msg-network-recovery-assistant"
            synced = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": False,
                    "messages": [
                        {"id": user_id, "role": "user", "content": "继续完成任务"},
                        {
                            "id": assistant_id,
                            "role": "assistant",
                            "content": "",
                            "parts": [
                                {
                                    "type": "error",
                                    "error_id": f"chat-error:{assistant_id}",
                                    "error_kind": "runtime",
                                    "text": "网络连接失败，请重试。",
                                    "error_code": "network_unavailable",
                                    "retryable": True,
                                }
                            ],
                        },
                    ],
                },
            )
            assert synced.status_code == 200

            recovered = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "继续完成任务",
                    "local_session_id": session_id,
                    "client_message_id": user_id,
                    "client_assistant_message_id": assistant_id,
                },
            )
            assert recovered.status_code == 200
            events = [
                json.loads(line[6:])
                for line in recovered.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events[-1]["type"] == "done"
            assert events[-1]["server_persisted"] is True

            detail = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
            assert detail["message_count"] == 2
            assistant = next(
                item for item in detail["messages"] if item["id"] == assistant_id
            )
            assert "恢复后的正文" in assistant["content"]
            assert "/files/recovered-result.txt" in assistant["content"]
            assert assistant["usage"]["status"] == "completed"
            assert assistant["usage"]["total_tokens"] == 9
            assert (
                assistant["dispatch_decision"]["decision_id"] == "dsp-network-recovery"
            )
            assert not any(part.get("type") == "error" for part in assistant["parts"])

        with database.SessionLocal() as db:
            session = db.get(ChatSession, session_id)
            stored_user = db.get(ChatMessage, user_id)
            stored_assistant = db.get(ChatMessage, assistant_id)
            assert session is not None
            assert stored_user is not None
            assert stored_assistant is not None
            assert stored_user.dispatch_id == "dsp-network-recovery"
            assert stored_assistant.dispatch_id == "dsp-network-recovery"

            assert not chat_repo.replace_incomplete_assistant_message(
                db,
                session,
                {
                    "id": assistant_id,
                    "role": "assistant",
                    "content": "不能覆盖成功回答",
                    "dispatch_id": "dsp-network-recovery",
                },
                recovery_user_message_id=user_id,
            )
            assert stored_assistant.content != "不能覆盖成功回答"

            with pytest.raises(ValueError, match="does not match session"):
                chat_repo.replace_incomplete_assistant_message(
                    db,
                    SimpleNamespace(
                        id="chs-other-session",
                        owner_user_id=session.owner_user_id,
                    ),
                    {
                        "id": assistant_id,
                        "role": "assistant",
                        "content": "跨会话覆盖",
                        "dispatch_id": "dsp-network-recovery",
                    },
                    recovery_user_message_id=user_id,
                )

            with pytest.raises(ValueError, match="does not match session"):
                chat_repo.replace_incomplete_assistant_message(
                    db,
                    SimpleNamespace(id=session.id, owner_user_id="other-owner"),
                    {
                        "id": assistant_id,
                        "role": "assistant",
                        "content": "越权覆盖",
                        "dispatch_id": "dsp-network-recovery",
                    },
                    recovery_user_message_id=user_id,
                )

            stored_assistant.usage = json.dumps({"status": "failed"})
            db.flush()
            with pytest.raises(ValueError, match="dispatch does not match"):
                chat_repo.replace_incomplete_assistant_message(
                    db,
                    session,
                    {
                        "id": assistant_id,
                        "role": "assistant",
                        "content": "错误 dispatch 覆盖",
                        "dispatch_id": "dsp-other",
                    },
                    recovery_user_message_id=user_id,
                )
            db.rollback()
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


@pytest.mark.anyio
async def test_stream_disconnect_persists_partial_tool_timeline(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.core import database
    from app.models.auth import User
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-disconnect-context.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    attempts = 0
    runtime_messages: list[str] = []

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **kwargs):
            nonlocal attempts
            attempts += 1
            runtime_messages.append(_message)
            if attempts == 1:
                await kwargs["on_event"](
                    {
                        "type": "tool_execution_start",
                        "toolCallId": "call-disconnected",
                        "toolName": "write_report",
                        "args": {"path": "partial.txt"},
                    }
                )
                await asyncio.Event().wait()
            return (
                PiRunResult(
                    reply="已根据中断记录回答",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 8,
                        "completion_tokens": 4,
                        "total_tokens": 12,
                    },
                ),
                object(),
            )

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "连接中断上下文"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            with database.SessionLocal() as db:
                user = db.scalar(select(User).where(User.id == admin["user"]["id"]))
                assert user is not None
                response = await chat_api.stream_chat_message(
                    chat_api.ChatMessageRequest(
                        message="执行一个慢工具",
                        local_session_id=session_id,
                        client_message_id="msg-disconnect-user",
                        client_assistant_message_id="msg-disconnect-assistant",
                    ),
                    user,
                )

            iterator = response.body_iterator.__aiter__()
            while True:
                chunk = await iterator.__anext__()
                text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
                payload = json.loads(text.removeprefix("data: ").strip())
                if payload.get("type") == "event":
                    break
            await iterator.aclose()
            assert await chat_api.drain_background_chat_tasks(2.0) == 0

            detail = client.get(
                f"/chat/sessions/{session_id}",
                headers=headers,
            )
            assert detail.status_code == 200
            messages = detail.json()["messages"]
            assert len(messages) == 2
            assistant = messages[1]
            assert assistant["usage"]["status"] == "failed"
            assert assistant["usage"]["error_code"] == "cancelled"
            assert assistant["parts"][0]["type"] == "tool"
            assert assistant["parts"][0]["tool_name"] == "write_report"
            assert assistant["parts"][0]["status"] == "error"

            follow_up = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "刚才工具执行到哪里了？",
                    "local_session_id": session_id,
                    "client_message_id": "msg-after-disconnect-user",
                    "client_assistant_message_id": "msg-after-disconnect-assistant",
                },
            )
            assert follow_up.status_code == 200
            follow_up_events = [
                json.loads(line[6:])
                for line in follow_up.text.splitlines()
                if line.startswith("data: ")
            ]
            assert follow_up_events[-1]["type"] == "done"
            assert follow_up_events[-1]["reply"] == "已根据中断记录回答"
            assert "【尚未同步的会话执行记录】" in runtime_messages[1]
            assert "工具=write_report" in runtime_messages[1]
            assert "状态=error" in runtime_messages[1]
            assert "未收到工具最终结果" in runtime_messages[1]
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


@pytest.mark.anyio
async def test_chat_draining_rejects_generation_but_allows_message_backfill(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-draining.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    chat_api.set_chat_draining(False)
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "排空测试"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]

            chat_api.set_chat_draining(True)
            rejected = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "不应启动生成",
                    "local_session_id": session_id,
                },
            )
            assert rejected.status_code == 503
            assert rejected.json() == {
                "detail": {
                    "code": "chat_service_draining",
                    "message": "聊天服务正在排空，请稍后重试",
                }
            }
            assert rejected.headers["Retry-After"] == "5"

            backfill = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": False,
                    "messages": [
                        {
                            "id": "draining-backfill",
                            "role": "user",
                            "content": "补存即可",
                        }
                    ],
                },
            )
            assert backfill.status_code == 200
            assert backfill.json()["message_count"] == 1
    finally:
        chat_api.set_chat_draining(False)
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_server_signal_marks_chat_draining_before_uvicorn_shutdown(monkeypatch):
    from app import server

    events: list[object] = []
    monkeypatch.setattr(
        chat_api,
        "set_chat_draining",
        lambda value: events.append(("draining", value)),
    )
    monkeypatch.setattr(
        server.uvicorn.Server,
        "handle_exit",
        lambda _self, sig, _frame: events.append(("uvicorn", sig)),
    )

    instance = server.ChatDrainingServer(server.uvicorn.Config(app, lifespan="off"))
    instance.handle_exit(15, None)

    assert events == [("draining", True), ("uvicorn", 15)]


def test_server_chat_persistence_retries_only_transient_database_errors(monkeypatch):
    import sqlite3

    calls: list[int] = []
    delays: list[float] = []

    def flaky_persist(**_kwargs):
        calls.append(threading.get_ident())
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(chat_api, "_persist_server_chat_messages", flaky_persist)
    monkeypatch.setattr(chat_api.time, "sleep", delays.append)

    chat_api._persist_server_chat_messages_with_retry(
        user_id="user-retry",
        local_session_id="chs_retry_12345678",
        payloads=[{"id": "retry-user", "role": "user", "content": "重试"}],
    )

    assert len(calls) == 3
    assert delays == [
        chat_api._CHAT_PERSIST_RETRY_BASE_SECONDS,
        chat_api._CHAT_PERSIST_RETRY_BASE_SECONDS * 2,
    ]


def test_server_chat_persistence_does_not_retry_deterministic_errors(monkeypatch):
    calls: list[int] = []
    delays: list[float] = []

    def invalid_persist(**_kwargs):
        calls.append(threading.get_ident())
        raise ValueError("chat message id conflicts")

    monkeypatch.setattr(chat_api, "_persist_server_chat_messages", invalid_persist)
    monkeypatch.setattr(chat_api.time, "sleep", delays.append)

    with pytest.raises(ValueError, match="chat message id conflicts"):
        chat_api._persist_server_chat_messages_with_retry(
            user_id="user-invalid",
            local_session_id="chs_invalid_12345678",
            payloads=[{"id": "invalid-user", "role": "user", "content": "失败"}],
        )

    assert len(calls) == 1
    assert delays == []


@pytest.mark.anyio
async def test_server_chat_persistence_can_run_off_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    worker_threads: list[int] = []

    def successful_persist(**_kwargs):
        worker_threads.append(threading.get_ident())

    monkeypatch.setattr(
        chat_api,
        "_persist_server_chat_messages",
        successful_persist,
    )

    await asyncio.to_thread(
        chat_api._persist_server_chat_messages_with_retry,
        user_id="user-thread",
        local_session_id="chs_thread_12345678",
        payloads=[{"id": "thread-user", "role": "user", "content": "线程"}],
    )

    assert worker_threads
    assert all(thread_id != main_thread for thread_id in worker_threads)


@pytest.mark.anyio
async def test_stream_chat_lazy_backfills_generated_image_and_consumes_after_pi_success(
    tmp_path,
    monkeypatch,
):
    from pathlib import Path

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.core import database
    from app.core.database import transaction
    from app.core.security import now
    from app.models.auth import User
    from app.models.image_generation import ImageGenerationTask
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    db_path = Path(tmp_path) / "chat-generated-image-lazy.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    fetched_data = "bGF6eS1nZW5lcmF0ZWQtaW1hZ2U="

    async def fetch_context_image_data(image_url):
        assert image_url == "https://result.aliyuncs.com/lazy.jpg"
        return fetched_data

    route = chat_api.ChatImageRoute(
        mode="direct",
        answer_endpoint_id="vision-endpoint",
        answer_provider="provider",
        answer_model="vision-model",
    )
    received_images: list[list[dict[str, Any]]] = []

    class FakePiClient:
        effective_model = "vision-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            assert "会话生成图片" in message
            received_images.append(kwargs["images"])
            return (
                PiRunResult(
                    reply="牛头上有发光电路",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 8,
                        "completion_tokens": 5,
                        "total_tokens": 13,
                    },
                ),
                object(),
            )

    monkeypatch.setattr(
        chat_api.image_generation_service,
        "fetch_context_image_data",
        fetch_context_image_data,
    )
    monkeypatch.setattr(
        chat_api,
        "_resolve_chat_image_route",
        lambda *_args, **_kwargs: route,
    )
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)

    try:
        with TestClient(app) as client:
            payload = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {payload['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "生成图片 lazy backfill"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]

            stamp = now()
            with transaction() as db:
                user = db.scalar(
                    select(User).where(User.username == settings.bootstrap_username)
                )
                assert user is not None
                session = chat_repo.get_owned_session(db, session_id, user.id)
                assert session is not None
                chat_repo.append_messages(
                    db,
                    session,
                    [
                        {
                            "id": "msg-lazy-user",
                            "role": "user",
                            "content": "生成一张科技牛图片",
                        },
                        {
                            "id": "msg-lazy-assistant",
                            "role": "assistant",
                            "content": "图片生成任务已完成。",
                        },
                    ],
                )
                db.add(
                    ImageGenerationTask(
                        id="igt-lazy",
                        owner_user_id=user.id,
                        chat_session_id=session_id,
                        user_message_id="msg-lazy-user",
                        assistant_message_id="msg-lazy-assistant",
                        provider_task_id="provider-lazy",
                        model="kling/kling-v3-image-generation",
                        aspect_ratio="1:1",
                        resolution="1k",
                        prompt="生成一张科技牛图片",
                        status="succeeded",
                        image_url="https://result.aliyuncs.com/lazy.jpg",
                        created_at=stamp,
                        updated_at=stamp,
                        completed_at=stamp,
                    )
                )

            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "牛头上有什么科技元素？",
                    "local_session_id": session_id,
                },
            )
            assert response.status_code == 200
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events[-1]["type"] == "done"
            assert received_images == [
                [
                    {
                        "type": "image",
                        "data": fetched_data,
                        "mimeType": "image/jpeg",
                    }
                ]
            ]

            with database.SessionLocal() as db:
                task = db.get(ImageGenerationTask, "igt-lazy")
                assert task is not None
                assert task.image_data == fetched_data
                assert task.image_context_consumed_at is not None
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


@pytest.mark.anyio
async def test_stream_chat_timeout_preserves_generated_files(monkeypatch):
    from app.services.pi_runtime.runner import PiRpcTimeoutError

    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])

    class FakePiClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def chat_async(self, message, **kwargs):
            raise PiRpcTimeoutError(
                "Pi execution timed out (180s)",
                generated_files=[
                    {
                        "name": "report.docx",
                        "path": "report.docx",
                        "size": 12,
                        "url": "/api/files/pi-chat-timeout/report.docx",
                    }
                ],
            )

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)

    def fake_user():
        user = MagicMock()
        user.id = "usr-timeout"
        user.auth_version = 1
        return user

    app.dependency_overrides[chat_api.current_user] = fake_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/chat/messages",
                json={"message": "生成文件", "session_id": "pi-chat-usr-timeout"},
            )
        payloads = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert payloads[-1]["type"] == "error"
        assert payloads[-1]["error_code"] == "timeout"
        assert payloads[-1]["generated_files"][0]["name"] == "report.docx"
    finally:
        app.dependency_overrides.pop(chat_api.current_user, None)


@pytest.mark.anyio
async def test_stream_chat_soft_completes_timeout_with_generated_files(monkeypatch):
    from app.services import pi_local
    from app.services.pi_runtime.runner import PiRpcTimeoutError

    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
    files = [
        {
            "name": "report.docx",
            "path": "report.docx",
            "size": 12,
            "url": "/api/files/pi-chat-soft-timeout/report.docx",
        }
    ]
    monkeypatch.setattr(
        chat_api.LocalPiAgentClient,
        "_resolve",
        lambda self: (
            SimpleNamespace(id="agent-1"),
            SimpleNamespace(model="chat-model"),
        ),
    )
    monkeypatch.setattr(
        chat_api.LocalPiAgentClient,
        "_ensure_harness_task",
        staticmethod(lambda *_args, **_kwargs: None),
    )

    class TimeoutRunner:
        def __init__(self, runtime):
            pass

        async def run(self, **kwargs):
            raise PiRpcTimeoutError(
                "Pi execution timed out (180s)",
                generated_files=files,
            )

    monkeypatch.setattr(pi_local, "PiRpcRunner", TimeoutRunner)

    def fake_user():
        user = MagicMock()
        user.id = "usr-soft-timeout"
        user.auth_version = 1
        return user

    app.dependency_overrides[chat_api.current_user] = fake_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/chat/messages",
                json={
                    "message": "生成文件",
                    "session_id": "pi-chat-usr-soft-timeout",
                },
            )
        payloads = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert response.status_code == 200
        assert payloads[-1]["type"] == "done"
        assert "report.docx" in payloads[-1]["reply"]
        assert payloads[-1]["generated_files"] == files
    finally:
        app.dependency_overrides.pop(chat_api.current_user, None)


@pytest.mark.anyio
async def test_stream_chat_serializes_runtime_event_without_disconnect(monkeypatch):
    from app.services.pi_runtime.runner import PiRunResult

    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])

    class FakePiClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def chat_async(self, message, **kwargs):
            assert message == "流式请求"
            event = {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "delta": "流式回复",
                },
            }
            on_event = kwargs.get("on_event")
            assert on_event is not None
            await on_event(event)
            return (
                PiRunResult(
                    reply="流式回复",
                    stats={},
                    state={},
                    events=[event],
                    token_usage={
                        "prompt_tokens": 2,
                        "completion_tokens": 2,
                        "total_tokens": 4,
                    },
                ),
                object(),
            )

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)

    def fake_user():
        user = MagicMock()
        user.id = "usr-stream-event"
        user.auth_version = 1
        return user

    app.dependency_overrides[chat_api.current_user] = fake_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/chat/messages",
                json={
                    "message": "流式请求",
                    "session_id": "pi-chat-usr-stream-event",
                },
            )

        assert response.status_code == 200
        payloads = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert any(
            payload.get("type") == "event"
            and payload["item"]["type"] == "ModelClientStreamingChunkEvent"
            and payload["item"]["content"] == "流式回复"
            for payload in payloads
        )
        assert payloads[-1]["type"] == "done"
        assert payloads[-1]["reply"] == "流式回复"
    finally:
        app.dependency_overrides.pop(chat_api.current_user, None)


@pytest.mark.anyio
async def test_stream_chat_rejects_another_users_runtime_session(monkeypatch):
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")

    def fake_user():
        return SimpleNamespace(id="user-2")

    app.dependency_overrides[chat_api.current_user] = fake_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/chat/messages",
                json={"message": "偷看", "session_id": "pi-chat-user-1"},
            )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(chat_api.current_user, None)


@pytest.mark.anyio
@pytest.mark.parametrize("resolver_error", [None, ValueError("route parse failed")])
async def test_control_without_images_fails_stably_when_answer_route_is_missing(
    monkeypatch, resolver_error
):
    session = SimpleNamespace(
        agent_session_id=chat_api._runtime_session_id("user-1", "chs-missing-route"),
        preferred_model="answer-model",
    )
    persisted: list[dict[str, Any]] = []

    class _Transaction:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    def resolve_missing(*_args, **_kwargs):
        if resolver_error is not None:
            raise resolver_error
        return None

    monkeypatch.setattr(chat_api, "transaction", lambda: _Transaction())
    monkeypatch.setitem(
        chat_api._ACTIVE_CHAT_CONTEXTS,
        ("user-1", "chs-missing-route"),
        [
            {"writing_context": None, "space_ids": [], "document_ids": []},
        ],
    )
    monkeypatch.setattr(chat_api, "resolve_model_endpoint", resolve_missing)
    monkeypatch.setattr(chat_api.chat_repo, "get_owned_session", lambda *_args: session)
    monkeypatch.setattr(
        chat_api,
        "_record_control_operation",
        lambda **kwargs: persisted.append(kwargs),
    )
    monkeypatch.setattr(
        chat_api,
        "LocalPiAgentClient",
        lambda **_kwargs: pytest.fail("missing route must fail before dispatch"),
    )

    with pytest.raises(HTTPException) as captured:
        await chat_api._proxy_steer_or_follow_up(
            session_id="chs-missing-route",
            payload=chat_api.ChatSteerRequest(message="请继续"),
            user=SimpleNamespace(id="user-1"),
            mode="steer",
        )

    assert captured.value.status_code == 503
    assert captured.value.detail == {
        "error_code": "chat_endpoint_unavailable",
        "message": "当前未配置可用的对话模型端点，请先完成模型配置。",
    }
    assert len(persisted) == 1
    assert persisted[0]["usage"]["route_mode"] == "direct"
    assert persisted[0]["usage"]["calls"] == []


def test_server_marks_chat_draining_before_uvicorn_shutdown(monkeypatch):
    from app import server

    events: list[object] = []
    monkeypatch.setattr(
        chat_api,
        "set_chat_draining",
        lambda draining: events.append(("draining", draining)),
    )
    monkeypatch.setattr(
        server.uvicorn.Server,
        "handle_exit",
        lambda _server, sig, _frame: events.append(("uvicorn", sig)),
    )

    instance = object.__new__(server.ChatDrainingServer)
    instance.handle_exit(15, None)

    assert events == [("draining", True), ("uvicorn", 15)]


@pytest.mark.anyio
async def test_tool_approval_returns_accepted_after_runtime_dispatch(monkeypatch):
    session = SimpleNamespace(
        agent_session_id=chat_api._runtime_session_id("user-1", "chs-approval"),
        preferred_model=None,
    )

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    class FakePiClient:
        def __init__(self, **_kwargs):
            pass

        async def respond_to_extension_ui(self, **kwargs):
            assert kwargs == {
                "session_id": chat_api._runtime_session_id("user-1", "chs-approval"),
                "request_id": "approval-1",
                "confirmed": True,
            }

    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(chat_api.chat_repo, "get_owned_session", lambda *_args: session)
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)

    response = await chat_api.respond_to_tool_approval(
        "chs-approval",
        chat_api.ChatToolApprovalRequest(request_id="approval-1", confirmed=True),
        SimpleNamespace(id="user-1"),
    )

    assert response == {"accepted": True}


@pytest.mark.anyio
async def test_tool_approval_failure_never_returns_runtime_detail(monkeypatch):
    from app.services.pi_agent_client import PiAgentError

    session = SimpleNamespace(
        agent_session_id=chat_api._runtime_session_id("user-1", "chs-approval"),
        preferred_model=None,
    )

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    class FailingPiClient:
        def __init__(self, **_kwargs):
            pass

        async def respond_to_extension_ui(self, **_kwargs):
            raise PiAgentError(
                "provider raw api_key=approval-secret http://pi.internal"
            )

    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _SessionContext())
    monkeypatch.setattr(chat_api.chat_repo, "get_owned_session", lambda *_args: session)
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FailingPiClient)

    with pytest.raises(HTTPException) as captured:
        await chat_api.respond_to_tool_approval(
            "chs-approval",
            chat_api.ChatToolApprovalRequest(
                request_id="approval-1",
                confirmed=True,
            ),
            SimpleNamespace(id="user-1"),
        )

    assert captured.value.status_code == 409
    assert captured.value.detail == "工具审批未能提交，请稍后重试。"
    assert "approval-secret" not in str(captured.value.detail)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["steer", "follow_up"])
async def test_control_requests_rejected_during_chat_drain(monkeypatch, mode):
    monkeypatch.setattr(chat_api, "_CHAT_DRAINING", True)

    with pytest.raises(HTTPException) as captured:
        await chat_api._proxy_steer_or_follow_up(
            session_id="chs-draining",
            payload=chat_api.ChatSteerRequest(message="请继续"),
            user=SimpleNamespace(id="user-1"),
            mode=mode,
        )

    assert captured.value.status_code == 503
    assert captured.value.detail["code"] == "chat_service_draining"


@pytest.mark.anyio
async def test_stream_first_done_carries_summarized_session_title(
    tmp_path, monkeypatch
):
    """First completed turn persists a one-shot LLM title and surfaces it in done."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.core import database
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = Path(tmp_path) / "chat-title.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")

    summarize_calls = []

    def fake_summarize(*, user_id, messages, model=None):
        summarize_calls.append((user_id, len(messages), model))
        return "AI 总结的会话标题"

    monkeypatch.setattr(chat_api, "summarize_session_title", fake_summarize)

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def chat_async(self, message, **kwargs):
            return (
                PiRunResult(
                    reply="服务端已保存",
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
    try:
        with TestClient(app) as client:
            payload = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {payload['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "新对话"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]

            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "帮我梳理一下预算方案",
                    "local_session_id": session_id,
                    "client_message_id": "msg-title-user",
                    "client_assistant_message_id": "msg-title-assistant",
                },
            )
            assert response.status_code == 200
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            done = events[-1]
            assert done["type"] == "done"
            assert done["server_persisted"] is True
            # The title comes from the LLM summary, not the first user message.
            assert done["session_title"] == "AI 总结的会话标题"
            assert done["session_title_pending"] is False
            assert "帮我梳理" not in done["session_title"]

            detail = client.get(f"/chat/sessions/{session_id}", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["title"] == "AI 总结的会话标题"
            # Browser snapshot writes must not overwrite the frozen summary.
            partial = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "messages": [
                        {
                            "id": "msg-title-user",
                            "role": "user",
                            "content": "帮我梳理一下预算方案",
                        },
                        {
                            "id": "msg-title-assistant",
                            "role": "assistant",
                            "content": "服务端已保存",
                        },
                    ],
                },
            )
            assert partial.status_code == 200
            assert partial.json()["title"] == "AI 总结的会话标题"
            assert summarize_calls == [(payload["user"]["id"], 2, None)]
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(Path(tmp_path) / "chat-title.sqlite3")


@pytest.mark.anyio
async def test_stream_done_not_stalled_by_slow_title_summarizer(tmp_path, monkeypatch):
    """A slow title endpoint must not hold up the SSE done beyond the wait cap.

    The one-shot title summarizer runs in a worker thread and the stream only
    waits ``_CHAT_TITLE_DONE_WAIT_SECONDS`` for it before emitting ``done``;
    the remaining work keeps running in the background and persists later.
    """
    import time
    from pathlib import Path
    from threading import Event

    from fastapi.testclient import TestClient

    from app.core import database
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = Path(tmp_path) / "chat-title-slow.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")

    # Keep the summarizer blocked beyond the done wait, then release it so
    # background persistence can be observed without a multi-second test.
    monkeypatch.setattr(chat_api, "_CHAT_TITLE_DONE_WAIT_SECONDS", 0.05)
    release_title = Event()

    def slow_summarize(*, user_id, messages, model=None):
        release_title.wait(timeout=2.0)
        return "迟到的标题"

    monkeypatch.setattr(chat_api, "summarize_session_title", slow_summarize)

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def chat_async(self, message, **kwargs):
            return (
                PiRunResult(
                    reply="服务端已保存",
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
    try:
        with TestClient(app) as client:
            payload = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {payload['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "新对话"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]

            started = time.monotonic()
            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "帮我梳理一下预算方案",
                    "local_session_id": session_id,
                    "client_message_id": "msg-title-slow-user",
                    "client_assistant_message_id": "msg-title-slow-assistant",
                },
            )
            elapsed = time.monotonic() - started
            assert response.status_code == 200
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            done = events[-1]
            assert done["type"] == "done"
            assert done["server_persisted"] is True
            # A pending title must not delay done until the worker is released.
            assert done.get("session_title") is None
            assert done["session_title_pending"] is True
            assert elapsed < 1.0, f"done was stalled by slow title: {elapsed:.2f}s"
            release_title.set()
            deadline = time.monotonic() + 2.0
            while True:
                detail = client.get(
                    f"/chat/sessions/{session_id}",
                    headers=headers,
                )
                assert detail.status_code == 200
                if detail.json()["title"] == "迟到的标题":
                    break
                if time.monotonic() >= deadline:
                    pytest.fail("background title was not persisted")
                time.sleep(0.01)
    finally:
        release_title.set()
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(Path(tmp_path) / "chat-title-slow.sqlite3")


@pytest.mark.anyio
async def test_token_budget_stop_preserves_partial_reply_and_reported_usage(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from app.core import database
    from app.services import pi_local
    from app.services.pi_agent_client import PiAgentError
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    db_path = tmp_path / "chat-token-budget.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")

    class BudgetStoppedClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **kwargs):
            context_tokens_seen.append(int(kwargs.get("context_tokens") or 0))
            if len(context_tokens_seen) > 1:
                return (
                    PiRunResult(
                        reply="继续完成。",
                        stats={"contextUsage": {"tokens": 120_000}},
                        state={},
                        events=[],
                        token_usage={
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                        },
                    ),
                    object(),
                )
            for kind, text in (
                ("thinking_delta", "已经完成资料核对。"),
                ("text_delta", "第一部分：已经完成的分析。"),
            ):
                await kwargs["on_event"](
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {"type": kind, "delta": text},
                    }
                )
            error = PiAgentError("[chat_token_budget_exceeded] budget exhausted")
            error.code = "chat_token_budget_exceeded"
            error.generated_files = [
                {"name": "partial.txt", "url": "/api/files/budget-task/partial.txt"}
            ]
            error.token_usage = {
                "prompt_tokens": 480_000,
                "completion_tokens": 20_000,
                "total_tokens": 500_000,
                "cache_read_tokens": 80_000,
                "cache_write_tokens": 0,
                "input_uncached_tokens": 400_000,
            }
            # Pi measured the context before the ledger stopped the turn; the
            # next turn must budget off it instead of the flat baseline.
            error.context_usage = {"tokens": 120_000, "contextWindow": 200_000}
            raise error

    context_tokens_seen: list[int] = []

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", BudgetStoppedClient)
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "预算停止"}
            )
            session_id = created.json()["id"]
            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "请继续分析",
                    "local_session_id": session_id,
                    "client_message_id": "budget-user",
                    "client_assistant_message_id": "budget-assistant",
                },
            )
            assert response.status_code == 200
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events[-1]["type"] == "error"
            assert events[-1]["error_code"] == "chat_token_budget_exceeded"
            assert events[-1]["retryable"] is False
            assert events[-1]["server_persisted"] is True
            detail = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
            assert events[-1]["messages_revision"] == detail["messages_revision"]
            stored = detail["messages"][1]
            assert stored["thinking"] == "已经完成资料核对。"
            assert stored["content"].startswith("第一部分：已经完成的分析。")
            assert "/api/files/budget-task/partial.txt" in stored["content"]
            assert any(
                part.get("type") == "content"
                and "/api/files/budget-task/partial.txt" in part.get("text", "")
                for part in stored["parts"]
            )
            assert any(
                part.get("type") == "content"
                and "第一部分：已经完成的分析。" in part.get("text", "")
                for part in stored["parts"]
            )
            errors = [part for part in stored["parts"] if part.get("type") == "error"]
            assert len(errors) == 1
            assert errors[0]["error_id"] == "chat-error:budget-assistant"
            assert stored["usage"]["status"] == "failed"
            assert stored["usage"]["total_tokens"] == 500_000
            assert stored["usage"]["input_tokens"] == 480_000
            assert stored["usage"]["output_tokens"] == 20_000
            assert stored["usage"]["cache_read_tokens"] == 80_000
            assert stored["usage"]["input_uncached_tokens"] == 400_000
            assert stored["usage"]["context_input_tokens"] == 120_000
            assert stored["usage"]["context_window"] == 200_000

            continuation = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "继续完成",
                    "local_session_id": session_id,
                    "client_message_id": "budget-user-2",
                    "client_assistant_message_id": "budget-assistant-2",
                },
            )
            assert continuation.status_code == 200
            # 「在当前会话继续」按停止回合量到的上下文开额度，而不是回落基线。
            assert context_tokens_seen == [0, 120_000]
            assert (
                pi_local.chat_turn_token_warning_threshold(context_tokens_seen[1])
                > pi_local.CHAT_TURN_WARNING_BASE_TOKENS
            )
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


@pytest.mark.anyio
async def test_output_limit_preserves_partial_text_tool_and_usage_without_retry(
    tmp_path, monkeypatch
):
    """length 是受控失败：保留进度，但不把已执行工具透明重跑。"""
    from fastapi.testclient import TestClient

    from app.core import database
    from app.services.pi_agent_client import PiAgentError
    from app.services.pi_runtime.runner import (
        PI_OUTPUT_LIMIT_ERROR_CODE,
        PI_OUTPUT_LIMIT_PUBLIC_MESSAGE,
    )

    original = settings.db_path
    original_url = settings.database_url
    db_path = tmp_path / "chat-output-limit.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    attempts = 0

    class OutputLimitedClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **kwargs):
            nonlocal attempts
            attempts += 1
            for event in (
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "thinking_delta",
                        "delta": "正在整理长页面。",
                    },
                },
                {
                    "type": "tool_execution_start",
                    "toolCallId": "call-bbc",
                    "toolName": "read",
                    "args": {"path": "https://www.bbc.com/"},
                },
                {
                    "type": "tool_execution_end",
                    "toolCallId": "call-bbc",
                    "toolName": "read",
                    "isError": False,
                    "result": {"content": [{"type": "text", "text": "BBC 页面已读取"}]},
                },
                {
                    "type": "message_update",
                    "assistantMessageEvent": {
                        "type": "text_delta",
                        "delta": "已生成的",
                    },
                },
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "stopReason": "length",
                        "responseId": "resp-output-limit",
                    },
                },
            ):
                await kwargs["on_event"](event)
            error = PiAgentError(
                PI_OUTPUT_LIMIT_PUBLIC_MESSAGE,
                code=PI_OUTPUT_LIMIT_ERROR_CODE,
                max_attempts=1,
            )
            error.__dict__["partial_reply"] = "已生成的正文。"
            error.__dict__["token_usage"] = {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            }
            raise error

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", OutputLimitedClient)
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "输出截断"}
            )
            session_id = created.json()["id"]

            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "读取 BBC 后总结",
                    "local_session_id": session_id,
                    "client_message_id": "output-limit-user",
                    "client_assistant_message_id": "output-limit-assistant",
                },
            )

            assert response.status_code == 200
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert attempts == 1
            assert events[-1]["type"] == "error"
            assert events[-1]["error_code"] == PI_OUTPUT_LIMIT_ERROR_CODE
            # retryable 只表示允许同会话继续，不代表自动续跑或重跑已执行工具；
            # 上下文接近上限时，Pi 可能先压缩再继续。
            assert events[-1]["retryable"] is True
            assert events[-1]["server_persisted"] is True
            assert not any(event.get("type") == "done" for event in events)

            detail = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
            stored = detail["messages"][1]
            assert stored["content"] == "已生成的正文。"
            assert stored["thinking"] == "正在整理长页面。"
            assert stored["usage"]["status"] == "failed"
            assert stored["usage"]["error_code"] == PI_OUTPUT_LIMIT_ERROR_CODE
            assert stored["usage"]["input_tokens"] == 120
            assert stored["usage"]["output_tokens"] == 80
            assert stored["usage"]["total_tokens"] == 200
            tool_part = next(
                part for part in stored["parts"] if part.get("type") == "tool"
            )
            assert tool_part["status"] == "done"
            assert tool_part["output_text"] == "BBC 页面已读取"
            error_part = next(
                part for part in stored["parts"] if part.get("type") == "error"
            )
            assert error_part["error_code"] == PI_OUTPUT_LIMIT_ERROR_CODE
            assert error_part["retryable"] is True
            assert error_part["progress_preserved"] is True
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


@pytest.mark.anyio
@pytest.mark.parametrize("protocol_failure", [False, True])
async def test_content_filtered_stream_preserves_partial_reply_without_retry(
    tmp_path, monkeypatch, protocol_failure
):
    from fastapi.testclient import TestClient

    from app.core import database
    from app.services.pi_agent_client import PiAgentError
    from app.services.pi_runtime.runner import PiRpcProtocolError

    original = settings.db_path
    original_url = settings.database_url
    db_path = tmp_path / "chat-content-filtered.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(chat_api, "_start_session_title_task", lambda **_kwargs: None)
    attempts = 0
    partial = "已读取页面。\n尚未确认的数据"
    provider_message = (
        "Output data may contain inappropriate content. "
        "https://help.aliyun.com/zh/model-studio/error-code"
    )

    class FilteredClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **kwargs):
            nonlocal attempts
            attempts += 1
            rejected = {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": provider_message,
                    "responseId": "filtered-response",
                },
            }
            for event in (
                {
                    "type": "message_update",
                    "assistantMessageEvent": {"type": "text_delta", "delta": partial},
                },
                rejected,
                rejected,
                {"type": "error", "code": "protocol_error"},
            ):
                await kwargs["on_event"](event)
            if protocol_failure:
                raise PiRpcProtocolError("Pi RPC exited before agent_settled")
            raise PiAgentError(provider_message)

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FilteredClient)
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            created = client.post(
                "/chat/sessions", headers=headers, json={"title": "内容安全停止"}
            )
            session_id = created.json()["id"]
            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "读取页面后总结",
                    "local_session_id": session_id,
                    "client_message_id": "filtered-user",
                    "client_assistant_message_id": "filtered-assistant",
                },
            )
            assert response.status_code == 200
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert attempts == 1
            assert events[-1]["type"] == "error"
            assert events[-1]["error_code"] == "content_filtered"
            assert events[-1]["retryable"] is False
            assert events[-1]["server_persisted"] is True
            items = [event["item"] for event in events if event["type"] == "event"]
            chunks = [
                item["content"]
                for item in items
                if item["type"] == "ModelClientStreamingChunkEvent"
            ]
            assert "".join(chunks) == partial
            notices = [item for item in items if item["type"] == "PiRuntimeEvent"]
            assert len(notices) == 1
            assert notices[0]["content"]["errorCode"] == "content_filtered"
            assert notices[0]["content"]["retryable"] is False
            assert items[-1] == notices[0]
            assert not any(event["type"] == "done" for event in events)
            stored = client.get(
                f"/chat/sessions/{session_id}", headers=headers
            ).json()["messages"][1]
            assert stored["content"] == partial
            assert stored["usage"]["error_code"] == "content_filtered"
            errors = [part for part in stored["parts"] if part["type"] == "error"]
            assert len(errors) == 1
            assert errors[0]["error_code"] == "content_filtered"
            assert errors[0]["retryable"] is False
            assert provider_message not in json.dumps(stored, ensure_ascii=False)
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw_usage,error_code,expects_warning",
    [
        (None, "chat_token_usage_unavailable", False),
        ({"prompt_tokens": 8}, "chat_token_usage_unavailable", False),
        ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, None, False),
        (
            {
                "prompt_tokens": 500_000,
                "completion_tokens": 1,
                "total_tokens": 500_001,
            },
            None,
            True,
        ),
    ],
)
async def test_classifier_usage_must_be_reported_before_answer(
    tmp_path, monkeypatch, raw_usage, error_code, expects_warning
):
    import httpx
    from fastapi.testclient import TestClient

    from app.core import database
    from app.services import qa_service
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    db_path = tmp_path / "classifier-usage-budget.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "media_intent_mode", "confirm")
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(chat_api, "_start_session_title_task", lambda **_kwargs: None)
    endpoint = SimpleNamespace(
        id="classifier-budget",
        provider="controlled",
        model="classifier-model",
        adapter="openai_compatible",
        api_protocol="chat_completions",
        endpoint_url="http://classifier.test/v1",
        secret_ref=None,
    )
    original_route = chat_api.route_media_intent
    monkeypatch.setattr(
        chat_api,
        "route_media_intent",
        lambda *args, **kwargs: original_route(
            *args, **kwargs, classifier_endpoint=endpoint
        ),
    )
    classifier_calls = 0
    answer_calls = 0

    def classifier_response(url, **_kwargs):
        nonlocal classifier_calls
        classifier_calls += 1
        body = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "predicted_intent": "chat",
                                "confidence_band": "high",
                                "reason_code": "classifier_chat",
                            }
                        )
                    }
                }
            ],
        }
        if raw_usage is not None:
            body["usage"] = raw_usage
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    class AnswerClient:
        effective_model = "answer-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **_kwargs):
            nonlocal answer_calls
            answer_calls += 1
            return PiRunResult("预算可确认后才回答", {}, {}, [], {}), object()

    monkeypatch.setattr(qa_service.httpx, "post", classifier_response)
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", AnswerClient)
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            session_id = client.post(
                "/chat/sessions", headers=headers, json={"title": "分类用量"}
            ).json()["id"]
            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "图片",
                    "local_session_id": session_id,
                    "client_message_id": "classifier-budget-user",
                    "client_assistant_message_id": "classifier-budget-assistant",
                },
            )
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert classifier_calls == 1
            assert answer_calls == int(error_code is None)
            stored = client.get(f"/chat/sessions/{session_id}", headers=headers).json()[
                "messages"
            ][-1]
            if error_code is None:
                assert events[-1]["type"] == "done"
                assert stored["content"] == "预算可确认后才回答"
                assert stored["usage"].get("token_warning", False) is expects_warning
                assert (
                    events[-1]["usage"].get("token_warning", False) is expects_warning
                )
                if expects_warning:
                    assert stored["usage"]["token_warning_threshold"] == 500_000
                    assert stored["usage"]["token_warning_observed_tokens"] == 500_001
            else:
                assert events[-1]["error_code"] == error_code
                assert stored["usage"]["error_code"] == error_code
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_preprocessing_usage_warns_without_blocking_the_answer():
    """用量必须可核验，但超过建议值不能阻断回答。"""
    from app.services.pi_runtime.runner import PiRpcError

    consume = chat_api._consume_chat_preprocessing_usage
    # 证明字段上线前的历史记录重放时不扣额。
    assert consume(7, {"input_tokens": 900, "output_tokens": 10}) == 7
    assert chat_api._chat_preprocessing_reported_tokens({"total_tokens": 900}) == 0
    # 模型完成调用却没有上报用量时，仍按数据完整性错误停止。
    with pytest.raises(PiRpcError) as caught:
        consume(0, {"reported_total_tokens": None})
    assert caught.value.code == "chat_token_usage_unavailable"
    assert consume(5, {"reported_total_tokens": 0}) == 5
    assert consume(5, {"reported_total_tokens": 20}) == 25
    assert consume(1, {"reported_total_tokens": 500_000}) == 500_001


@pytest.mark.anyio
async def test_chat_async_keeps_the_stop_snapshot_through_error_wrapping(
    monkeypatch,
):
    """包装成 PiAgentError 时不能丢掉 context_usage，否则下一轮预算回落基线。"""
    from app.services import pi_local
    from app.services.pi_agent_client import PiAgentError
    from app.services.pi_runtime.runner import PiRpcError

    class StoppedRunner:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, **_kwargs):
            error = PiRpcError("budget exhausted", code="chat_token_budget_exceeded")
            error.context_usage = {"tokens": 120_000, "contextWindow": 200_000}
            raise error

    client = pi_local.LocalPiAgentClient(agent_id="pi-runtime", user_id="user-1")
    monkeypatch.setattr(pi_local, "PiRpcRunner", StoppedRunner)
    monkeypatch.setattr(
        client, "_resolve", lambda: (SimpleNamespace(tenant_id="_system"), object())
    )
    monkeypatch.setattr(client, "_ensure_harness_task", lambda *_a, **_k: None)

    with pytest.raises(PiAgentError) as caught:
        await client.chat_async(
            "继续处理",
            session_id="s-1",
            streamed_turn=True,
            context_tokens=120_000,
        )

    assert caught.value.code == "chat_token_budget_exceeded"
    assert caught.value.context_usage == {"tokens": 120_000, "contextWindow": 200_000}


@pytest.mark.anyio
async def test_streamed_turn_tracks_usage_without_a_token_hard_cap(
    monkeypatch,
):
    """Warnings retain proxy metering; only streamed Chat drops the wall clock."""
    from app.services import pi_local
    from app.services.pi_runtime.runner import PiRunResult

    captured: list[dict] = []

    class RecordingRunner:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, **kwargs):
            captured.append(kwargs)
            return PiRunResult("ok", {}, {}, [], {})

    client = pi_local.LocalPiAgentClient(agent_id="pi-runtime", user_id="user-1")
    monkeypatch.setattr(pi_local, "PiRpcRunner", RecordingRunner)
    monkeypatch.setattr(
        client, "_resolve", lambda: (SimpleNamespace(tenant_id="_system"), object())
    )
    monkeypatch.setattr(client, "_ensure_harness_task", lambda *_a, **_k: None)

    await client.chat_async("hi", session_id="s-1")
    assert captured[-1]["enforce_wall_clock_timeout"] is True
    assert captured[-1]["track_model_usage"] is False
    assert captured[-1]["max_tokens"] is None
    assert captured[-1]["initial_tokens"] == 0

    await client.chat_async("hi", session_id="s-1", streamed_turn=True)
    assert captured[-1]["enforce_wall_clock_timeout"] is False
    assert captured[-1]["track_model_usage"] is True
    assert captured[-1]["max_tokens"] is None
    assert captured[-1]["initial_tokens"] == 0

    client.capability = "writing"
    await client.chat_async(
        "write",
        session_id="writing-1",
        token_warning_threshold=3_000_000,
    )
    assert captured[-1]["enforce_wall_clock_timeout"] is True
    assert captured[-1]["track_model_usage"] is True
    assert captured[-1]["max_tokens"] is None
    assert captured[-1]["initial_tokens"] == 0


def test_session_boundary_threshold_scales_with_the_model_window():
    """阈值按窗口占比决定，绝对下限兜住小窗口模型。"""
    threshold = chat_api._chat_session_boundary_threshold

    # 窗口未知或非法时退回默认窗口，不是直接放行也不是直接触发。
    assert threshold(None) == threshold(chat_api._CHAT_SESSION_BOUNDARY_DEFAULT_WINDOW)
    assert threshold(0) == threshold(None)

    # 同一个绝对值在不同窗口下含义不同：200k 窗口的一半是 110k。
    assert threshold(200_000) == 110_000
    assert threshold(1_000_000) > threshold(200_000) > threshold(None)

    # 小窗口模型即使占比很高，绝对量也不值得劝用户另开会话。
    assert threshold(32_000) == chat_api._CHAT_SESSION_BOUNDARY_MIN_TOKENS
    assert threshold(8_000) == chat_api._CHAT_SESSION_BOUNDARY_MIN_TOKENS


def test_session_context_snapshot_reads_the_newest_completed_turn():
    """tokens 与 window 必须取自同一条消息，失败回合跳过，缺失即未知。"""
    snapshot = chat_api._chat_session_context_snapshot

    session = SimpleNamespace(
        messages=[
            SimpleNamespace(
                role="assistant",
                usage=json.dumps(
                    {
                        "status": "completed",
                        "context_input_tokens": 30_000,
                        "context_window": 128_000,
                    }
                ),
            ),
            SimpleNamespace(role="user", content="继续"),
            SimpleNamespace(
                role="assistant",
                usage=json.dumps(
                    {
                        "status": "completed",
                        "context_input_tokens": 90_000,
                        "context_window": 200_000,
                    }
                ),
            ),
            # 失败回合不代表当前上下文，跳过继续往前找。
            SimpleNamespace(
                role="assistant",
                usage=json.dumps({"status": "failed"}),
            ),
        ],
    )
    assert snapshot(session) == (90_000, 200_000)

    # 最近一条 completed assistant 没有快照时保守视为未知。
    assert snapshot(
        SimpleNamespace(
            messages=[
                SimpleNamespace(
                    role="assistant",
                    usage=json.dumps({"status": "completed"}),
                )
            ]
        )
    ) == (0, None)

    # 没有 messages 关系的会话对象不能让读取炸掉。
    assert snapshot(SimpleNamespace()) == (0, None)


def test_session_context_snapshot_reuses_a_stopped_turn_for_the_turn_budget():
    """边界建议只认 completed，单轮预算必须复用停止回合量到的真实上下文。"""
    snapshot = chat_api._chat_session_context_snapshot

    session = SimpleNamespace(
        messages=[
            SimpleNamespace(
                role="assistant",
                usage=json.dumps(
                    {
                        "status": "completed",
                        "context_input_tokens": 40_000,
                        "context_window": 128_000,
                    }
                ),
            ),
            SimpleNamespace(
                role="assistant",
                usage=json.dumps(
                    {
                        "status": "failed",
                        "error_code": "chat_token_budget_exceeded",
                        "context_input_tokens": 120_000,
                        "context_window": 128_000,
                    }
                ),
            ),
        ],
    )

    # 边界建议：仍然是最近一条 completed assistant。
    assert snapshot(session) == (40_000, 128_000)
    # 单轮预算：停止回合的 120k 覆盖更早的 40k，不掉回预算下限。
    assert snapshot(session, include_failed=True) == (120_000, 128_000)

    # 失败回合没有快照时不假装知道。
    assert snapshot(
        SimpleNamespace(
            messages=[
                SimpleNamespace(
                    role="assistant", usage=json.dumps({"status": "failed"})
                )
            ]
        ),
        include_failed=True,
    ) == (0, None)

    # 停止回合没留下快照时继续往前找最近的已知测量。
    assert snapshot(
        SimpleNamespace(
            messages=[
                SimpleNamespace(
                    role="assistant",
                    usage=json.dumps(
                        {
                            "status": "completed",
                            "context_input_tokens": 40_000,
                            "context_window": 128_000,
                        }
                    ),
                ),
                SimpleNamespace(
                    role="assistant",
                    usage=json.dumps({"status": "failed"}),
                ),
            ]
        ),
        include_failed=True,
    ) == (40_000, 128_000)


def test_stopped_turn_usage_keeps_the_context_snapshot_for_the_next_budget():
    """停止回合的 usage 必须留下真实上下文，否则继续一次又撞上限。"""
    from app.services import pi_local

    error = PiRpcError("budget exhausted", code="chat_token_budget_exceeded")
    error.context_usage = {"tokens": 120_000, "contextWindow": 200_000}
    usage = chat_api._chat_failure_usage(
        route=SimpleNamespace(
            mode="direct",
            answer_provider="pi",
            answer_model="test-model",
            answer_endpoint_id=None,
            vision_provider=None,
            vision_model=None,
            vision_endpoint_id=None,
        ),
        started_at=time.monotonic(),
        vision_result=None,
        error=error,
        requested_model="test-model",
    )

    assert usage["status"] == "failed"
    assert usage["context_input_tokens"] == 120_000
    assert usage["context_window"] == 200_000

    session = SimpleNamespace(
        messages=[SimpleNamespace(role="assistant", usage=json.dumps(usage))]
    )
    context_tokens = chat_api._chat_session_context_snapshot(
        session, include_failed=True
    )[0]
    assert context_tokens == 120_000
    assert pi_local.chat_turn_token_warning_threshold(context_tokens) == 840_000


def test_chat_turn_warning_threshold_scales_with_context_and_has_a_ceiling():
    """提醒阈值随上下文缩放；达到阈值后仍继续执行。"""
    from app.services import pi_local

    budget = pi_local.chat_turn_token_warning_threshold

    # 上下文未知或很小时不低于原有基线。
    assert budget(0) == pi_local.CHAT_TURN_WARNING_BASE_TOKENS
    assert budget(-5) == pi_local.CHAT_TURN_WARNING_BASE_TOKENS
    assert budget(1_000) == pi_local.CHAT_TURN_WARNING_BASE_TOKENS

    # 长会话按“上下文 × 期望调用轮数”拿到更大的额度。
    assert budget(120_000) == 120_000 * pi_local.CHAT_TURN_EXPECTED_CALLS
    assert budget(120_000) > pi_local.CHAT_TURN_WARNING_BASE_TOKENS

    # 只限制提醒阈值，不是执行硬上限。
    assert budget(10_000_000) == pi_local.CHAT_TURN_WARNING_CEILING_TOKENS

    # 只有服务端固定的显式生成 Skill 扩展到 300 万；普通聊天不变。
    assert (
        budget(0, selected_skill="dashi-ppt")
        == pi_local.GENERATION_CHAT_TURN_WARNING_BASE_TOKENS
    )
    assert budget(120_000, selected_skill="docx") == (
        120_000 * pi_local.GENERATION_CHAT_TURN_EXPECTED_CALLS
    )
    assert budget(10_000_000, selected_skill="writing-report") == (
        pi_local.GENERATION_CHAT_TURN_WARNING_CEILING_TOKENS
    )
    assert budget(10_000_000, selected_skill="china-weather") == (
        pi_local.CHAT_TURN_WARNING_CEILING_TOKENS
    )
    assert budget(10_000_000, selected_skill="DASHI-PPT") == (
        pi_local.CHAT_TURN_WARNING_CEILING_TOKENS
    )


def test_chat_request_binds_selected_skill_to_the_first_command_line():
    from app.schemas.chat import ChatMessageRequest

    request = ChatMessageRequest(
        message="/skill:dashi-ppt\n\n生成年度汇报",
        selected_skill="dashi-ppt",
    )
    assert request.selected_skill == "dashi-ppt"

    with pytest.raises(ValueError, match="selected_skill must match"):
        ChatMessageRequest(
            message="请使用 /skill:dashi-ppt 生成年度汇报",
            selected_skill="dashi-ppt",
        )
    with pytest.raises(ValueError, match="selected_skill must match"):
        ChatMessageRequest(
            message=" /skill:dashi-ppt\n\n生成年度汇报",
            selected_skill="dashi-ppt",
        )
    with pytest.raises(ValueError, match="selected_skill must match"):
        ChatMessageRequest(
            message="",
            images=[{"data": "AA==", "mimeType": "image/png"}],
            selected_skill="dashi-ppt",
        )


def test_session_boundary_unknown_latest_input_stays_in_current_session():
    from app.schemas.chat import ChatSessionBoundaryCheckRequest

    session = SimpleNamespace(
        title="人物抠图任务",
        writing_project_id=None,
        messages=[
            SimpleNamespace(
                role="assistant",
                usage=json.dumps(
                    {
                        "status": "completed",
                        "input_tokens": 90_000,
                        "context_input_tokens": 90_000,
                    }
                ),
            ),
            SimpleNamespace(role="user", content="压缩上下文后继续"),
            SimpleNamespace(
                role="assistant",
                usage=json.dumps({"status": "completed"}),
            ),
        ],
    )
    recommendation = chat_api._chat_session_boundary_recommendation(
        session,
        ChatSessionBoundaryCheckRequest(
            message="请撰写 AIMPLOYEE 企业盒子产品介绍",
            client_message_id="boundary-unknown-user",
            client_assistant_message_id="boundary-unknown-assistant",
            model="qwen3.7-plus",
        ),
    )

    assert recommendation.recommendation == "stay"
    assert recommendation.reason_code == "below_usage_threshold"
    assert recommendation.session_input_tokens == 0


def test_session_boundary_decision_is_durable_idempotent_and_plain_chat(
    tmp_path,
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from app.core import database
    from app.models.chat import ChatDispatchDecision
    from app.schemas.chat import ChatMessageRequest
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    db_path = tmp_path / "chat-session-boundary.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    runtime_calls = 0

    class FakePiClient:
        effective_model = "qwen3.7-plus"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **_kwargs):
            nonlocal runtime_calls
            runtime_calls += 1
            return (
                PiRunResult(
                    reply="AIMPLOYEE 产品介绍已完成。",
                    stats={"contextUsage": {"tokens": 42_000}},
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

    def unexpected_media_route(*_args, **_kwargs):
        pytest.fail("boundary-confirmed plain chat must not enter media routing")

    monkeypatch.setattr(settings, "media_intent_mode", "auto")
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    monkeypatch.setattr(chat_api, "route_media_intent", unexpected_media_route)
    monkeypatch.setattr(chat_api, "_start_session_title_task", lambda **_kwargs: None)
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={
                    "title": "人物抠图任务",
                    "preferred_model": "qwen3.7-plus",
                },
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            endpoint = f"/chat/sessions/{session_id}/boundary-check"

            below_threshold = client.post(
                endpoint,
                headers=headers,
                json={
                    "message": "请撰写 AIMPLOYEE 企业盒子产品介绍",
                    "client_message_id": "boundary-low-user",
                    "client_assistant_message_id": "boundary-low-assistant",
                    "model": "qwen3.7-plus",
                },
            )
            assert below_threshold.status_code == 200
            assert below_threshold.json() == {
                "recommendation": "stay",
                "reason_code": "below_usage_threshold",
                "session_input_tokens": 0,
                # 会话还没有任何 completed assistant，窗口未知，回退默认窗口。
                "session_context_window": None,
                "threshold_tokens": chat_api._chat_session_boundary_threshold(None),
                "decision_id": None,
            }

            stored = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "messages": [
                        {
                            "id": "boundary-user",
                            "role": "user",
                            "content": "请完成这张人物照片的抠图并导出透明 PNG",
                        },
                        {
                            "id": "boundary-assistant",
                            "role": "assistant",
                            "content": "人物抠图已经完成。",
                            "usage": {
                                "provider": "agent_chat",
                                "input_tokens": 90_000,
                                "context_input_tokens": 90_000,
                                "output_tokens": 100,
                                "total_tokens": 90_100,
                            },
                        },
                    ]
                },
            )
            assert stored.status_code == 200

            task = "请撰写 AIMPLOYEE 企业盒子产品介绍"
            boundary_request = {
                "message": task,
                "client_message_id": "boundary-new-user",
                "client_assistant_message_id": "boundary-new-assistant",
                "model": "qwen3.7-plus",
            }
            unrelated = client.post(
                endpoint,
                headers=headers,
                json=boundary_request,
            )
            assert unrelated.status_code == 200
            assert unrelated.json()["recommendation"] == "confirm_new"
            assert unrelated.json()["reason_code"] == "large_session_unrelated"
            assert unrelated.json()["session_input_tokens"] == 90_000
            decision_id = unrelated.json()["decision_id"]
            assert decision_id

            with database.SessionLocal() as db:
                decision = db.get(ChatDispatchDecision, decision_id)
                assert decision is not None
                assert decision.status == "awaiting_confirmation"
                assert decision.target_type == "session_boundary_pending"
                assert task not in (decision.decision_json or "")

            repeated_check = client.post(
                endpoint,
                headers=headers,
                json=boundary_request,
            )
            assert repeated_check.status_code == 200
            assert repeated_check.json()["decision_id"] == decision_id

            omitted_confirmation = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    **boundary_request,
                    "local_session_id": session_id,
                },
            )
            assert omitted_confirmation.status_code == 409
            assert runtime_calls == 0

            confirm_endpoint = (
                f"/chat/sessions/{session_id}/boundary-decisions/{decision_id}:confirm"
            )
            confirmation = {**boundary_request, "target": "new"}
            confirmed = client.post(
                confirm_endpoint,
                headers=headers,
                json=confirmation,
            )
            assert confirmed.status_code == 200
            target_session_id = confirmed.json()["session"]["id"]
            assert confirmed.json()["target"] == "new"
            assert target_session_id != session_id

            repeated_confirmation = client.post(
                confirm_endpoint,
                headers=headers,
                json=confirmation,
            )
            assert repeated_confirmation.status_code == 200
            assert repeated_confirmation.json()["session"]["id"] == target_session_id

            listed = client.get("/chat/sessions", headers=headers)
            assert listed.status_code == 200
            assert {item["id"] for item in listed.json()} == {
                session_id,
                target_session_id,
            }

            for override in (
                {"model": "another-model"},
                {"message": "篡改后的任务"},
                {"client_message_id": "tampered-user-id"},
                {"client_assistant_message_id": "tampered-assistant-id"},
            ):
                tampered = client.post(
                    confirm_endpoint,
                    headers=headers,
                    json={**confirmation, **override},
                )
                assert tampered.status_code == 409

            stream_payload = {
                "message": task,
                "local_session_id": target_session_id,
                "client_message_id": boundary_request["client_message_id"],
                "client_assistant_message_id": boundary_request[
                    "client_assistant_message_id"
                ],
                "model": boundary_request["model"],
                "decision_id": decision_id,
            }

            changed_scope = client.post(
                "/knowledge-spaces",
                headers=headers,
                json={"name": "Changed authorized scope", "description": ""},
            )
            assert changed_scope.status_code == 201
            for extra_context in (
                {
                    "images": [
                        {
                            "type": "image",
                            "data": "AAAA",
                            "mimeType": "image/png",
                        }
                    ]
                },
                {"space_ids": [changed_scope.json()["id"]]},
                {"document_ids": ["doc-tampered"]},
                {"session_reference_ids": ["chs-tampered"]},
                {"writing_publication_ids": ["wpub-tampered"]},
                {"explicit_skill": "image-generation"},
                {"media_options": {"resolution": "2k"}},
                {"final_intent": "chat"},
            ):
                contextualized = client.post(
                    "/chat/messages",
                    headers=headers,
                    json={**stream_payload, **extra_context},
                )
                assert contextualized.status_code == 409
                assert runtime_calls == 0
            streamed = client.post(
                "/chat/messages",
                headers=headers,
                json=stream_payload,
            )
            assert streamed.status_code == 200
            events = [
                json.loads(line[6:])
                for line in streamed.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events[-1]["type"] == "done"
            assert events[-1]["reply"] == "AIMPLOYEE 产品介绍已完成。"
            assert events[-1]["usage"]["context_input_tokens"] == 42_000
            persisted_target = client.get(
                f"/chat/sessions/{target_session_id}",
                headers=headers,
            )
            assert persisted_target.status_code == 200
            persisted_assistant = next(
                message
                for message in persisted_target.json()["messages"]
                if message["id"] == boundary_request["client_assistant_message_id"]
            )
            assert persisted_assistant["usage"]["context_input_tokens"] == 42_000
            # 已冻结的服务端账单不可被浏览器旧快照伪造；遗留用量合并单独覆盖。
            forged_usage = client.post(
                f"/chat/sessions/{target_session_id}/messages", headers=headers,
                json={"replace": True, "messages": [{
                    "id": boundary_request["client_assistant_message_id"],
                    "role": "assistant", "content": persisted_assistant["content"],
                    "usage": {"provider": "agent_chat", "input_tokens": 120_000,
                              "output_tokens": 100, "total_tokens": 120_100,
                              "context_input_tokens": 10_000},
                }]},
            )
            assert forged_usage.status_code == 200, forged_usage.text
            frozen_assistant = next(
                message for message in forged_usage.json()["messages"]
                if message["id"] == boundary_request["client_assistant_message_id"]
            )
            assert frozen_assistant["usage"] == persisted_assistant["usage"]
            legacy_session = client.post(
                "/chat/sessions", headers=headers, json={"title": "遗留用量合并"},
            )
            assert legacy_session.status_code == 201, legacy_session.text
            legacy_session_id = legacy_session.json()["id"]
            legacy_assistant_id = "boundary-legacy-assistant"
            legacy_seed = client.post(
                f"/chat/sessions/{legacy_session_id}/messages", headers=headers,
                json={"messages": [{
                    "id": legacy_assistant_id, "role": "assistant",
                    "content": persisted_assistant["content"], "usage": persisted_assistant["usage"],
                }]},
            )
            assert legacy_seed.status_code == 200, legacy_seed.text
            assert not legacy_seed.json()["messages"][0]["metadata"].get("assistant_message_frozen")
            for has_context_snapshot, expected_context in (
                (False, 42_000),
                (True, 10_000),
                (True, 0),
                (True, None),
            ):
                usage_payload = {
                    "provider": "agent_chat",
                    "input_tokens": 120_000,
                    "output_tokens": 100,
                    "total_tokens": 120_100,
                }
                if has_context_snapshot:
                    usage_payload["context_input_tokens"] = expected_context
                merged_usage = client.post(
                    f"/chat/sessions/{legacy_session_id}/messages",
                    headers=headers,
                    json={
                        "replace": True,
                        "messages": [
                            {
                                "id": legacy_assistant_id,
                                "role": "assistant",
                                "content": "AIMPLOYEE 产品介绍已完成。",
                                "usage": usage_payload,
                            }
                        ],
                    },
                )
                assert merged_usage.status_code == 200
                restored_target = client.get(
                    f"/chat/sessions/{legacy_session_id}",
                    headers=headers,
                )
                assert restored_target.status_code == 200
                restored_assistant = next(
                    message
                    for message in restored_target.json()["messages"]
                    if message["id"] == legacy_assistant_id
                )
                assert restored_assistant["usage"]["input_tokens"] == 120_000
                assert (
                    restored_assistant["usage"]["context_input_tokens"]
                    == expected_context
                )
            assert runtime_calls == 1

            replayed = client.post(
                "/chat/messages",
                headers=headers,
                json=stream_payload,
            )
            assert replayed.status_code == 200
            assert runtime_calls == 1

            with database.SessionLocal() as db:
                decision = db.get(ChatDispatchDecision, decision_id)
                assert decision is not None
                assert decision.status == "dispatched"
                assert decision.target_type == "session_boundary_consumed"
                assert decision.target_id == target_session_id
                decision.created_at = (
                    datetime.now(timezone.utc)
                    - chat_api._CHAT_SESSION_BOUNDARY_DECISION_TTL
                    - timedelta(seconds=1)
                )
                db.commit()

            expired_completed_replay = client.post(
                "/chat/messages",
                headers=headers,
                json=stream_payload,
            )
            assert expired_completed_replay.status_code == 200
            assert runtime_calls == 1
            for replay_override in (
                {"model": "another-model"},
                {"media_options": {"resolution": "2k"}},
                {"explicit_skill": "image-generation"},
                {"final_intent": "chat"},
            ):
                tampered_replay = client.post(
                    "/chat/messages",
                    headers=headers,
                    json={**stream_payload, **replay_override},
                )
                assert tampered_replay.status_code == 409
                assert runtime_calls == 1

            second_request = {
                "message": "请创建企业盒子市场发布计划",
                "client_message_id": "boundary-current-user",
                "client_assistant_message_id": "boundary-current-assistant",
                "model": "qwen3.7-plus",
            }
            second_check = client.post(
                endpoint,
                headers=headers,
                json=second_request,
            )
            assert second_check.status_code == 200
            second_decision_id = second_check.json()["decision_id"]
            assert second_decision_id
            second_confirm = client.post(
                (
                    f"/chat/sessions/{session_id}/boundary-decisions/"
                    f"{second_decision_id}:confirm"
                ),
                headers=headers,
                json={**second_request, "target": "current"},
            )
            assert second_confirm.status_code == 200
            assert chat_api._claim_chat_session_boundary_decision(
                body=ChatMessageRequest(
                    **second_request,
                    local_session_id=session_id,
                    decision_id=second_decision_id,
                ),
                message=second_request["message"],
                user_id=auth["user"]["id"],
                model=second_request["model"],
            )
            # 上面的手动认领模拟了“首轮消费了 decision 但没有落库 completed
            # assistant”。此时请求绑定不一致的重试仍然拒绝；完全相同的请求可以
            # 重试，见本用例末尾的 retried_after_failed_turn。
            for tampered_retry in (
                {"message": "换一个完全不同的任务"},
                {"model": "another-model"},
                {"client_assistant_message_id": "boundary-current-other"},
            ):
                blocked_retry = client.post(
                    "/chat/messages",
                    headers=headers,
                    json={
                        **second_request,
                        **tampered_retry,
                        "local_session_id": session_id,
                        "decision_id": second_decision_id,
                    },
                )
                assert blocked_retry.status_code == 409
                assert runtime_calls == 1

            continuation = client.post(
                endpoint,
                headers=headers,
                json={
                    "message": "请继续把上面的背景换成透明色",
                    "client_message_id": "boundary-cont-user",
                    "client_assistant_message_id": "boundary-cont-assistant",
                    "model": "qwen3.7-plus",
                },
            )
            assert continuation.status_code == 200
            assert continuation.json()["recommendation"] == "stay"
            assert continuation.json()["reason_code"] == "continuation_signal"

            related = client.post(
                endpoint,
                headers=headers,
                json={
                    "message": "请整理一份人物抠图操作说明",
                    "client_message_id": "boundary-related-user",
                    "client_assistant_message_id": "boundary-related-assistant",
                    "model": "qwen3.7-plus",
                },
            )
            assert related.status_code == 200
            assert related.json()["recommendation"] == "stay"
            assert related.json()["reason_code"] == "related_topic"

            with_attachment = client.post(
                endpoint,
                headers=headers,
                json={
                    "message": task,
                    "client_message_id": "boundary-attachment-user",
                    "client_assistant_message_id": "boundary-attachment-assistant",
                    "model": "qwen3.7-plus",
                    "has_attachments": True,
                },
            )
            assert with_attachment.status_code == 200
            assert with_attachment.json()["recommendation"] == "stay"
            assert with_attachment.json()["reason_code"] == "explicit_context"

            compacted = client.post(
                "/chat/sessions",
                headers=headers,
                json={
                    "title": "已压缩的人物抠图会话",
                    "preferred_model": "qwen3.7-plus",
                },
            )
            assert compacted.status_code == 201
            compacted_session_id = compacted.json()["id"]
            compacted_messages = client.post(
                f"/chat/sessions/{compacted_session_id}/messages",
                headers=headers,
                json={
                    "messages": [
                        {
                            "id": "compact-user-old",
                            "role": "user",
                            "content": "请完成图片抠图",
                        },
                        {
                            "id": "compact-assistant-old",
                            "role": "assistant",
                            "content": "第一轮已完成。",
                            "usage": {
                                "input_tokens": 90_000,
                                "context_input_tokens": 90_000,
                                "output_tokens": 100,
                                "total_tokens": 90_100,
                            },
                        },
                        {
                            "id": "compact-user-latest",
                            "role": "user",
                            "content": "压缩上下文后继续",
                        },
                        {
                            "id": "compact-assistant-latest",
                            "role": "assistant",
                            "content": "压缩后的回答。",
                            "usage": {
                                "input_tokens": 120_000,
                                "context_input_tokens": 10_000,
                                "output_tokens": 100,
                                "total_tokens": 120_100,
                            },
                        },
                    ]
                },
            )
            assert compacted_messages.status_code == 200
            compacted_check = client.post(
                f"/chat/sessions/{compacted_session_id}/boundary-check",
                headers=headers,
                json={
                    "message": task,
                    "client_message_id": "boundary-compacted-user",
                    "client_assistant_message_id": "boundary-compacted-assistant",
                    "model": "qwen3.7-plus",
                },
            )
            assert compacted_check.status_code == 200
            assert compacted_check.json()["recommendation"] == "stay"
            assert compacted_check.json()["reason_code"] == "below_usage_threshold"
            assert compacted_check.json()["session_input_tokens"] == 10_000

            expired_request = {
                "message": "请制定企业盒子渠道发布节奏",
                "client_message_id": "boundary-expired-user",
                "client_assistant_message_id": "boundary-expired-assistant",
                "model": "qwen3.7-plus",
            }
            expired_check = client.post(
                endpoint,
                headers=headers,
                json=expired_request,
            )
            assert expired_check.status_code == 200
            expired_decision_id = expired_check.json()["decision_id"]
            assert expired_decision_id
            with database.SessionLocal() as db:
                expired_decision = db.get(
                    ChatDispatchDecision,
                    expired_decision_id,
                )
                assert expired_decision is not None
                expired_decision.created_at = (
                    datetime.now(timezone.utc)
                    - chat_api._CHAT_SESSION_BOUNDARY_DECISION_TTL
                    - timedelta(seconds=1)
                )
                db.commit()
            sessions_before_expired_confirm = len(
                client.get("/chat/sessions", headers=headers).json()
            )
            assert (
                client.post(
                    endpoint,
                    headers=headers,
                    json=expired_request,
                ).status_code
                == 409
            )
            expired_confirm = client.post(
                (
                    f"/chat/sessions/{session_id}/boundary-decisions/"
                    f"{expired_decision_id}:confirm"
                ),
                headers=headers,
                json={**expired_request, "target": "new"},
            )
            assert expired_confirm.status_code == 409
            assert (
                len(client.get("/chat/sessions", headers=headers).json())
                == sessions_before_expired_confirm
            )

            expired_claim_request = {
                "message": "请规划企业盒子销售渠道",
                "client_message_id": "boundary-expired-claim-user",
                "client_assistant_message_id": "boundary-expired-claim-assistant",
                "model": "qwen3.7-plus",
            }
            expired_claim_check = client.post(
                endpoint,
                headers=headers,
                json=expired_claim_request,
            )
            assert expired_claim_check.status_code == 200
            expired_claim_decision_id = expired_claim_check.json()["decision_id"]
            assert expired_claim_decision_id
            expired_claim_confirm = client.post(
                (
                    f"/chat/sessions/{session_id}/boundary-decisions/"
                    f"{expired_claim_decision_id}:confirm"
                ),
                headers=headers,
                json={**expired_claim_request, "target": "current"},
            )
            assert expired_claim_confirm.status_code == 200
            with database.SessionLocal() as db:
                expired_claim_decision = db.get(
                    ChatDispatchDecision,
                    expired_claim_decision_id,
                )
                assert expired_claim_decision is not None
                expired_claim_decision.created_at = (
                    datetime.now(timezone.utc)
                    - chat_api._CHAT_SESSION_BOUNDARY_DECISION_TTL
                    - timedelta(seconds=1)
                )
                db.commit()
            delayed_reconfirm = client.post(
                (
                    f"/chat/sessions/{session_id}/boundary-decisions/"
                    f"{expired_claim_decision_id}:confirm"
                ),
                headers=headers,
                json={**expired_claim_request, "target": "current"},
            )
            assert delayed_reconfirm.status_code == 200
            expired_claim = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    **expired_claim_request,
                    "local_session_id": session_id,
                    "decision_id": expired_claim_decision_id,
                },
            )
            assert expired_claim.status_code == 200
            assert runtime_calls == 2

            # 首轮消费 decision 后失败、且没有 completed assistant 落库时，
            # 完全相同的请求必须还能重试，否则用户会卡在必然 409 的重发上。
            retry_payload = {
                **second_request,
                "local_session_id": session_id,
                "decision_id": second_decision_id,
            }
            retried_after_failed_turn = client.post(
                "/chat/messages",
                headers=headers,
                json=retry_payload,
            )
            assert retried_after_failed_turn.status_code == 200
            assert runtime_calls == 3
            # 重试落库之后，同一请求回到长期幂等 replay，不再调用模型。
            assert (
                client.post(
                    "/chat/messages",
                    headers=headers,
                    json=retry_payload,
                ).status_code
                == 200
            )
            assert runtime_calls == 3
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_session_boundary_handoff_carries_the_stopped_turn_forward(
    tmp_path,
    monkeypatch,
):
    """受控停止后交接：新会话继承来源标题与分类，首轮带上交接上下文。"""
    from fastapi.testclient import TestClient

    from app.core import database
    from app.services.pi_runtime.runner import PiRunResult

    original = settings.db_path
    original_url = settings.database_url
    db_path = tmp_path / "chat-session-handoff.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    captured: list[dict[str, Any]] = []

    class FakePiClient:
        effective_model = "qwen3.7-plus"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **kwargs):
            captured.append(dict(kwargs))
            return (
                PiRunResult(
                    reply="已基于上一轮结果完成。",
                    stats={"contextUsage": {"tokens": 12_000}},
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

    seed_calls: list[dict[str, Any]] = []
    seed_result = {"ok": True}

    def fake_seed(**kwargs):
        seed_calls.append(dict(kwargs))
        return seed_result["ok"]

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    monkeypatch.setattr(chat_api, "seed_handoff_workspace", fake_seed)
    monkeypatch.setattr(chat_api, "_start_session_title_task", lambda **_kwargs: None)
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "企业盒子产品介绍", "preferred_model": "qwen3.7-plus"},
            )
            assert created.status_code == 201
            source_session_id = created.json()["id"]
            seeded = client.post(
                f"/chat/sessions/{source_session_id}/messages",
                headers=headers,
                json={
                    "messages": [
                        {
                            "id": "handoff-user-old",
                            "role": "user",
                            "content": "请撰写 AIMPLOYEE 企业盒子产品介绍并导出 docx",
                        },
                        {
                            "id": "handoff-assistant-done",
                            "role": "assistant",
                            "content": "资料收集完成。",
                            "usage": {
                                "provider": "agent_chat",
                                "status": "completed",
                                "input_tokens": 90_000,
                                "output_tokens": 400,
                                "total_tokens": 90_400,
                                "context_input_tokens": 90_000,
                                "context_window": 128_000,
                            },
                        },
                        {
                            "id": "handoff-user-stopped",
                            "role": "user",
                            "content": "继续写正文并导出 docx",
                        },
                        # 真实的交接触发点：受控停止的那一轮以 failed 落库，
                        # 它才是「做到哪了」的记录。
                        {
                            "id": "handoff-assistant-old",
                            "role": "assistant",
                            "content": "正文写到第三节时暂停。",
                            "usage": {
                                "provider": "agent_chat",
                                "status": "failed",
                                "error_code": "chat_token_budget_exceeded",
                                "input_tokens": 120_000,
                                "output_tokens": 600,
                                "total_tokens": 120_600,
                            },
                        },
                    ]
                },
            )
            assert seeded.status_code == 200

            handoff_request = {
                "message": "接着把正文写完并导出",
                "client_message_id": "handoff-user",
                "client_assistant_message_id": "handoff-assistant",
                "model": "qwen3.7-plus",
            }
            # 用户在受控停止卡片上主动要求交接：不跑话题启发式，
            # 即使这条消息带着明显的承接信号也直接给出一次性 decision。
            check = client.post(
                f"/chat/sessions/{source_session_id}/boundary-check",
                headers=headers,
                json={**handoff_request, "requested_target": "handoff"},
            )
            assert check.status_code == 200
            assert check.json()["recommendation"] == "confirm_new"
            assert check.json()["reason_code"] == "user_requested_handoff"
            decision_id = check.json()["decision_id"]
            assert decision_id

            confirmed = client.post(
                (
                    f"/chat/sessions/{source_session_id}/boundary-decisions/"
                    f"{decision_id}:confirm"
                ),
                headers=headers,
                json={**handoff_request, "target": "handoff"},
            )
            assert confirmed.status_code == 200
            assert confirmed.json()["target"] == "handoff"
            target = confirmed.json()["session"]
            target_session_id = target["id"]
            assert target_session_id != source_session_id
            assert target["title"] == "企业盒子产品介绍（续）"
            assert target["preferred_model"] == "qwen3.7-plus"
            # 不复制历史：新会话是空的，上下文靠首轮注入。
            assert target["messages"] == []

            streamed = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    **handoff_request,
                    "local_session_id": target_session_id,
                    "decision_id": decision_id,
                },
            )
            assert streamed.status_code == 200
            assert len(captured) == 1
            resume_context = str(captured[0].get("resume_context") or "")
            assert "【上一轮未完成任务交接】" in resume_context
            # 诉求取触发停止的那条用户消息，进展取那轮 failed assistant 的正文，
            # 而不是更早那条 completed 的结论。
            assert "继续写正文并导出 docx" in resume_context
            assert "正文写到第三节时暂停" in resume_context
            assert "资料收集完成。" not in resume_context
            # 工作区在拼接上下文之前按运行时 id 播种，文案要照真实结果写。
            assert len(seed_calls) == 1
            assert seed_calls[0] == {
                "user_id": auth["user"]["id"],
                "source_session_id": chat_api._runtime_session_id(
                    auth["user"]["id"], source_session_id
                ),
                "target_session_id": chat_api._runtime_session_id(
                    auth["user"]["id"], target_session_id
                ),
            }
            assert chat_api._HANDOFF_WORKSPACE_SEEDED in resume_context
            assert chat_api._HANDOFF_WORKSPACE_MISSING not in resume_context
            # 交接块自带上限，不靠外层截断；截断会先切掉它自己的 header。
            assert len(resume_context) <= chat_api._HANDOFF_CONTEXT_MAX_CHARS
            assert len(resume_context) <= (chat_api._SESSION_RECOVERY_CONTEXT_MAX_CHARS)

            # 播种失败时不得继续声称文件已复制：这段话是模型判断「文件在不在
            # 手上」的唯一依据，谎报会让它去找不存在的文件。
            seed_result["ok"] = False
            failed_seed_request = {
                "message": "把导出补上",
                "client_message_id": "handoff-retry-user",
                "client_assistant_message_id": "handoff-retry-assistant",
                "model": "qwen3.7-plus",
            }
            retry_check = client.post(
                f"/chat/sessions/{source_session_id}/boundary-check",
                headers=headers,
                json={**failed_seed_request, "requested_target": "handoff"},
            )
            assert retry_check.status_code == 200
            retry_decision_id = retry_check.json()["decision_id"]
            retry_confirm = client.post(
                (
                    f"/chat/sessions/{source_session_id}/boundary-decisions/"
                    f"{retry_decision_id}:confirm"
                ),
                headers=headers,
                json={**failed_seed_request, "target": "handoff"},
            )
            assert retry_confirm.status_code == 200
            retry_target_id = retry_confirm.json()["session"]["id"]
            assert (
                client.post(
                    "/chat/messages",
                    headers=headers,
                    json={
                        **failed_seed_request,
                        "local_session_id": retry_target_id,
                        "decision_id": retry_decision_id,
                    },
                ).status_code
                == 200
            )
            failed_seed_context = str(captured[-1].get("resume_context") or "")
            assert "【上一轮未完成任务交接】" in failed_seed_context
            assert chat_api._HANDOFF_WORKSPACE_MISSING in failed_seed_context
            assert chat_api._HANDOFF_WORKSPACE_SEEDED not in failed_seed_context

            # 来源会话保持原样。
            source_after = client.get(
                f"/chat/sessions/{source_session_id}",
                headers=headers,
            )
            assert source_after.status_code == 200
            assert [item["id"] for item in source_after.json()["messages"]] == [
                "handoff-user-old",
                "handoff-assistant-done",
                "handoff-user-stopped",
                "handoff-assistant-old",
            ]
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_session_boundary_references_travel_with_a_new_session(
    tmp_path,
    monkeypatch,
):
    """资料引用是指针：不再豁免判定，但必须与 decision 绑定的集合一致。"""
    from fastapi.testclient import TestClient

    from app.core import database

    original = settings.db_path
    original_url = settings.database_url
    db_path = tmp_path / "chat-session-boundary-refs.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    monkeypatch.setattr(chat_api, "_start_session_title_task", lambda **_kwargs: None)
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "人物抠图任务", "preferred_model": "qwen3.7-plus"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            assert (
                client.post(
                    f"/chat/sessions/{session_id}/messages",
                    headers=headers,
                    json={
                        "messages": [
                            {
                                "id": "refs-user",
                                "role": "user",
                                "content": "请完成人物照片抠图",
                            },
                            {
                                "id": "refs-assistant",
                                "role": "assistant",
                                "content": "抠图完成。",
                                "usage": {
                                    "provider": "agent_chat",
                                    "input_tokens": 90_000,
                                    "output_tokens": 100,
                                    "total_tokens": 90_100,
                                    "context_input_tokens": 90_000,
                                    "context_window": 128_000,
                                },
                            },
                        ]
                    },
                ).status_code
                == 200
            )

            request = {
                "message": "请撰写 AIMPLOYEE 企业盒子产品介绍",
                "client_message_id": "refs-boundary-user",
                "client_assistant_message_id": "refs-boundary-assistant",
                "model": "qwen3.7-plus",
                "references": {
                    "space_ids": ["spc-b", "spc-a"],
                    "document_ids": ["doc-1"],
                },
            }
            with_references = client.post(
                f"/chat/sessions/{session_id}/boundary-check",
                headers=headers,
                json=request,
            )
            assert with_references.status_code == 200
            # 带资料引用不再是留在当前会话的理由。
            assert with_references.json()["reason_code"] == "large_session_unrelated"
            decision_id = with_references.json()["decision_id"]
            assert decision_id

            # 顺序和重复不改变绑定。
            reordered = client.post(
                f"/chat/sessions/{session_id}/boundary-decisions/{decision_id}:confirm",
                headers=headers,
                json={
                    **request,
                    "references": {
                        "space_ids": ["spc-a", "spc-b", "spc-a"],
                        "document_ids": ["doc-1"],
                    },
                    "target": "new",
                },
            )
            assert reordered.status_code == 200

            # 换一组引用就是另一个请求。
            changed = client.post(
                f"/chat/sessions/{session_id}/boundary-decisions/{decision_id}:confirm",
                headers=headers,
                json={
                    **request,
                    "references": {"space_ids": ["spc-a"]},
                    "target": "new",
                },
            )
            assert changed.status_code == 409
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_session_boundary_concurrent_confirmation_creates_one_target(
    tmp_path,
    monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    from app.core import database
    from app.models.chat import ChatSession
    from app.schemas.chat import ChatSessionBoundaryConfirmRequest

    original = settings.db_path
    original_url = settings.database_url
    db_path = tmp_path / "chat-session-boundary-concurrent.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={
                    "title": "人物抠图任务",
                    "preferred_model": "qwen3.7-plus",
                },
            )
            assert created.status_code == 201
            source_session_id = created.json()["id"]
            stored = client.post(
                f"/chat/sessions/{source_session_id}/messages",
                headers=headers,
                json={
                    "messages": [
                        {
                            "id": "boundary-race-user",
                            "role": "user",
                            "content": "请完成人物抠图",
                        },
                        {
                            "id": "boundary-race-assistant",
                            "role": "assistant",
                            "content": "人物抠图已经完成。",
                            "usage": {
                                "input_tokens": 90_000,
                                "context_input_tokens": 90_000,
                                "output_tokens": 100,
                                "total_tokens": 90_100,
                            },
                        },
                    ]
                },
            )
            assert stored.status_code == 200
            request = {
                "message": "请撰写 AIMPLOYEE 企业盒子产品介绍",
                "client_message_id": "boundary-race-new-user",
                "client_assistant_message_id": "boundary-race-new-assistant",
                "model": "qwen3.7-plus",
            }
            checked = client.post(
                f"/chat/sessions/{source_session_id}/boundary-check",
                headers=headers,
                json=request,
            )
            assert checked.status_code == 200
            decision_id = checked.json()["decision_id"]
            assert decision_id

            barrier = threading.Barrier(2)
            original_get = chat_api.chat_dispatch.get_dispatch_decision_by_id
            get_lock = threading.Lock()
            initial_reads = 0

            def synchronized_get(*args, **kwargs):
                nonlocal initial_reads
                decision = original_get(*args, **kwargs)
                if kwargs.get("for_update"):
                    with get_lock:
                        initial_reads += 1
                        read_number = initial_reads
                    if read_number <= 2:
                        barrier.wait(timeout=10)
                return decision

            monkeypatch.setattr(
                chat_api.chat_dispatch,
                "get_dispatch_decision_by_id",
                synchronized_get,
            )
            confirmation = ChatSessionBoundaryConfirmRequest(
                **request,
                target="new",
            )

            def confirm_once() -> tuple[int, str | None]:
                try:
                    response = chat_api.confirm_session_boundary(
                        source_session_id,
                        decision_id,
                        confirmation,
                        user=SimpleNamespace(id=auth["user"]["id"]),
                    )
                    return 200, response.session.id
                except HTTPException as exc:
                    return exc.status_code, None

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _index: confirm_once(), range(2)))

            assert sorted(status for status, _target_id in results) == [200, 409]
            successful_target_id = next(
                target_id
                for status, target_id in results
                if status == 200 and target_id is not None
            )
            with database.SessionLocal() as db:
                session_ids = set(db.scalars(select(ChatSession.id)).all())
            assert session_ids == {source_session_id, successful_target_id}

            monkeypatch.setattr(
                chat_api.chat_dispatch,
                "get_dispatch_decision_by_id",
                original_get,
            )
            retried = chat_api.confirm_session_boundary(
                source_session_id,
                decision_id,
                confirmation,
                user=SimpleNamespace(id=auth["user"]["id"]),
            )
            assert retried.session.id == successful_target_id
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(db_path)


def test_project_writing_context_is_explicit_persisted_and_reauthorized(
    tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient

    from app.core import database
    from app.models.auth import User
    from app.services.chat_knowledge import resolve_chat_writing_context
    from app.services.pi_runtime.runner import PiRunResult

    old_path, old_url = settings.db_path, settings.database_url
    db_path = tmp_path / "explicit-writing-context.sqlite3"
    settings.db_path, settings.database_url = db_path, f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "media_intent_mode", "off")
    observed_scopes = []

    class Runtime:
        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            observed_scopes.append(decode_capability_token(kwargs["capability_token"]))
            return PiRunResult("已核对范围。", {}, {}, [], {}), object()

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", Runtime)
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            spaces = [
                client.post(
                    "/knowledge-spaces",
                    headers=headers,
                    json={"name": name, "description": ""},
                ).json()["id"]
                for name in ("selected", "unrelated")
            ]
            created = client.post(
                "/writing/projects",
                headers=headers,
                json={"title": "项目范围", "space_ids": [spaces[0]]},
            )
            assert created.status_code == 201, created.text
            project_id = created.json()["id"]
            session = client.post(
                "/chat/sessions", headers=headers, json={"title": "写作"}
            ).json()
            linked = client.patch(
                f"/chat/sessions/{session['id']}",
                headers=headers,
                json={"writing_project_id": project_id},
            )
            assert linked.status_code == 200, linked.text
            plain = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "普通聊天",
                    "local_session_id": session["id"],
                    "client_message_id": "plain-user",
                    "client_assistant_message_id": "plain-answer",
                },
            )
            assert plain.status_code == 200, plain.text
            assert observed_scopes[-1]["space_mode"] == "accessible"
            assert observed_scopes[-1]["project_ids"] == []
            request = {
                "message": "继续这份文稿",
                "local_session_id": session["id"],
                "client_message_id": "writing-user",
                "client_assistant_message_id": "writing-answer",
                "writing_context": {"project_id": project_id},
            }
            response = client.post("/chat/messages", headers=headers, json=request)
            assert response.status_code == 200, response.text
            assert observed_scopes[-1]["space_mode"] == "selected"
            assert observed_scopes[-1]["space_ids"] == [spaces[0]]
            calls_before_replay = len(observed_scopes)
            replay = client.post("/chat/messages", headers=headers, json=request)
            assert replay.status_code == 200, replay.text
            assert len(observed_scopes) == calls_before_replay
            restored = client.get(
                f"/chat/sessions/{session['id']}", headers=headers
            ).json()
            user_message = next(
                m for m in restored["messages"] if m["id"] == "writing-user"
            )
            context = user_message["writing_context"]
            assert context["project_id"] == project_id
            assert context["scope_hash"].startswith("sha256:")
            fork = client.post(
                f"/chat/sessions/{session['id']}/fork",
                headers=headers,
                json={"message_id": "writing-answer"},
            )
            assert fork.status_code == 201, fork.text
            assert (
                next(
                    m for m in fork.json()["messages"] if m["content"] == "继续这份文稿"
                )["writing_context"]
                == context
            )
            changed_retry = client.post(
                "/chat/messages",
                headers=headers,
                json={**request, "writing_context": None},
            )
            assert changed_retry.status_code == 409
            outside = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    **request,
                    "client_message_id": "outside-user",
                    "space_ids": [spaces[1]],
                },
            )
            assert outside.status_code == 422
            stale = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    **request,
                    "writing_context": {
                        "project_id": project_id,
                        "scope_hash": "sha256:stale",
                    },
                },
            )
            assert stale.status_code == 409
            with database.SessionLocal() as db:
                actor = db.get(User, auth["user"]["id"])
                assert actor is not None
                actor.status = "disabled"
                with pytest.raises(HTTPException) as rejected:
                    resolve_chat_writing_context(
                        db, user=actor, context=context, space_ids=[], document_ids=[]
                    )
                assert rejected.value.status_code == 403
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(db_path)


def test_runtime_citation_identity_is_case_sensitive_and_not_limited_to_fifty():
    refs = [
        {"id": f"writing-evidence:ver_{index}:0-20", "document_id": f"doc_{index}"}
        for index in range(65)
    ]
    mixed = {"id": "writing-evidence:ver_AbC:0-20", "document_id": "doc-mixed"}
    lower = {"id": "writing-evidence:ver_abc:0-20", "document_id": "doc-lower"}
    merged = chat_api._merge_chat_references(refs, [mixed, lower, refs[0]])
    assert [item["id"] for item in merged] == [
        *[item["id"] for item in refs],
        mixed["id"],
        lower["id"],
    ]
    event = {
        "type": "tool_execution_end",
        "toolName": "nexus_search_evidence",
        "result": {"details": {"evidence": [mixed, lower]}},
    }
    assert (
        chat_api._runtime_evidence_references(
            [event], f"事实 [citation:{mixed['id']}]"
        )[0]["document_id"]
        == "doc-mixed"
    )


@pytest.mark.parametrize("mode", ["steer", "follow_up"])
@pytest.mark.parametrize(
    ("memory_enabled", "transition"),
    [
        (False, "ended"),
        (False, "replaced"),
        (False, "accepted"),
        (True, "ended"),
        (True, "replaced"),
        (True, "accepted"),
        (True, "rotation_replaced"),
    ],
)
def test_control_rejected_after_turn_change_leaves_no_message(
    tmp_path,
    monkeypatch,
    mode,
    transition,
    memory_enabled,
):
    from fastapi.testclient import TestClient

    from app.core import database
    from app.models.user_memory import UserMemoryJob, UserMemoryTurn

    db_path = tmp_path / "control-turn-transition.sqlite3"
    old_path, old_url = settings.db_path, settings.database_url
    settings.db_path, settings.database_url = db_path, f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "personal_memory_enabled", memory_enabled)
    delivered = []

    class FakePiClient:
        def __init__(self, **kwargs):
            pass

        async def steer_async(self, message, **kwargs):
            delivered.append(message)

        async def follow_up_async(self, message, **kwargs):
            delivered.append(message)

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    monkeypatch.setattr(
        chat_api,
        "resolve_model_endpoint",
        lambda *args, **kwargs: SimpleNamespace(id=None, provider="pi", model=None),
    )
    monkeypatch.setattr(
        chat_api,
        "_resolve_chat_image_route",
        lambda *args, **kwargs: chat_api.ChatImageRoute(
            mode="ocr_assisted",
            answer_endpoint_id=None,
            answer_provider="pi",
            answer_model=None,
        ),
    )
    try:
        with TestClient(app) as client:
            auth = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {auth['access_token']}"}
            session_id = client.post("/chat/sessions", headers=headers, json={}).json()[
                "id"
            ]
            with database.SessionLocal() as db:
                session = chat_repo.get_owned_session(
                    db, session_id, auth["user"]["id"]
                )
                chat_repo.update_session(
                    db,
                    session,
                    agent_session_id=chat_api._runtime_session_id(
                        auth["user"]["id"], session_id
                    ),
                )
                db.commit()
            memory_context = {}
            next_memory_context = {}
            if memory_enabled:
                with database.SessionLocal() as db:
                    state = chat_api.user_memory_repo.get_or_create_state(
                        db, auth["user"]["id"]
                    )
                    state.learning_mode = "suggest"
                    state.automatic_source_cutoff_at = None
                    db.commit()
                chat_api._persist_server_chat_messages(
                    user_id=auth["user"]["id"],
                    local_session_id=session_id,
                    payloads=[
                        {"id": "control-source", "role": "user", "content": "我叫李梅"}
                    ],
                    memory_context=memory_context,
                )
            project_id = client.post(
                "/writing/projects/from-chat",
                headers=headers,
                json={"title": "控制请求所属文稿", "seed_markdown": "正文"},
            ).json()["id"]
            running = [
                {
                    "writing_context": {"project_id": project_id},
                    "space_ids": [],
                    "document_ids": [],
                    "memory_turn_id": memory_context.get("turn_id"),
                }
            ]
            monkeypatch.setitem(
                chat_api._ACTIVE_CHAT_CONTEXTS,
                (auth["user"]["id"], session_id),
                running,
            )

            def prepare_images(*args, **kwargs):
                if transition == "ended":
                    running.clear()
                elif transition == "replaced":
                    running[:] = [
                        {"writing_context": None, "space_ids": [], "document_ids": []}
                    ]
                return "本地 OCR 结果"

            monkeypatch.setattr(chat_api, "parse_images_with_ocr", prepare_images)
            if transition == "rotation_replaced":
                original_rotate = chat_api._rotate_memory_turn_for_control_input

                def replace_during_rotation(**kwargs):
                    chat_api._persist_server_chat_messages(
                        user_id=auth["user"]["id"],
                        local_session_id=session_id,
                        payloads=[
                            {
                                "id": "next-source",
                                "role": "user",
                                "content": "我在杭州工作",
                            }
                        ],
                        memory_context=next_memory_context,
                    )
                    running[:] = [
                        {
                            "writing_context": None,
                            "space_ids": [],
                            "document_ids": [],
                            "memory_turn_id": next_memory_context["turn_id"],
                        }
                    ]
                    original_rotate(**kwargs)

                monkeypatch.setattr(
                    chat_api,
                    "_rotate_memory_turn_for_control_input",
                    replace_during_rotation,
                )
            response = client.post(
                f"/chat/sessions/{session_id}/{'follow-up' if mode == 'follow_up' else 'steer'}",
                headers=headers,
                json={
                    "message": "依据当前文稿继续",
                    "client_message_id": "control-input",
                    "images": [{"data": "aW1hZ2U=", "mimeType": "image/png"}],
                },
            )
            messages = client.get(
                f"/chat/sessions/{session_id}", headers=headers
            ).json()["messages"]
            if transition == "accepted":
                assert response.status_code == 200, response.text
                assert response.json()["server_persisted"] is True
                saved = next(item for item in messages if item["id"] == "control-input")
                assert saved["writing_context"]["project_id"] == project_id
                assert delivered == ["依据当前文稿继续\n\n本地 OCR 结果"]
            else:
                assert response.status_code == 409, response.text
                assert all(item["id"] != "control-input" for item in messages)
                assert delivered == []
            if memory_enabled:
                with database.SessionLocal() as db:
                    source_turn = db.get(UserMemoryTurn, memory_context["turn_id"])
                    assert source_turn.status == (
                        "closed"
                        if transition in {"accepted", "rotation_replaced"}
                        else "live"
                    )
                    control_turn = db.scalar(
                        select(UserMemoryTurn).where(
                            UserMemoryTurn.chat_message_id == "control-input",
                        )
                    )
                    if transition == "accepted":
                        assert control_turn is not None
                        assert (
                            db.scalar(
                                select(UserMemoryJob.id).where(
                                    UserMemoryJob.turn_id == control_turn.id,
                                )
                            )
                            is not None
                        )
                    else:
                        assert control_turn is None
                    if transition == "rotation_replaced":
                        assert (
                            db.get(
                                UserMemoryTurn, next_memory_context["turn_id"]
                            ).status
                            == "live"
                        )
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(db_path)


# ── 跨轮引用复用与 citation_statuses ─────────────────────────────
# 首轮工具来源即使正文没有 marker 也要持久化；后轮只凭正文 exact marker 时必须回填
# 同 owner/session 的先前来源，并在 retrieval SSE、持久化记录与 GET 详情里给出一致
# 结果（失败引用标 unavailable/unlinked 且不泄漏元数据）。

_CITATION_TEST_PASSWORD = "Member-password-123"


@pytest.fixture
def citation_client(tmp_path, monkeypatch):
    """隔离 SQLite + upload_dir 的 TestClient，退出时还原 settings。"""
    from fastapi.testclient import TestClient

    from app.core import database

    old_path, old_url = settings.db_path, settings.database_url
    db_path = tmp_path / "chat-citations.sqlite3"
    settings.db_path, settings.database_url = db_path, f"sqlite:///{db_path}"
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    try:
        with TestClient(app) as client:
            yield client
    finally:
        settings.db_path, settings.database_url = old_path, old_url
        database.reset_database(db_path)


def _citation_owner(client, username, *, space_name):
    """新建成员，并让它在自己的知识空间里拿到 reader 权限。"""
    admin = {"Authorization": f"Bearer {bootstrap_admin(client)['access_token']}"}
    invited = client.post(
        "/admin/users",
        headers=admin,
        json={
            "username": username,
            "email": f"{username}@example.com",
            "display_name": username,
        },
    )
    assert invited.status_code == 201, invited.text
    accepted = client.post(
        f"/auth/invitations/{invited.json()['invite_token']}/accept",
        json={
            "password": _CITATION_TEST_PASSWORD,
            "password_confirmation": _CITATION_TEST_PASSWORD,
        },
    )
    assert accepted.status_code == 200, accepted.text
    login = client.post(
        "/auth/login", json={"username": username, "password": _CITATION_TEST_PASSWORD}
    )
    assert login.status_code == 200, login.text
    user, token = login.json()["user"], login.json()["access_token"]
    space_id = client.post(
        "/knowledge-spaces", headers=admin, json={"name": space_name, "description": ""}
    ).json()["id"]
    granted = client.post(
        f"/knowledge-spaces/{space_id}/members",
        headers=admin,
        json={"user_id": user["id"], "role": "reader"},
    )
    assert granted.status_code == 201, granted.text
    return admin, {"Authorization": f"Bearer {token}"}, user, space_id


def _citation_source(tmp_path, *, space_id, owner_id, name, text, end=None):
    """真实 Document/Version + 冻结快照，返回 citation 身份与 DB 真值。"""
    from app.core.database import SessionLocal
    from app.repositories.documents import create_document
    from app.services.evidence_snapshots import freeze_text, make_citation_id

    with SessionLocal() as db:
        document, version = create_document(
            db,
            space_id=space_id,
            category_id=None,
            name=name,
            created_by_user_id=owner_id,
            file_path=str(tmp_path / "source.txt"),
        )
        version.processing_stage = "ready"
        version.queryable = True
        version.is_current_published = True
        db.commit()
        document_id, version_id = document.id, version.id
        created_at = document.created_at
    digest = freeze_text(version_id, text)
    char_end = len(text) if end is None else end
    return SimpleNamespace(
        document_id=document_id,
        version_id=version_id,
        digest=digest,
        char_end=char_end,
        created_at=created_at,
        text=text,
        name=name,
        citation_id=make_citation_id(version_id, digest, 0, char_end),
    )


def _citation_seed(client, tmp_path, username, *, space_name, name, text, end=None):
    """成员 + 它有 reader 权限的空间 + 空间里的真实文档快照。"""
    admin, headers, member, space_id = _citation_owner(
        client, username, space_name=space_name
    )
    source = _citation_source(
        tmp_path,
        space_id=space_id,
        owner_id=member["id"],
        name=name,
        text=text,
        end=end,
    )
    return SimpleNamespace(
        admin=admin, headers=headers, member=member, space_id=space_id, source=source
    )


def _citation_candidate(source):
    """先前来源带着伪造的标题/上传者/时间/空间；回填必须改用 DB 真值。"""
    return {
        "id": source.citation_id,
        "document_id": source.document_id,
        "document_version_id": source.version_id,
        "text_sha256": source.digest,
        "char_start": 0,
        "char_end": source.char_end,
        "document_name": "伪造资料名",
        "space_id": "spc-forged",
        "created_by_user_id": "usr-forged",
        "created_by_display_name": "伪造上传人",
        "created_at": "1999-01-01T00:00:00+00:00",
        "excerpt": "伪造摘录SECRET",
        "support_state": "supported",
    }


def _citation_runtime(source, marker_reply):
    """首轮：工具返回来源、正文无 marker；之后各轮：无工具、只输出 exact marker。"""
    from app.services.pi_runtime.runner import PiRunResult

    tool_event = {
        "type": "tool_execution_end",
        "toolName": "nexus_search_evidence",
        "isError": False,
        "result": {
            "details": {
                "ok": True,
                "tool": "search_evidence",
                "inline": {"evidence": [_citation_candidate(source)]},
            }
        },
    }

    class _Runtime:
        effective_model = "chat-model"
        calls = 0

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **_kwargs):
            type(self).calls += 1
            events = [tool_event] if type(self).calls == 1 else []
            reply = "已检索到资料，本轮正文暂未引用。" if events else marker_reply
            return PiRunResult(reply, {}, {}, events, {}), object()

    return _Runtime


def _citation_turn(client, headers, session_id, *, space_id, index):
    return client.post(
        "/chat/messages",
        headers=headers,
        json={
            "message": f"第 {index} 轮",
            "local_session_id": session_id,
            "space_ids": [space_id],
            "client_message_id": f"citation-turn{index}-user",
            "client_assistant_message_id": f"citation-turn{index}-assistant",
        },
    )


def _citation_first_turn(
    client, tmp_path, monkeypatch, *, username, space_name, name, text
):
    """seed 之后建会话，并用 fake Pi 跑完首轮工具检索。"""
    env = _citation_seed(
        client, tmp_path, username, space_name=space_name, name=name, text=text
    )
    runtime = _citation_runtime(env.source, f"结论 ^[{env.source.citation_id}]")
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", runtime)
    env.runtime = runtime
    env.session_id = client.post(
        "/chat/sessions", headers=env.headers, json={"title": "跨轮引用"}
    ).json()["id"]
    first = _citation_turn(
        client, env.headers, env.session_id, space_id=env.space_id, index=1
    )
    assert first.status_code == 200, first.text
    return env


def _citation_history(message_id, content, *, role="assistant", references=None):
    return {
        "id": message_id,
        "role": role,
        "content": content,
        "references": references or [],
    }


def _citation_session(client, headers, owner_id, payloads):
    """建会话并按顺序写入给定的历史消息。"""
    from app.core.database import SessionLocal

    session_id = client.post(
        "/chat/sessions", headers=headers, json={"title": "引用核验"}
    ).json()["id"]
    with SessionLocal() as db:
        session = chat_repo.get_owned_session(db, session_id, owner_id)
        assert session is not None
        chat_repo.append_messages(db, session, payloads)
        db.commit()
    return session_id


def _citation_messages(client, headers, session_id):
    return client.get(f"/chat/sessions/{session_id}", headers=headers).json()[
        "messages"
    ]


def _citation_pick(messages, message_id):
    return next(item for item in messages if item["id"] == message_id)


def _assert_trusted_metadata(reference, source, *, space_id, space_name, owner_name):
    """回填来源必须用 DB 真值，而不是先前记录里的伪造标题/上传者/时间/空间。"""
    from app.core.security import iso

    assert (reference["document_id"], reference["document_name"]) == (
        source.document_id,
        source.name,
    )
    assert (reference["space_id"], reference["space_name"]) == (space_id, space_name)
    assert reference["created_by_display_name"] == owner_name
    assert reference["created_at"] == iso(source.created_at)
    assert reference["excerpt"] == source.text


def _sse_payloads(response):
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _last_retrieval(payloads):
    retrievals = [item for item in payloads if item.get("type") == "retrieval"]
    assert retrievals, payloads
    return retrievals[-1]


@pytest.mark.parametrize(
    "narrowed", [False, True], ids=["same-scope", "narrowed-scope"]
)
def test_later_turn_reuses_prior_tool_reference_only_inside_the_current_scope(
    citation_client, tmp_path, monkeypatch, narrowed
):
    """首轮工具来源在后轮只凭 exact marker 复用；显式收窄资料范围时不得回填。"""
    from app.core.database import SessionLocal
    from app.models.chat import ChatMessage

    owner = "citation-scope-owner" if narrowed else "citation-backfill-owner"
    home_space = "citation-scope-space" if narrowed else "citation-backfill-space"
    env = _citation_first_turn(
        citation_client,
        tmp_path,
        monkeypatch,
        username=owner,
        space_name=home_space,
        name="缔智元公司介绍",
        text="企业级人工智能与数字员工。",
    )
    client, headers, source = citation_client, env.headers, env.source
    scope_id = env.space_id
    if narrowed:
        scope_id = client.post(
            "/knowledge-spaces",
            headers=env.admin,
            json={"name": "citation-scope-other-space", "description": ""},
        ).json()["id"]
        granted = client.post(
            f"/knowledge-spaces/{scope_id}/members",
            headers=env.admin,
            json={"user_id": env.member["id"], "role": "reader"},
        )
        assert granted.status_code == 201, granted.text

    second = _citation_turn(client, headers, env.session_id, space_id=scope_id, index=2)
    assert second.status_code == 200, second.text
    payloads = _sse_payloads(second)
    assert payloads[-1]["type"] == "done"
    retrievals = [item for item in payloads if item.get("type") == "retrieval"]
    cited = [
        ref
        for item in retrievals
        for ref in item["references"]
        if ref.get("id") == source.citation_id
    ]

    messages = _citation_messages(client, headers, env.session_id)
    # 首轮正文没有 marker，工具来源仍必须留在记录里供后轮复用。
    first_assistant = _citation_pick(messages, "citation-turn1-assistant")
    assert [ref["id"] for ref in first_assistant["references"]] == [source.citation_id]
    assert first_assistant["citation_statuses"] == {}
    stored = _citation_pick(messages, "citation-turn2-assistant")

    if narrowed:
        # 正文引用落在显式收窄后的范围之外：不回填、标 unavailable、不泄漏元数据。
        assert cited == []
        assert retrievals[-1]["citation_statuses"] == {
            source.citation_id: "unavailable"
        }
        assert stored["references"] == []
        assert stored["citation_statuses"] == {source.citation_id: "unavailable"}
        for leak in ("伪造", source.name, source.text):
            assert leak not in second.text
            assert leak not in json.dumps(stored, ensure_ascii=False)
        return

    # 正文 exact marker 命中先前来源，retrieval/详情/持久化都要给出重建后的 DB 真值。
    assert len(cited) == 1
    assert retrievals[-1]["citation_statuses"] == {}
    _assert_trusted_metadata(
        cited[0], source, space_id=env.space_id, space_name=home_space, owner_name=owner
    )
    assert "伪造" not in json.dumps(retrievals, ensure_ascii=False)
    assert stored["citation_statuses"] == {}
    assert [ref["id"] for ref in stored["references"]] == [source.citation_id]
    _assert_trusted_metadata(
        stored["references"][0],
        source,
        space_id=env.space_id,
        space_name=home_space,
        owner_name=owner,
    )
    assert "伪造" not in json.dumps(stored, ensure_ascii=False)
    with SessionLocal() as db:
        row = db.get(ChatMessage, "citation-turn2-assistant")
        assert row is not None
        persisted = json.loads(row.references_json)
        assert [ref["id"] for ref in persisted] == [source.citation_id]
        assert persisted[0]["document_name"] == source.name

    # 同一请求重放必须复用已持久化的来源，不再调用运行时。
    replayed = _citation_turn(
        client, headers, env.session_id, space_id=env.space_id, index=2
    )
    assert replayed.status_code == 200, replayed.text
    assert env.runtime.calls == 2
    replay_retrieval = _last_retrieval(_sse_payloads(replayed))
    assert replay_retrieval["citation_statuses"] == {}
    assert source.citation_id in {
        ref.get("id") for ref in replay_retrieval["references"]
    }


def test_history_detail_backfills_empty_references_without_rewriting_the_stored_row(
    citation_client, tmp_path
):
    """历史空 refs 的消息 GET 时安全补全，但 DB 原始行保持不动。"""
    from app.core.database import SessionLocal
    from app.models.chat import ChatMessage

    env = _citation_seed(
        citation_client,
        tmp_path,
        "citation-history-owner",
        space_name="citation-history-space",
        name="慧云科技公司介绍",
        text="企业级人工智能与数字员工。",
    )
    headers, member, source = env.headers, env.member, env.source
    session_id = _citation_session(
        citation_client,
        headers,
        member["id"],
        [
            _citation_history("history-u1", "先查资料", role="user"),
            _citation_history(
                "history-a1",
                "先前检索到的资料。",
                references=[_citation_candidate(source)],
            ),
            _citation_history("history-u2", "再说一次", role="user"),
            _citation_history("history-a2", f"结论 ^[{source.citation_id}]"),
        ],
    )
    target = _citation_pick(
        _citation_messages(citation_client, headers, session_id), "history-a2"
    )
    assert target["citation_statuses"] == {}
    assert [ref["id"] for ref in target["references"]] == [source.citation_id]
    reference = target["references"][0]
    # 伪造的标题/上传者/时间/空间必须由 DB 真值替代。
    _assert_trusted_metadata(
        reference,
        source,
        space_id=env.space_id,
        space_name="citation-history-space",
        owner_name="citation-history-owner",
    )
    assert "伪造" not in json.dumps(target, ensure_ascii=False)
    with SessionLocal() as db:
        # 读回补全只发生在响应里：原记录仍是空引用，历史来源也未被改写。
        empty_row = db.get(ChatMessage, "history-a2")
        assert empty_row is not None
        assert empty_row.references_json == "[]"
        source_row = db.get(ChatMessage, "history-a1")
        assert source_row is not None
        assert (
            json.loads(source_row.references_json)[0]["document_name"] == "伪造资料名"
        )


@pytest.mark.parametrize("mode", ["expired", "deleted", "missing", "moved", "moved_space"])
@pytest.mark.parametrize("explicit_knowledge", [False, True])
def test_history_invalid_attachment_only_revokes_its_own_citation(
    citation_client, tmp_path, mode, explicit_knowledge,
):
    """附件失效不连带撤销仍有 ACL 的知识引用或同轮其它附件。"""
    from datetime import datetime, timedelta, timezone

    from app.core.database import SessionLocal
    from app.models.documents import Document

    env = _citation_seed(
        citation_client, tmp_path, "history-attachment-owner",
        space_name="history-attachment-space", name="公司资料", text="公司知识原文。",
    )
    attachments = [
        _citation_source(
            tmp_path, space_id=None, owner_id=env.member["id"],
            name=f"附件{index}", text=f"附件{index}原文。",
        )
        for index in range(2)
    ]
    sources = [env.source, *attachments]
    request = _citation_history("attachment-user", "结合附件查资料", role="user")
    request["document_ids"] = [source.document_id for source in attachments]
    if explicit_knowledge:
        request["space_ids"] = [env.space_id]
        request["document_ids"].append(env.source.document_id)
    session_id = _citation_session(
        citation_client, env.headers, env.member["id"], [
            request,
            _citation_history(
                "attachment-assistant",
                " ".join(f"^[{source.citation_id}]" for source in sources),
                references=[_citation_candidate(source) for source in sources],
            ),
        ],
    )
    with SessionLocal() as db:
        for source in attachments:
            document = db.get(Document, source.document_id)
            document.is_temporary = True
            document.chat_session_id = session_id
        db.commit()
    before = _citation_pick(
        _citation_messages(citation_client, env.headers, session_id), "attachment-assistant",
    )
    assert {ref["id"] for ref in before["references"]} == {s.citation_id for s in sources}
    moved_space = None
    if mode == "moved_space":
        moved_space = citation_client.post(
            "/knowledge-spaces", headers=env.admin,
            json={"name": "attachment-moved-space", "description": ""},
        ).json()["id"]
    with SessionLocal() as db:
        document = db.get(Document, attachments[0].document_id)
        if mode == "expired":
            document.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif mode == "deleted":
            document.status = "deleted"
        elif mode == "missing":
            db.delete(document)
        elif mode == "moved":
            document.chat_session_id = None
        else:
            # 移到未选择的文件夹，不把旧附件身份变成新的知识授权。
            document.space_id = moved_space
        db.commit()
    after = _citation_pick(
        _citation_messages(citation_client, env.headers, session_id), "attachment-assistant",
    )
    assert {ref["id"] for ref in after["references"]} == {
        env.source.citation_id, attachments[1].citation_id,
    }
    assert after["citation_statuses"] == {attachments[0].citation_id: "unavailable"}


@pytest.mark.parametrize(
    ("filename", "uncertainty", "stored_state", "expected_state"),
    [
        ("photo.jpg", "", "uncertain", "supported"),
        ("photo.jpg", "\n\n【不确定性（不可作确定事实）】\n编号模糊", "uncertain", "uncertain"),
        ("photo.jpg", "", "unsupported", "unsupported"),
        ("scan.pdf", "", "uncertain", "uncertain"),
    ],
)
def test_history_image_reference_projects_verified_source_without_rewriting_history(
    citation_client, tmp_path, filename, uncertainty, stored_state, expected_state,
):
    from app.core.database import SessionLocal
    from app.models.chat import ChatMessage
    from app.models.documents import DocumentVersion
    from app.repositories.document_pages import replace_version_pages
    from app.services.evidence_snapshots import read_snapshot
    from app.services.pdf_ocr import ExtractedPdfPage, PageTextSpan, PdfExtractionResult, PdfTextBlock

    raw = "视觉模型解析，未经核验\n\n【可见文字】\n设备功率 37.5W" + uncertainty
    env = _citation_seed(
        citation_client, tmp_path, "photo-history-owner",
        space_name="照片引用空间", name=filename, text=raw,
    )
    source = env.source
    with SessionLocal() as db:
        version = db.get(DocumentVersion, source.version_id)
        version.file_path = str(tmp_path / filename)
        version.source_relative_path = filename
        page = ExtractedPdfPage(
            page_number=1, width_points=100, height_points=100, rotation=0,
            extraction_method="vision", text=raw, quality_score=0.5,
            average_confidence=None, warning_code="image_vision_uncertain",
            blocks=(PdfTextBlock(0, raw, "vision"),),
        )
        replace_version_pages(
            db, version=version, extraction=PdfExtractionResult(
                pages=(page,), text=raw, page_spans=(PageTextSpan(1, 0, len(raw)),),
                extraction_method="vision", ocr_page_count=0, vision_page_count=1,
                warning_code="image_vision_uncertain",
            ),
        )
        db.commit()
    candidate = {**_citation_candidate(source), "excerpt": raw, "support_state": stored_state}
    old_body = f"旧回答仍记录未经核验。 ^[{source.citation_id}]"
    session_id = _citation_session(
        citation_client, env.headers, env.member["id"], [
            _citation_history("photo-u1", "查看照片", role="user"),
            _citation_history("photo-a1", old_body, references=[candidate]),
        ],
    )
    target = _citation_pick(
        _citation_messages(citation_client, env.headers, session_id), "photo-a1",
    )
    assert target["content"] == old_body
    assert target["citation_statuses"] == {}
    reference = target["references"][0]
    assert reference["id"] == source.citation_id
    assert reference["support_state"] == expected_state
    assert reference["text_sha256"] == source.digest
    assert (reference["char_start"], reference["char_end"]) == (0, len(raw))
    assert read_snapshot(source.version_id, source.digest) == raw
    if filename.endswith(".jpg"):
        assert "视觉模型解析，未经核验" not in reference["excerpt"]
        assert reference["page_number"] == 1
        assert reference["page_image_path"].endswith("/pages/1/image")
    else:
        assert reference["excerpt"] == raw
    if uncertainty:
        assert "编号模糊" in reference["excerpt"]
    with SessionLocal() as db:
        stored = db.get(ChatMessage, "photo-a1")
        assert stored.content == old_body
        assert json.loads(stored.references_json)[0]["excerpt"] == raw
        assert json.loads(stored.references_json)[0]["support_state"] == stored_state


@pytest.mark.parametrize("filename", ["source.txt", "photo.jpg"])
@pytest.mark.parametrize("mode", ["revoked", "corrupt_snapshot", "invalid_range"])
def test_history_detail_drops_unavailable_citations_and_hides_their_metadata(
    citation_client, tmp_path, mode, filename,
):
    """撤权/快照损坏/非法区间：不回填、标 unavailable、不泄漏该引用元数据。"""
    from app.core.database import SessionLocal
    from app.models.access import SpaceMember
    from app.models.chat import ChatMessage

    text = "内部资料原文片段。"
    env = _citation_seed(
        citation_client,
        tmp_path,
        f"citation-{mode}-owner",
        space_name=f"citation-{mode}-space",
        name="不可用资料标题",
        text=text,
        end=len(text) + 5 if mode == "invalid_range" else None,
    )
    headers, member, source = env.headers, env.member, env.source
    from app.models.documents import DocumentVersion

    with SessionLocal() as db:
        version = db.get(DocumentVersion, source.version_id)
        version.source_relative_path = filename
        version.file_path = str(tmp_path / filename)
        version.extraction_method = "vision"
        version.extraction_warning_code = "image_vision_uncertain"
        db.commit()
    message_id = f"unavailable-{mode}-a1"
    session_id = _citation_session(
        citation_client,
        headers,
        member["id"],
        [
            _citation_history(f"unavailable-{mode}-u1", "查资料", role="user"),
            _citation_history(
                message_id,
                f"结论 ^[{source.citation_id}]",
                references=[_citation_candidate(source)],
            ),
        ],
    )
    if mode == "revoked":
        # 收回空间读权限：已有候选但当前 ACL 不再授权。
        with SessionLocal() as db:
            row = db.scalar(
                select(SpaceMember).where(
                    SpaceMember.space_id == env.space_id,
                    SpaceMember.user_id == member["id"],
                )
            )
            assert row is not None
            db.delete(row)
            db.commit()
    elif mode == "corrupt_snapshot":
        snapshot = (
            settings.upload_dir
            / "_evidence_snapshots"
            / source.version_id
            / f"{source.digest}.txt"
        )
        snapshot.write_bytes("被篡改的正文".encode())

    target = _citation_pick(
        _citation_messages(citation_client, headers, session_id), message_id
    )
    assert target["references"] == []
    assert target["citation_statuses"] == {source.citation_id: "unavailable"}
    serialized = json.dumps(target, ensure_ascii=False)
    for leak in ("伪造资料名", "伪造上传人", "伪造摘录SECRET", "不可用资料标题", text):
        assert leak not in serialized
    with SessionLocal() as db:
        # 历史记录不被改写，失败只体现在响应状态里。
        stored = db.get(ChatMessage, message_id)
        assert stored is not None
        persisted = json.loads(stored.references_json)
        assert persisted[0]["document_id"] == source.document_id
        assert persisted[0]["document_name"] == "伪造资料名"


def test_history_detail_never_backfills_unknown_foreign_or_future_sources(
    citation_client, tmp_path
):
    """未知 ID、其他 owner/session、更晚的消息都不能当作先前来源。"""
    from app.services.evidence_snapshots import make_citation_id

    env = _citation_seed(
        citation_client,
        tmp_path,
        "citation-isolation-owner",
        space_name="citation-isolation-space",
        name="隔离资料",
        text="企业级人工智能与数字员工。",
    )
    headers, member, source = env.headers, env.member, env.source
    admin_id = bootstrap_admin(citation_client)["user"]["id"]

    def ranged(start, end):
        item = SimpleNamespace(**vars(source))
        item.char_end = end
        item.citation_id = make_citation_id(
            source.version_id, source.digest, start, end
        )
        return item

    other, future = ranged(0, 5), ranged(0, 8)
    session_id = _citation_session(
        citation_client,
        headers,
        member["id"],
        [
            _citation_history("isolation-u1", "开始", role="user"),
            _citation_history("isolation-unknown", "结论 ^[cit_unknown000]"),
            _citation_history("isolation-other", f"结论 ^[{other.citation_id}]"),
            _citation_history("isolation-future", f"结论 ^[{future.citation_id}]"),
            _citation_history(
                "isolation-late",
                "后来检索到的资料。",
                references=[_citation_candidate(future)],
            ),
        ],
    )
    # 另一位 owner 的另一次会话持有候选来源：跨 owner/session 都不能当作先前来源。
    _citation_session(
        citation_client,
        env.admin,
        admin_id,
        [
            _citation_history(
                "isolation-outsider-session",
                "别人的会话资料。",
                references=[_citation_candidate(other)],
            ),
        ],
    )

    messages = _citation_messages(citation_client, headers, session_id)
    for message_id, expected in (
        ("isolation-unknown", {"cit_unknown000": "unlinked"}),
        ("isolation-other", {other.citation_id: "unlinked"}),
        ("isolation-future", {future.citation_id: "unlinked"}),
    ):
        message = _citation_pick(messages, message_id)
        assert message["references"] == []
        assert message["citation_statuses"] == expected


@pytest.mark.anyio
async def test_chat_preflight_pool_is_bounded_nonblocking_and_cancel_safe(
    monkeypatch,
):
    limit = settings.chat_preflight_max_workers
    monkeypatch.setattr(chat_api, "_CHAT_PREFLIGHT_QUEUE_TIMEOUT_SECONDS", 0.03)
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0

    def slow_preflight(index: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            release.wait(timeout=2)
            return index
        finally:
            with lock:
                active -= 1

    tasks = [
        asyncio.create_task(chat_api._run_chat_preflight(slow_preflight, index))
        for index in range(limit)
    ]
    deadline = asyncio.get_running_loop().time() + 1
    while True:
        with lock:
            running = active
        if running == limit:
            break
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.005)

    # A saturated synchronous preflight pool must not prevent unrelated loop work.
    await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
    assert peak == limit

    with pytest.raises(HTTPException) as saturated_error:
        await chat_api._run_chat_preflight(slow_preflight, limit)
    assert saturated_error.value.status_code == 503
    assert saturated_error.value.detail["code"] == "chat_preflight_busy"

    tasks[0].cancel()
    await asyncio.gather(tasks[0], return_exceptions=True)
    with pytest.raises(HTTPException) as cancelled_slot_error:
        await chat_api._run_chat_preflight(slow_preflight, limit + 1)
    assert cancelled_slot_error.value.status_code == 503
    with lock:
        # Cancelling the waiter cannot release its permit while its thread runs.
        assert active == limit
    assert peak == limit

    release.set()
    results = await asyncio.gather(*tasks[1:])
    assert sorted(results) == list(range(1, limit))


def test_session_message_cursor_pagination_and_private_image_delivery(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from app.core import database
    from app.main import app

    original = settings.db_path
    original_url = settings.database_url
    settings.db_path = Path(tmp_path) / "chat-page.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            created = client.post(
                "/chat/sessions",
                headers=headers,
                json={"title": "分页会话"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            messages = [
                {
                    "id": f"page-{index:03d}",
                    "role": "user",
                    "content": f"消息 {index}",
                    **(
                        {
                            "images": [
                                {
                                    "type": "image",
                                    "data": "cGFnZS1pbWFnZQ==",
                                    "mimeType": "image/png",
                                }
                            ]
                        }
                        if index == 0
                        else {}
                    ),
                }
                for index in range(45)
            ]
            saved = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={"messages": messages},
            )
            assert saved.status_code == 200
            recent = saved.json()
            assert [item["id"] for item in recent["messages"]] == [
                f"page-{index:03d}" for index in range(5, 45)
            ]
            assert recent["message_count"] == 45
            assert recent["has_more_messages"] is True
            assert recent["next_before_message_id"] == "page-005"

            statements: list[str] = []

            def capture_statement(
                _conn, _cursor, statement, _parameters, _context, _executemany
            ):
                statements.append(statement)

            engine = database._get_engine()
            event.listen(engine, "before_cursor_execute", capture_statement)
            try:
                older_response = client.get(
                    f"/chat/sessions/{session_id}",
                    headers=headers,
                    params={"before_message_id": "page-005"},
                )
            finally:
                event.remove(engine, "before_cursor_execute", capture_statement)
            assert older_response.status_code == 200
            older = older_response.json()
            assert [item["id"] for item in older["messages"]] == [
                f"page-{index:03d}" for index in range(5)
            ]
            assert older["has_more_messages"] is False
            assert older["next_before_message_id"] is None
            page_selects = [
                statement
                for statement in statements
                if "chat_messages.variant_active" in statement
                and "ORDER BY" in statement
            ]
            assert page_selects
            assert all("LIMIT" in statement for statement in page_selects)

            image = older["messages"][0]["images"][0]
            assert image == {
                "type": "image",
                "mimeType": "image/png",
                "url": (f"/api/chat/sessions/{session_id}/messages/page-000/images/0"),
            }
            assert "data" not in image
            image_response = client.get(
                image["url"].removeprefix("/api"),
                headers=headers,
            )
            assert image_response.status_code == 200
            assert image_response.headers["content-type"] == "image/png"
            assert image_response.content == b"page-image"

            around = client.get(
                f"/chat/sessions/{session_id}",
                headers=headers,
                params={"around_message_id": "page-020", "message_limit": 5},
            )
            assert around.status_code == 200
            assert [item["id"] for item in around.json()["messages"]] == [
                "page-018",
                "page-019",
                "page-020",
                "page-021",
                "page-022",
            ]
            assert (
                client.get(
                    f"/chat/sessions/{session_id}",
                    headers=headers,
                    params={
                        "before_message_id": "page-010",
                        "around_message_id": "page-020",
                    },
                ).status_code
                == 422
            )

            # A page-shaped replacement is an upsert unless the caller proves
            # that it holds the complete snapshot.
            partial = client.post(
                f"/chat/sessions/{session_id}/messages",
                headers=headers,
                json={
                    "replace": True,
                    "base_revision": recent["messages_revision"],
                    "messages": [
                        {
                            "id": "page-044",
                            "role": "user",
                            "content": "消息 44",
                        }
                    ],
                },
            )
            assert partial.status_code == 200
            assert partial.json()["message_count"] == 45
            assert (
                client.get(
                    image["url"].removeprefix("/api"),
                    headers=headers,
                ).status_code
                == 200
            )

            created_user = client.post(
                "/admin/users",
                headers=headers,
                json={
                    "username": "history-image-other",
                    "display_name": "图片越权测试",
                    "password": "History-image-123!",
                },
            )
            assert created_user.status_code == 201
            other_login = client.post(
                "/auth/login",
                json={
                    "username": "history-image-other",
                    "password": "History-image-123!",
                },
            )
            assert other_login.status_code == 200
            other_headers = {
                "Authorization": f"Bearer {other_login.json()['access_token']}"
            }
            assert (
                client.get(
                    image["url"].removeprefix("/api"),
                    headers=other_headers,
                ).status_code
                == 404
            )
    finally:
        settings.db_path = original
        settings.database_url = original_url
        database.reset_database(Path(tmp_path) / "chat-page.sqlite3")


def test_new_chat_turn_can_recall_own_prior_topic_before_answer(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from httpx import ASGITransport, AsyncClient

    from app.core import database
    from app.core.database import transaction
    from app.repositories import chat as chat_repo
    from app.services.pi_runtime.runner import PiRunResult

    old_path, old_url = settings.db_path, settings.database_url
    path = tmp_path / "chat-auto-recall.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "qa_vector_enabled", False)
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **kwargs):
            pass

        async def chat_async(self, _message, **kwargs):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://nexus.test"
            ) as client:
                response = await client.post(
                    "/internal/v1/agent-capabilities/history/recall",
                    headers={
                        "Authorization": f"Bearer {kwargs['capability_token']}",
                        "X-Nexus-Agent-ID": chat_api.PI_RUNTIME_AGENT_ID,
                    },
                )
            assert response.status_code == 200, response.text
            prior = response.json()["items"]
            assert len(prior) == 1
            assert prior[0]["source_user"] == "JEV 指日本脑炎病毒"
            return (
                PiRunResult(
                    reply="你此前明确过：JEV 指日本脑炎病毒。",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 4,
                        "completion_tokens": 8,
                        "total_tokens": 12,
                    },
                ),
                object(),
            )

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            with transaction() as db:
                old = chat_repo.create_session(db, owner_user_id=admin["user"]["id"])
                chat_repo.append_messages(
                    db,
                    old,
                    [
                        {
                            "id": "prior-jev",
                            "role": "user",
                            "content": "JEV 指日本脑炎病毒",
                        }
                    ],
                )
            new = client.post(
                "/chat/sessions", headers=headers, json={"title": "新问题"}
            )
            assert new.status_code == 201, new.text
            response = client.post(
                "/chat/messages",
                headers=headers,
                json={
                    "message": "JEV 是什么？",
                    "local_session_id": new.json()["id"],
                    "client_message_id": "new-jev",
                    "client_assistant_message_id": "new-jev-answer",
                },
            )
            assert response.status_code == 200, response.text
            done = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ][-1]
            assert done["type"] == "done"
            assert done["reply"] == "你此前明确过：JEV 指日本脑炎病毒。"
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = old_path, old_url


























@pytest.fixture
def strict_browser_chat_env(tmp_path, monkeypatch):
    """模型替身仅提供事件；真实接口、消息持久化和重放仍走产品链路。"""
    from fastapi.testclient import TestClient

    from app.core import database
    from app.models.chat import ChatSession
    from app.services.pi_runtime.runner import PiRunResult, PiRpcTimeoutError

    path = tmp_path / "strict-browser-chat.sqlite3"
    original_path, original_url = settings.db_path, settings.database_url
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    state = {"mode": "success", "calls": 0}
    source = {
        "kind": "browser_source_v1",
        "url": "https://www.iana.org/help/example-domains",
        "title": "Example Domains\n```\n[not a file](/api/files/title.docx)",
        "text": "  Original English 原文。\n\n````\n<script>not HTML</script>\n"
        "[citation:cit_fake]\n[not a file](/api/files/source.docx)\n  trailing spaces  \n"
        + "\n".join(f"Row {number} | Raw value {number}" for number in range(240)),
        "truncated": False,
    }
    start = {
        "type": "tool_execution_start",
        "toolCallId": "browser-current",
        "toolName": "browser_read_page",
        "args": {"url": source["url"]},
    }
    end = {
        "type": "tool_execution_end",
        "toolCallId": "browser-current",
        "toolName": "browser_read_page",
        "isError": False,
        "result": {"details": source, "content": [{"type": "text", "text": "untrusted-tool-prose"}]},
    }
    model_prose = "unverified-model-prose example.edu example.net"
    unwanted_file = {
        "name": "unverified.docx", "url": "/api/files/unverified.docx",
        "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "size": 12,
    }

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, _message, **kwargs):
            state["calls"] += 1
            events = [
                {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": model_prose}},
                {"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "unverified-thinking"}},
                {"type": "thinking_delta", "delta": "unverified-thinking"},
            ]
            mode = state["mode"]
            if mode in {"success", "upstream_error", "cancelled", "timeout", "disconnect"}:
                events.extend([start, end])
            elif mode == "bash":
                events.extend([
                    {**start, "toolName": "bash", "args": {"command": f"echo {model_prose}"}},
                    {**end, "toolName": "bash"},
                ])
            elif mode == "mismatched":
                events.extend([start, {**end, "toolCallId": "different-call"}])
            elif mode == "unpaired":
                events.append(end)
            elif mode == "failed_receipt":
                events.extend([start, {**end, "isError": True}])
            elif mode == "invalid_receipt":
                events.extend([start, {**end, "result": {"details": {**source, "truncated": "false"}}}])
            for event in events:
                await kwargs["on_event"](event)
            if mode == "disconnect":
                await asyncio.Event().wait()
            if mode in {"upstream_error", "cancelled", "timeout"}:
                error = (
                    PiRpcTimeoutError(model_prose)
                    if mode == "timeout"
                    else PiRpcError(model_prose, code=mode)
                )
                error.partial_reply = model_prose
                error.generated_files = [unwanted_file]
                error.token_usage = {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}
                raise error
            return PiRunResult(
                reply=model_prose, stats={}, state={},
                # 历史/最终裁剪事件不能充当本轮证据；成功也不依赖 result.events。
                events=[start, end] if mode == "historical" else [],
                token_usage={"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
                generated_files=[unwanted_file],
            ), object()

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    try:
        with TestClient(app) as client:
            admin = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {admin['access_token']}"}
            session_id = client.post("/chat/sessions", headers=headers, json={"title": "网页原文"}).json()["id"]
            with database.SessionLocal() as db:
                db.get(ChatSession, session_id).title_finalized = True
                db.commit()
            yield SimpleNamespace(
                client=client, headers=headers, session_id=session_id,
                owner_id=admin["user"]["id"], state=state, source=source,
            )
    finally:
        database.reset_database(path)
        settings.db_path, settings.database_url = original_path, original_url


def _strict_browser_request(env, **changes):
    payload = {
        "message": "/skill:agent-browser\n读取网页，只返回原文",
        "selected_skill": "agent-browser",
        "local_session_id": env.session_id,
        "client_message_id": "browser-user",
        "client_assistant_message_id": "browser-assistant",
    }
    payload.update(changes)
    return env.client.post("/chat/messages", headers=env.headers, json=payload)


@pytest.mark.parametrize("selected_skill", ["agent-browser", None])
def test_strict_browser_literal_source_survives_stream_history_and_replay(
    strict_browser_chat_env, selected_skill,
):
    from app.core.database import SessionLocal
    from app.models.writing import WritingProject

    env = strict_browser_chat_env
    response = _strict_browser_request(env, selected_skill=selected_skill)
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    done = events[-1]
    assert done["type"] == "done" and done["server_persisted"] is True
    assert done["generated_files"] == []
    assert done["token_usage"]["total_tokens"] == 7
    assert "writing_project_id" not in done
    assert f"`````text\n{env.source['text']}\n`````" in done["reply"]
    assert env.source["title"] in done["reply"] and env.source["url"] in done["reply"]
    assert not any(
        event.get("item", {}).get("type") in {"PiThinkingChunkEvent", "PiThinkingEvent", "ModelClientStreamingChunkEvent"}
        for event in events[:-1]
    )
    snapshot = env.client.get(f"/chat/sessions/{env.session_id}", headers=env.headers)
    assistant = next(item for item in snapshot.json()["messages"] if item["role"] == "assistant")
    assert assistant["content"] == done["reply"]
    assert assistant.get("thinking") in (None, "")
    assert assistant["references"] == []
    assert not any(part["type"] == "file" for part in assistant["parts"])
    assert any(part.get("tool_name") == "browser_read_page" and part["status"] == "done" for part in assistant["parts"])
    replay = _strict_browser_request(env, selected_skill=selected_skill)
    assert replay.status_code == 200, replay.text
    replay_done = json.loads([line[6:] for line in replay.text.splitlines() if line.startswith("data: ")][-1])
    assert replay_done["reply"] == done["reply"] and replay_done["generated_files"] == []
    assert env.state["calls"] == 1
    for surface in (response.text, snapshot.text, replay.text):
        assert "unverified-model-prose" not in surface
        assert "unverified-thinking" not in surface
        assert "untrusted-tool-prose" not in surface
        assert "/api/files/unverified.docx" not in surface
    with SessionLocal() as db:
        assert db.scalar(select(WritingProject.id)) is None


@pytest.mark.parametrize(
    "mode",
    ["bash", "mismatched", "unpaired", "failed_receipt", "invalid_receipt", "historical",
     "upstream_error", "cancelled", "timeout"],
)
def test_strict_browser_fails_closed_without_trusted_current_receipt(
    strict_browser_chat_env, mode,
):
    from app.services.chat_browser_delivery import SAFE_FAILURE

    env = strict_browser_chat_env
    env.state["mode"] = mode
    response = _strict_browser_request(env)
    assert response.status_code == 200, response.text
    terminal = json.loads([line[6:] for line in response.text.splitlines() if line.startswith("data: ")][-1])
    assert terminal["type"] == "error" and terminal["server_persisted"] is True
    assert terminal.get("generated_files", []) == []
    assert SAFE_FAILURE in terminal["detail"]
    snapshot = env.client.get(f"/chat/sessions/{env.session_id}", headers=env.headers)
    assistant = next(item for item in snapshot.json()["messages"] if item["role"] == "assistant")
    assert assistant["content"] == SAFE_FAILURE
    assert assistant["usage"]["status"] != "completed"
    assert assistant.get("thinking") in (None, "")
    for surface in (response.text, snapshot.text):
        assert "unverified-model-prose" not in surface
        assert "unverified-thinking" not in surface
        assert "untrusted-tool-prose" not in surface
        assert "/api/files/unverified.docx" not in surface
        assert "Original English" not in surface


@pytest.mark.anyio
async def test_strict_browser_disconnect_never_persists_model_text(strict_browser_chat_env):
    from app.core.database import SessionLocal
    from app.models.auth import User
    from app.services.chat_browser_delivery import SAFE_FAILURE

    env = strict_browser_chat_env
    env.state["mode"] = "disconnect"
    with SessionLocal() as db:
        response = await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message="/skill:agent-browser\n读取网页",
                selected_skill="agent-browser", local_session_id=env.session_id,
                client_message_id="browser-disconnect-user",
                client_assistant_message_id="browser-disconnect-assistant",
            ),
            db.get(User, env.owner_id),
        )
    iterator = response.body_iterator.__aiter__()
    while True:
        chunk = await iterator.__anext__()
        payload = json.loads(chunk.decode().removeprefix("data: ").strip())
        if payload.get("type") == "event":
            break
    await iterator.aclose()
    assert await chat_api.drain_background_chat_tasks(2.0) == 0
    snapshot = env.client.get(f"/chat/sessions/{env.session_id}", headers=env.headers)
    assistant = next(item for item in snapshot.json()["messages"] if item["role"] == "assistant")
    assert assistant["content"] == SAFE_FAILURE
    assert assistant["usage"]["status"] == "failed"
    assert assistant["usage"]["error_code"] == "cancelled"
    assert "unverified-model-prose" not in snapshot.text
    assert "unverified-thinking" not in snapshot.text


@pytest.mark.parametrize("tampering", ["source", "message", "unsigned"])
def test_strict_browser_replay_rejects_client_forged_receipts(
    strict_browser_chat_env, tampering,
):
    import base64

    from app.services.chat_browser_delivery import BrowserSourceError, replay

    env = strict_browser_chat_env
    response = _strict_browser_request(env)
    assert response.status_code == 200, response.text
    snapshot = env.client.get(f"/chat/sessions/{env.session_id}", headers=env.headers).json()
    assistant = next(item for item in snapshot["messages"] if item["role"] == "assistant")
    part = next(item for item in assistant["parts"] if item.get("tool_name") == "browser_read_page")
    receipt = json.loads(part["output_text"])
    scope = (env.owner_id, env.session_id, assistant["id"])
    if tampering == "source":
        receipt["data"] = base64.urlsafe_b64encode(json.dumps(
            {**env.source, "text": "a client-forged source"}, ensure_ascii=False,
        ).encode()).decode()
    elif tampering == "message":
        scope = (env.owner_id, env.session_id, "different-assistant")
    else:
        receipt = env.source
    part["output_text"] = json.dumps(receipt, ensure_ascii=False)
    with pytest.raises(BrowserSourceError):
        replay(assistant, scope)


def test_strict_browser_rejects_retired_template_before_model(strict_browser_chat_env):
    env = strict_browser_chat_env
    response = _strict_browser_request(env, template_revision_id="missing-template")
    assert response.status_code == 410
    assert "演示模板库已停用" in response.text
    assert env.state["calls"] == 0


def test_strict_browser_signed_replay_preserves_security_filtered_source(strict_browser_chat_env):
    env = strict_browser_chat_env
    env.source["text"] = (
        "Authorization: Bearer browser-auth-value\n"
        "Plain original fact.\nBearer browser-bearer-value\n"
        "api_key: browser-key-value"
    )
    response = _strict_browser_request(env)
    assert response.status_code == 200, response.text
    done = json.loads([line[6:] for line in response.text.splitlines() if line.startswith("data: ")][-1])
    assert done["type"] == "done"
    assert (
        "Authorization: [REDACTED]\nPlain original fact.\n"
        "Bearer [REDACTED]\napi_key: [REDACTED]"
    ) in done["reply"]
    snapshot = env.client.get(f"/chat/sessions/{env.session_id}", headers=env.headers)
    replay = _strict_browser_request(env)
    assert replay.status_code == 200, replay.text
    replay_done = json.loads([line[6:] for line in replay.text.splitlines() if line.startswith("data: ")][-1])
    assert replay_done["reply"] == done["reply"]
    for surface in (response.text, snapshot.text, replay.text):
        assert "browser-auth-value" not in surface
        assert "browser-bearer-value" not in surface
        assert "browser-key-value" not in surface


def test_strict_browser_cjk_source_fits_signed_receipt(strict_browser_chat_env):
    import base64

    from app.services.chat_browser_delivery import replay

    env = strict_browser_chat_env
    env.source["text"] = "中" * 20000
    response = _strict_browser_request(env)
    assert response.status_code == 200, response.text
    done = json.loads([line[6:] for line in response.text.splitlines() if line.startswith("data: ")][-1])
    assert done["type"] == "done" and done["server_persisted"] is True
    snapshot = env.client.get(f"/chat/sessions/{env.session_id}", headers=env.headers).json()
    assistant = next(item for item in snapshot["messages"] if item["role"] == "assistant")
    part = next(item for item in assistant["parts"] if item.get("tool_name") == "browser_read_page")
    assert len(part["output_text"].encode("utf-8")) <= 80000
    receipt = json.loads(part["output_text"])
    source = json.loads(base64.urlsafe_b64decode(receipt["data"]))
    assert source["truncated"] is True
    assert 0 < len(source["text"]) < 20000
    assert source["text"] == env.source["text"][:len(source["text"])]
    assert "截断内容" in done["reply"]
    assert replay(assistant, (env.owner_id, env.session_id, assistant["id"]))["content"] == done["reply"]
    repeated = _strict_browser_request(env)
    replay_done = json.loads([line[6:] for line in repeated.text.splitlines() if line.startswith("data: ")][-1])
    assert replay_done["reply"] == done["reply"]
    assert env.state["calls"] == 1


@pytest.mark.parametrize(
    "ending,consume_before_ack",
    [
        ("complete", False), ("complete", True), ("error", True),
        ("empty_error", True),
        ("cancelled", False), ("partial_steer", True), ("same_text", False),
        ("native_extra", False), ("priority", False),
    ],
)
def test_runtime_reply_segments_are_durable_and_never_overwrite(
    tmp_path, monkeypatch, ending, consume_before_ack,
):
    from pathlib import Path
    from fastapi.testclient import TestClient
    from app.core import database
    from app.models.chat import ChatMessage
    from app.services.pi_runtime.runner import PiRunResult

    original, original_url = settings.db_path, settings.database_url
    settings.db_path = Path(tmp_path) / "reply-segments.sqlite3"
    settings.database_url = f"sqlite:///{settings.db_path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(settings, "personal_memory_enabled", False)
    monkeypatch.setattr(settings, "media_intent_mode", "off")
    monkeypatch.setattr(
        chat_api, "resolve_model_endpoint",
        lambda *_args, **_kwargs: SimpleNamespace(id="reply-test", provider="controlled", model="chat-model"),
    )
    sent_controls: list[str] = []
    pending_controls: list[str] = []
    state: dict[str, Any] = {}
    headers: dict[str, str] = {}
    session_id = ""

    async def consume(identity: str):
        event = {
            "type": "message_start",
            "message": {"role": "user", "content": [{"type": "text", "text": "同一追加"}],
                        "nexusClientMessageId": identity},
        }
        await state["emit"](event)
        await state["emit"](event)

    async def answer(text: str, timestamp: int, *, complete: bool = True):
        await state["emit"]({"type": "message_start", "message": {
            "role": "assistant", "timestamp": timestamp, "content": [],
        }})
        await state["emit"]({"type": "message_update", "assistantMessageEvent": {
            "type": "text_delta", "delta": text,
        }})
        if complete:
            event = {"type": "message_end", "message": {
                "role": "assistant", "timestamp": timestamp, "stopReason": "stop",
                "content": [{"type": "text", "text": text}],
            }}
            await state["emit"](event)
            await state["emit"](event)

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **kwargs):
            pass

        async def steer_async(self, message, *, client_message_id, **kwargs):
            sent_controls.append(client_message_id)
            if consume_before_ack:
                await consume(client_message_id)
            else:
                pending_controls.append(client_message_id)

        follow_up_async = steer_async

        async def chat_async(self, message, **kwargs):
            state["emit"] = kwargs["on_event"]
            state["runs"] = int(state.get("runs", 0)) + 1
            await answer("第一份完整答案" if ending != "partial_steer" else "第一份已显示部分", 101,
                         complete=ending != "partial_steer")
            if ending != "partial_steer":
                with database.SessionLocal() as db:
                    # 证明存储不依赖 SSE 消费者，后续输入尚未进入模型时第一份已经提交。
                    first = db.get(ChatMessage, "reply-root")
                    assert first is not None and first.content == "第一份完整答案"
                    assert first.usage is None
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
                    pending = await api.post("/chat/messages", headers=headers, json={
                        "message": "初始问题", "local_session_id": session_id,
                        "client_message_id": "user-root", "client_assistant_message_id": "reply-root",
                    })
                    assert pending.status_code == 409, pending.text
                    assert pending.json()["detail"]["code"] == "chat_turn_running"
            if ending != "native_extra":
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
                    mode = "steer" if ending == "partial_steer" else "follow-up"
                    for identity in (["control-one", "control-two"] if ending in {"same_text", "priority"} else ["control-one"]):
                        if ending == "priority":
                            mode = "steer" if identity == "control-two" else "follow-up"
                        request = {"message": "同一追加", "client_message_id": identity,
                                   "agent_session_id": kwargs["session_id"],
                                   "client_assistant_message_id": "reply-root"}
                        response = await api.post(
                            f"/chat/sessions/{session_id}/{mode}", headers=headers, json=request,
                        )
                        assert response.status_code == 200, response.text
                        # 相同控制 ID 重放只确认排队，不再次送入运行时。
                        duplicate = await api.post(
                            f"/chat/sessions/{session_id}/{mode}", headers=headers, json=request,
                        )
                        assert duplicate.status_code == 200, duplicate.text
                if pending_controls:
                    await consume(pending_controls.pop(-1 if ending == "priority" else 0))
            if ending in {"error", "cancelled", "empty_error"}:
                if ending != "empty_error":
                    await answer("第二份已显示部分", 102, complete=False)
                error = PiRpcError("controlled runtime failure", code=(
                    "cancelled" if ending == "cancelled" else "upstream_error"
                ))
                error.token_usage = {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}
                error.partial_reply = "第一份完整答案"
                raise error
            await answer("第二份完整答案", 102)
            if ending in {"same_text", "priority"}:
                await consume(pending_controls.pop(0))
                await answer("第三份完整答案", 103)
            return PiRunResult(
                reply="第三份完整答案" if ending in {"same_text", "priority"} else "第二份完整答案",
                stats={}, state={}, events=[],
                token_usage={"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            ), object()

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    try:
        with TestClient(app) as client:
            owner = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {owner['access_token']}"}
            session_id = client.post("/chat/sessions", headers=headers, json={"title": "分段回答"}).json()["id"]
            request = {
                "message": "初始问题", "local_session_id": session_id,
                "client_message_id": "user-root", "client_assistant_message_id": "reply-root",
            }
            response = client.post("/chat/messages", headers=headers, json=request)
            assert response.status_code == 200, response.text
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            snapshot = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
            assistants = [message for message in snapshot["messages"] if message["role"] == "assistant"]
            assert len(assistants) == (3 if ending in {"same_text", "priority"} else 2)
            assert assistants[0]["id"] == "reply-root"
            assert assistants[0]["content"] == (
                "第一份已显示部分" if ending == "partial_steer" else "第一份完整答案"
            )
            assert assistants[0]["metadata"]["pi_incomplete"] is (ending == "partial_steer")
            assert all(message["metadata"]["assistant_message_frozen"] for message in assistants)
            if ending == "empty_error":
                assert assistants[1]["content"] != "第一份完整答案"
                assert assistants[1]["metadata"]["pi_incomplete"] is True
            else:
                assert assistants[1]["content"] == (
                    "第二份已显示部分" if ending in {"error", "cancelled"} else "第二份完整答案"
                )
            assert all(not (message["usage"] or {}).get("total_tokens") for message in assistants[:-1])
            assert sum(message["usage"]["total_tokens"] for message in assistants if message["usage"]) == 7
            assert len(sent_controls) == (0 if ending == "native_extra" else 2 if ending in {"same_text", "priority"} else 1)
            assert len([event for event in events if event["type"] == "input_consumed"]) == len(sent_controls)
            complete = [event for event in events if event["type"] == "assistant_message" and event["phase"] == "complete"]
            assert {event["message"]["id"] for event in complete} == {message["id"] for message in assistants}
            assert complete[0]["message"]["content"] == assistants[0]["content"]
            assert complete[0]["server_persisted"] is True
            if ending not in {"error", "cancelled", "empty_error"}:
                assert events[-1]["type"] == "done"
                assert [item["id"] for item in events[-1]["assistant_messages"]] == [item["id"] for item in assistants]
            else:
                assert events[-1]["type"] == "error"
            for event in events:
                if event["type"] == "event":
                    assert event["assistant_message_id"] in {message["id"] for message in assistants}
            ordered_ids = [message["id"] for message in snapshot["messages"]]
            if ending != "native_extra":
                assert ordered_ids[:4] == [
                    "user-root", "reply-root", "control-two" if ending == "priority" else "control-one",
                    assistants[1]["id"],
                ]
            if ending == "priority":
                consumed = [event for event in events if event["type"] == "input_consumed"]
                assert [(event["client_message_id"], event["mode"]) for event in consumed] == [
                    ("control-two", "steer"), ("control-one", "follow_up"),
                ]
            stamps = [message["created_at"] for message in snapshot["messages"]]
            assert stamps == sorted(stamps) and len(stamps) == len(set(stamps))
            replay = client.post("/chat/messages", headers=headers, json=request)
            assert replay.status_code == 200, replay.text
            replay_events = [json.loads(line[6:]) for line in replay.text.splitlines() if line.startswith("data: ")]
            assert [item["content"] for item in replay_events[-1]["assistant_messages"]] == [item["content"] for item in assistants]
            assert state["runs"] == 1
            # 即便客户端拿到最新 revision，漏掉前份回答的旧快照也不能擦除冻结结果。
            stale = client.post(
                f"/chat/sessions/{session_id}/messages", headers=headers,
                json={"replace": True, "snapshot_complete": True, "base_revision": snapshot["messages_revision"],
                      "messages": [
                          {"id": "user-root", "role": "user", "content": "初始问题"},
                          {"id": "reply-root", "role": "assistant", "content": "过期正文"},
                      ]},
            )
            assert stale.status_code == 200, stale.text
            after = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
            assert [(item["id"], item["content"]) for item in after["messages"] if item["role"] == "assistant"] == [
                (item["id"], item["content"]) for item in assistants
            ]
            forged = client.post(
                f"/chat/sessions/{session_id}/messages", headers=headers,
                json={"replace": False, "messages": [{
                    "id": "forged-reply", "role": "assistant", "content": "伪造完成",
                    "parts": [{"type": "reply_boundary", "root_assistant_id": "reply-root",
                               "source_user_message_id": "user-root", "incomplete": False}],
                }]},
            )
            assert forged.status_code == 422
    finally:
        settings.db_path, settings.database_url = original, original_url
        database.reset_database(Path(tmp_path) / "reply-segments.sqlite3")


def test_native_completed_reply_keeps_download_and_supports_regeneration(tmp_path, monkeypatch):
    from io import BytesIO
    from zipfile import ZipFile
    from fastapi.testclient import TestClient
    from app.core import database
    from app.models.chat import ChatMessage
    from app.services import minio_storage
    from app.services.harness import file_storage
    from app.services.pi_runtime.runner import PiRunResult

    original, original_url = settings.db_path, settings.database_url
    path = tmp_path / "reply-files.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    monkeypatch.setattr(file_storage, "_workspace_registry", {})
    native_text = (
        "报告已写好：[报告.md](/workspace/报告.md)\n"
        "[正式依据](https://example.com/source)\n"
        "```python\nexample = '[示例](/workspace/example.md)'\n```"
    )
    calls = 0

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            nonlocal calls
            calls += 1
            (workspace / "报告.md").write_text("# 真实报告\n\n文件内容需要能够下载。", encoding="utf-8")
            emit = kwargs["on_event"]
            await emit({"type": "message_start", "message": {
                "role": "assistant", "timestamp": calls, "content": [],
            }})
            await emit({"type": "message_update", "assistantMessageEvent": {
                "type": "text_delta", "delta": native_text,
            }})
            await emit({"type": "message_end", "message": {
                "role": "assistant", "timestamp": calls, "stopReason": "stop",
                "content": [{"type": "text", "text": native_text}],
            }})
            # 真实文件转换、登记与下载路由；扫描确实发生在原生完成事件之后。
            files = file_storage.scan_and_upload_workspace(kwargs["session_id"], workspace)
            reply = file_storage.rewrite_markdown_paths_to_docx(native_text, files)
            return PiRunResult(
                reply=reply, stats={}, state={}, events=[], generated_files=files,
                token_usage={"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            ), object()

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    try:
        with TestClient(app) as client:
            owner = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {owner['access_token']}"}
            session_id = client.post("/chat/sessions", headers=headers, json={"title": "报告"}).json()["id"]
            request = {
                "message": "交付报告文件", "local_session_id": session_id,
                "client_message_id": "file-user", "client_assistant_message_id": "file-answer",
            }
            response = client.post("/chat/messages", headers=headers, json=request)
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert events[-1]["type"] == "done", events[-1]
            early = next(event["message"] for event in events if event["type"] == "assistant_message" and event["phase"] == "complete")
            final = events[-1]["assistant_messages"][0]
            assert final["content"] == early["content"]
            assert "[报告.md](/workspace/报告.md)" not in final["content"]
            assert "[正式依据](https://example.com/source)" in final["content"]
            assert "example = '[示例](/workspace/example.md)'" in final["content"]
            cards = [part for part in final["parts"] if part["type"] == "file"]
            assert len(cards) == 1 and cards[0]["name"] == "报告.docx"
            downloaded = client.get(cards[0]["href"].removeprefix("/api"), headers=headers)
            assert downloaded.status_code == 200
            assert len(downloaded.content) == cards[0]["size"]
            with ZipFile(BytesIO(downloaded.content)) as archive:
                assert "文件内容需要能够下载" in archive.read("word/document.xml").decode()
            replay = client.post("/chat/messages", headers=headers, json=request)
            assert replay.status_code == 200, replay.text
            assert calls == 1
            regenerated = client.post(
                "/chat/messages", headers=headers,
                json={**request, "client_assistant_message_id": "file-regenerated",
                      "regenerate_from_message_id": "file-answer"},
            )
            regenerated_events = [json.loads(line[6:]) for line in regenerated.text.splitlines() if line.startswith("data: ")]
            assert regenerated.status_code == 200, regenerated.text
            assert regenerated_events[-1]["server_persisted"] is True
            history = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
            active = [item for item in history["messages"] if item["role"] == "assistant"]
            assert len(active) == 1 and active[0]["id"] == "file-regenerated"
            assert active[0]["metadata"]["assistant_message_frozen"] is True
            with database.SessionLocal() as db:
                original_answer = db.get(ChatMessage, "file-answer")
                new_answer = db.get(ChatMessage, "file-regenerated")
                assert original_answer.variant_active is False and new_answer.variant_active is True
                assert original_answer.variant_group_id == new_answer.variant_group_id
                assert original_answer.content == early["content"]
            assert calls == 2
    finally:
        settings.db_path, settings.database_url = original, original_url
        database.reset_database(path)


@pytest.mark.parametrize("second_valid", [True, False])
def test_strict_browser_freezes_each_verified_reply_without_raw_model_text(
    strict_browser_chat_env, monkeypatch, second_valid,
):
    from app.services.chat_browser_delivery import SAFE_FAILURE
    from app.services.pi_runtime.runner import PiRunResult

    env = strict_browser_chat_env
    callback = None
    calls = 0
    monkeypatch.setattr(settings, "media_intent_mode", "off")
    monkeypatch.setattr(
        chat_api, "resolve_model_endpoint",
        lambda *_args, **_kwargs: SimpleNamespace(id="reply-test", provider="controlled", model="chat-model"),
    )

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **kwargs):
            pass

        async def follow_up_async(self, message, *, client_message_id, **kwargs):
            await callback({"type": "message_start", "message": {
                "role": "user", "nexusClientMessageId": client_message_id,
                "content": [{"type": "text", "text": message}],
            }})

        async def chat_async(self, message, **kwargs):
            nonlocal callback, calls
            calls += 1
            callback = kwargs["on_event"]
            for number in (1, 2):
                source = {**env.source, "text": f"已核验第{number}份网页原文"}
                await callback({"type": "message_start", "message": {
                    "role": "assistant", "timestamp": number, "content": [],
                }})
                await callback({"type": "tool_execution_start", "toolCallId": f"web-{number}",
                                "toolName": "browser_read_page", "args": {"url": source["url"]}})
                await callback({"type": "tool_execution_end", "toolCallId": f"web-{number}",
                                "toolName": "browser_read_page", "isError": False,
                                "result": {"details": source if number == 1 or second_valid else {},
                                           "content": [{"type": "text", "text": "不可信工具输出"}]}})
                await callback({"type": "message_end", "message": {
                    "role": "assistant", "timestamp": number, "stopReason": "stop",
                    "content": [{"type": "text", "text": "不可信模型改写"}],
                }})
                if number == 1:
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
                        accepted = await api.post(
                            f"/chat/sessions/{env.session_id}/follow-up", headers=env.headers,
                            json={"message": "再读一份", "client_message_id": "browser-control",
                                  "agent_session_id": kwargs["session_id"],
                                  "client_assistant_message_id": "browser-assistant"},
                        )
                        assert accepted.status_code == 200, accepted.text
            return PiRunResult(
                reply="不可信模型改写", stats={}, state={}, events=[],
                token_usage={"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            ), object()

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    response = _strict_browser_request(env)
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert "不可信模型改写" not in response.text and "不可信工具输出" not in response.text
    snapshots = events[-1]["assistant_messages"]
    assert len(snapshots) == 2
    assert "已核验第1份网页原文" in snapshots[0]["content"]
    if second_valid:
        assert "已核验第2份网页原文" in snapshots[1]["content"]
        assert "已核验第1份网页原文" not in snapshots[1]["content"]
    else:
        assert snapshots[1]["content"] == SAFE_FAILURE
    history = env.client.get(f"/chat/sessions/{env.session_id}", headers=env.headers).json()
    replies = [item for item in history["messages"] if item["role"] == "assistant"]
    assert [item["content"] for item in replies] == [item["content"] for item in snapshots]
    replay = _strict_browser_request(env)
    if second_valid:
        replay_events = [json.loads(line[6:]) for line in replay.text.splitlines() if line.startswith("data: ")]
        assert replay.status_code == 200, replay.text
        assert len(replay_events[-1]["assistant_messages"]) == 2
    else:
        assert replay.status_code == 409, replay.text
        assert replay.json()["detail"] == SAFE_FAILURE
        preserved = env.client.get(f"/chat/sessions/{env.session_id}", headers=env.headers)
        assert preserved.status_code == 200, preserved.text
        first = next(message for message in preserved.json()["messages"] if message["id"] == replies[0]["id"])
        assert first == replies[0]
    assert calls == 1


@pytest.mark.parametrize("case", ["empty_control", "fallback_saved", "control_order_retry", "two_empty_controls"])
def test_reply_segment_empty_control_and_browser_fallback_races(tmp_path, monkeypatch, case):
    from fastapi.testclient import TestClient
    from app.core import database
    from app.services.pi_runtime.runner import PiRunResult

    original, original_url = settings.db_path, settings.database_url
    path = tmp_path / "reply-races.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(settings, "personal_memory_enabled", False)
    monkeypatch.setattr(settings, "media_intent_mode", "off")
    monkeypatch.setattr(
        chat_api, "resolve_model_endpoint",
        lambda *_args, **_kwargs: SimpleNamespace(id="reply-test", provider="controlled", model="chat-model"),
    )
    persist = chat_api._persist_server_chat_messages_with_retry
    failed_once = False
    emit = None
    session_id = ""
    headers = {}
    control_ids = ["race-control"]
    if case == "two_empty_controls":
        control_ids.append("race-control-second")

    def persist_with_one_failure(**kwargs):
        nonlocal failed_once
        if (
            case == "control_order_retry" and len(kwargs["payloads"]) == 1
            and kwargs["payloads"][0].get("id") == "race-control"
        ):
            raise RuntimeError("isolated input persistence failure")
        if case == "fallback_saved" and not failed_once and any(
            payload.get("role") == "assistant" for payload in kwargs["payloads"]
        ):
            failed_once = True
            raise RuntimeError("isolated persistence failure")
        return persist(**kwargs)

    monkeypatch.setattr(chat_api, "_persist_server_chat_messages_with_retry", persist_with_one_failure)

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **kwargs):
            pass

        async def steer_async(self, message, *, client_message_id, **kwargs):
            await emit({"type": "message_start", "message": {
                "role": "user", "nexusClientMessageId": client_message_id,
                "content": [{"type": "text", "text": message}],
            }})
            # 消费已发生，但发送协程没有拿到 RPC ACK；不能把已消费输入丢掉。
            raise RuntimeError("control ACK lost")

        async def chat_async(self, message, **kwargs):
            nonlocal emit
            emit = kwargs["on_event"]
            if case in {"empty_control", "control_order_retry", "two_empty_controls"}:
                await emit({"type": "message_start", "message": {
                    "role": "assistant", "timestamp": 1, "content": [],
                }})
                await emit({"type": "tool_execution_start", "toolCallId": "read-first",
                            "toolName": "read", "args": {"path": "data.txt"}})
                await emit({"type": "tool_execution_end", "toolCallId": "read-first",
                            "toolName": "read", "isError": False, "result": "读取完成"})
                await emit({"type": "message_end", "message": {
                    "role": "assistant", "timestamp": 1, "stopReason": "toolUse", "content": [],
                }})
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
                    for control_id in control_ids:
                        accepted = await api.post(
                            f"/chat/sessions/{session_id}/steer", headers=headers,
                            json={"message": "按新要求回答", "client_message_id": control_id,
                                  "agent_session_id": kwargs["session_id"],
                                  "client_assistant_message_id": "race-answer"},
                        )
                        assert accepted.status_code == 200, accepted.text
                        assert accepted.json()["queued"] is True
            await emit({"type": "message_start", "message": {
                "role": "assistant", "timestamp": 2, "content": [],
            }})
            await emit({"type": "message_update", "assistantMessageEvent": {
                "type": "text_delta", "delta": "最终单份答复",
            }})
            await emit({"type": "message_end", "message": {
                "role": "assistant", "timestamp": 2, "stopReason": "stop",
                "content": [{"type": "text", "text": "最终单份答复"}],
            }})
            if case == "fallback_saved":
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
                    saved = await api.post(
                        f"/chat/sessions/{session_id}/messages", headers=headers,
                        json={"replace": False, "messages": [{
                            "id": "race-answer", "role": "assistant", "content": "最终单份答复",
                            "parts": [{"type": "content", "text": "最终单份答复"}],
                            "metadata": {"assistant_message_frozen": True},
                        }]},
                    )
                    assert saved.status_code == 200, saved.text
                    record = next(item for item in saved.json()["messages"] if item["id"] == "race-answer")
                    assert not record["metadata"].get("assistant_message_frozen")
            return PiRunResult(
                reply="最终单份答复", stats={}, state={}, events=[],
                token_usage={"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            ), object()

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    try:
        with TestClient(app) as client:
            owner = bootstrap_admin(client)
            headers = {"Authorization": f"Bearer {owner['access_token']}"}
            session_id = client.post("/chat/sessions", headers=headers, json={"title": "竞态"}).json()["id"]
            response = client.post("/chat/messages", headers=headers, json={
                "message": "初始要求", "local_session_id": session_id,
                "client_message_id": "race-user", "client_assistant_message_id": "race-answer",
            })
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert events[-1]["type"] == "done" and events[-1]["server_persisted"] is True
            assert len(events[-1]["assistant_messages"]) == 1
            complete = [event for event in events if event["type"] == "assistant_message" and event["phase"] == "complete"]
            assert {event["message"]["content"] for event in complete} == {"最终单份答复"}
            if case == "fallback_saved":
                assert complete[0]["server_persisted"] is False
                assert complete[-1]["server_persisted"] is True
            history = client.get(f"/chat/sessions/{session_id}", headers=headers).json()
            replies = [message for message in history["messages"] if message["role"] == "assistant"]
            assert len(replies) == 1 and replies[0]["id"] == "race-answer"
            assert replies[0]["metadata"]["assistant_message_frozen"] is True
            assert replies[0]["usage"]["total_tokens"] == 7
            if case in {"empty_control", "control_order_retry", "two_empty_controls"}:
                assert [message["id"] for message in history["messages"]] == [
                    "race-user", *control_ids, "race-answer",
                ]
                assert replies[0]["metadata"]["source_user_message_id"] == control_ids[-1]
                assert any(part.get("tool_call_id") == "read-first" for part in replies[0]["parts"])
                consumed = [event for event in events if event["type"] == "input_consumed"]
                assert len(consumed) == (2 if case in {"control_order_retry", "two_empty_controls"} else 1)
                assert consumed[-1]["user_created_at"] == history["messages"][-2]["created_at"]
                assert consumed[-1]["assistant_created_at"] == replies[0]["created_at"]
                if case == "control_order_retry":
                    assert "messages_revision" not in consumed[0]
                    assert consumed[-1]["messages_revision"] > 0
                if case == "two_empty_controls":
                    stale = client.post(
                        f"/chat/sessions/{session_id}/messages", headers=headers,
                        json={
                            "replace": True, "snapshot_complete": True,
                            "base_revision": history["messages_revision"],
                            "messages": [
                                {"id": "race-user", "role": "user", "content": "初始要求"},
                                {"id": "race-answer", "role": "assistant", "content": "最终单份答复"},
                            ],
                        },
                    )
                    assert stale.status_code == 200, stale.text
                    assert [message["id"] for message in stale.json()["messages"]] == [
                        "race-user", *control_ids, "race-answer",
                    ]
                    assert all(
                        message["content"] == "按新要求回答"
                        for message in stale.json()["messages"] if message["id"] in control_ids
                    )
    finally:
        settings.db_path, settings.database_url = original, original_url
        database.reset_database(path)


def test_late_reply_files_use_full_declared_paths_not_basenames():
    from app.services.chat_reply_segments import ChatReplySegments, ReplySegment

    replies = ChatReplySegments("root-answer", {"id": "root-user", "role": "user", "content": "生成文件"})
    first = replies.current
    first.native_content = "[第一份](/workspace/a/report.md)\n`[第一份](/workspace/a/report.md)`\n`[代码示例](/workspace/c/report.md)`"
    second = ReplySegment("second-answer", "second-user", replies.tick())
    second.native_content = "[第二份](/workspace/b/report.md)\n文字中提及 report.md 不代表文件归属。"
    replies.current = second
    replies.segments.append(second)
    first_file = {"path": "a/report.docx", "evidence_sources": []}
    second_file = {"path": "b/report.docx", "evidence_sources": []}
    unknown_file = {"path": "c/report.docx", "evidence_sources": []}
    grouped = replies.files_by_reply([first_file, second_file, unknown_file])
    assert grouped == {"root-answer": [first_file], "second-answer": [second_file, unknown_file]}


def test_late_verified_file_sources_complete_frozen_reply_citations(citation_client, tmp_path, monkeypatch):
    from app.core.database import SessionLocal
    from app.models.auth import User
    from app.services import minio_storage
    from app.services.evidence_export import validate_evidence_sources
    from app.services.harness import file_storage
    from app.services.pi_runtime.runner import PiRunResult

    env = _citation_seed(
        citation_client, tmp_path, "late-file-owner", space_name="late-file-space",
        name="真实来源", text="经核验的项目事实。",
    )
    workspace = tmp_path / "late-file-workspace"
    workspace.mkdir()
    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(settings, "media_intent_mode", "off")
    monkeypatch.setattr(minio_storage, "is_minio_enabled", lambda: False)
    monkeypatch.setattr(file_storage, "_workspace_registry", {})
    text = f"结论 ^[{env.source.citation_id}]\n[报告](/workspace/报告.md)"

    class FakePiClient:
        effective_model = "chat-model"

        def __init__(self, **kwargs):
            pass

        async def chat_async(self, message, **kwargs):
            markdown = f"# 真实报告\n\n结论 ^[{env.source.citation_id}]"
            (workspace / "报告.md").write_text(markdown, encoding="utf-8")
            await kwargs["on_event"]({"type": "message_end", "message": {
                "role": "assistant", "timestamp": 1, "stopReason": "stop",
                "content": [{"type": "text", "text": text}],
            }})
            # 文件导出的核验来源晚于正文完成，不得靠重写正文补交付。
            with SessionLocal() as db:
                sources = validate_evidence_sources(
                    db, user=db.get(User, env.member["id"]), texts=[markdown],
                    evidence=[_citation_candidate(env.source)], space_ids=[env.space_id],
                ).sources
            files = file_storage.scan_and_upload_workspace(
                kwargs["session_id"], workspace, evidence_sources=sources,
            )
            assert files and any(source["snapshot_status"] == "verified" for source in files[0]["evidence_sources"])
            return PiRunResult(
                reply=text, stats={}, state={}, events=[], generated_files=files,
                token_usage={"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7},
            ), object()

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    session_id = citation_client.post(
        "/chat/sessions", headers=env.headers, json={"title": "晚到文件来源"},
    ).json()["id"]
    response = citation_client.post("/chat/messages", headers=env.headers, json={
        "message": "生成有来源的报告", "local_session_id": session_id, "space_ids": [env.space_id],
        "client_message_id": "late-file-user", "client_assistant_message_id": "late-file-answer",
    })
    events = _sse_payloads(response)
    assert events[-1]["type"] == "done", events[-1]
    completed = [event["message"] for event in events if event["type"] == "assistant_message" and event["phase"] == "complete"]
    assert completed[0]["references"] == []
    final = events[-1]["assistant_messages"][0]
    assert final["content"] == completed[0]["content"]
    assert [source["id"] for source in final["references"]] == [env.source.citation_id]
    assert final["citation_statuses"] == {}
    card = next(part for part in final["parts"] if part["type"] == "file")
    downloaded = citation_client.get(card["href"].removeprefix("/api"), headers=env.headers)
    assert downloaded.status_code == 200 and len(downloaded.content) == card["size"]
    history = _citation_messages(citation_client, env.headers, session_id)
    stored = _citation_pick(history, "late-file-answer")
    assert stored["content"] == final["content"]
    assert stored["references"] == final["references"]
