"""Focused tests for the shared media task dispatch service.

Covers idempotency, conflict detection, outcome_unknown mapping,
first-frame retention, and historical nullable rows.
"""

from __future__ import annotations

import base64
import json
import asyncio
import threading
from contextlib import contextmanager
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core import database
from app.api import chat as chat_api
from app.core.config import settings
from app.core.security import new_token, now
from app.models.auth import User
from app.models.chat import ChatDispatchDecision, ChatMessage, ChatSession
from app.models.external_object_cleanup import ExternalObjectCleanupOutbox
from app.models.image_generation import ImageGenerationTask
from app.repositories import chat as chat_repo
from app.models.video_generation import VideoGenerationTask
from app.repositories import chat_dispatch
from app.schemas.chat import ChatImageContent
from app.services import image_generation as image_service
from app.services import external_object_cleanup
from app.services import media_intent_router
from app.services import media_task_dispatch
from app.services import video_generation as video_service
from app.services.minio_storage import MinioSettings


@pytest.fixture(autouse=True)
def _isolate_title_provider(monkeypatch: pytest.MonkeyPatch):
    summarizer = Mock(return_value="媒体任务标题")
    monkeypatch.setattr(chat_api, "summarize_session_title", summarizer)
    return summarizer


@contextmanager
def _configured_db(tmp_path: Path):
    overridden_settings = {
        "db_path": settings.db_path,
        "database_url": settings.database_url,
        "dashscope_api_key": settings.dashscope_api_key,
        "dashscope_image_workspace_id": settings.dashscope_image_workspace_id,
        "dashscope_video_workspace_id": settings.dashscope_video_workspace_id,
        "dashscope_image_model": settings.dashscope_image_model,
        "dashscope_image_edit_model": settings.dashscope_image_edit_model,
        "dashscope_video_model": settings.dashscope_video_model,
        "dashscope_video_image_model": settings.dashscope_video_image_model,
        "media_intent_mode": settings.media_intent_mode,
    }
    db_path = tmp_path / "media-dispatch.sqlite3"
    settings.db_path = db_path
    settings.database_url = f"sqlite:///{db_path}"
    settings.dashscope_api_key = "dispatch-test-key"
    settings.dashscope_image_workspace_id = "image-test-workspace"
    settings.dashscope_video_workspace_id = "video-test-workspace"
    settings.dashscope_image_model = "kling/kling-v3-image-generation"
    settings.dashscope_image_edit_model = "wan2.7-image-pro"
    settings.dashscope_video_model = "kling/kling-v3-video-generation"
    settings.dashscope_video_image_model = "kling/kling-v3-omni-video-generation"
    try:
        database.initialize_database()
        with database.SessionLocal() as db:
            user = User(
                id="usr_test",
                username="dispatch-user",
                display_name="Dispatch User",
                email="dispatch@test.com",
                password_hash="hashed",
                role="admin",
                auth_version=1,
                created_at=now(),
            )
            db.add(user)
            db.commit()
            yield db, user
    finally:
        for key, value in overridden_settings.items():
            setattr(settings, key, value)
        database.reset_database(db_path)


def _create_session(db, user: User) -> ChatSession:
    session = ChatSession(
        id=f"chs_{new_token(10)}",
        owner_user_id=user.id,
        title="Test Session",
        created_at=now(),
        updated_at=now(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _valid_edit_source() -> ChatImageContent:
    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (640, 480), color=(10, 120, 200)).save(output, format="PNG")
    return ChatImageContent(
        data=base64.b64encode(output.getvalue()).decode("ascii"),
        mimeType="image/png",
    )


def test_media_request_digest_binds_prompt_attachment_options_and_skill():
    base = chat_dispatch.request_digest(
        "生成产品海报",
        [{"type": "image", "data": "a", "mimeType": "image/png"}],
        media_options={"resolution": "1k"},
        requested_skill="image-generation",
    )
    assert base == chat_dispatch.request_digest(
        "生成产品海报",
        [{"type": "image", "data": "a", "mimeType": "image/png"}],
        media_options={"resolution": "1k"},
        requested_skill="image-generation",
    )
    assert base != chat_dispatch.request_digest(
        "生成产品海报",
        [{"type": "image", "data": "a", "mimeType": "image/png"}],
        media_options={"resolution": "2k"},
        requested_skill="image-generation",
    )
    assert base != chat_dispatch.request_digest(
        "生成产品海报",
        [{"type": "image", "data": "a", "mimeType": "image/png"}],
        media_options={"resolution": "1k"},
        requested_skill="image-edit",
    )
    assert base != chat_dispatch.request_digest(
        "生成产品海报",
        [{"type": "image", "data": "a", "mimeType": "image/png"}],
        media_options={"resolution": "1k"},
        requested_skill="image-generation",
        model="media-model-b",
    )


def test_dispatch_decision_lookup_is_owner_scoped(tmp_path: Path):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        decision = chat_dispatch.create_dispatch_decision(
            db,
            owner_user_id=user.id,
            chat_session_id=session.id,
            client_message_id="cm-owner-isolation",
            predicted_intent="image_generate",
            effective_route="image_generate",
            final_intent="image_generate",
            prompt_digest=chat_dispatch.prompt_digest("owner prompt"),
            attachment_digest=chat_dispatch.attachment_digest(None),
        )
        db.commit()

        assert (
            chat_dispatch.get_dispatch_decision(
                db,
                owner_user_id="other-user",
                client_message_id="cm-owner-isolation",
            )
            is None
        )
        assert (
            chat_dispatch.get_dispatch_decision_by_id(
                db,
                decision_id=decision.id,
                owner_user_id="other-user",
            )
            is None
        )


def test_confirmation_cas_allows_one_winner_across_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        decision = chat_dispatch.create_dispatch_decision(
            db,
            owner_user_id=user.id,
            chat_session_id=session.id,
            client_message_id="cm-confirm-cas",
            predicted_intent="image_generate",
            effective_route="clarify",
            route_source="classifier",
            confidence_band="high",
            reason_code="cas-test",
            decision_json={
                "clarification_choices": [
                    {"intent": "image_generate", "label": "生成图片"},
                    {"intent": "chat", "label": "只聊天"},
                ]
            },
            status="awaiting_confirmation",
        )
        db.commit()

        barrier = threading.Barrier(2)
        original_get = chat_dispatch.get_dispatch_decision_by_id
        lock = threading.Lock()
        initial_reads = 0

        def barrier_get(*args, **kwargs):
            nonlocal initial_reads
            result = original_get(*args, **kwargs)
            if kwargs.get("for_update"):
                with lock:
                    initial_reads += 1
                    read_number = initial_reads
                if read_number <= 2:
                    barrier.wait(timeout=10)
            return result

        monkeypatch.setattr(chat_dispatch, "get_dispatch_decision_by_id", barrier_get)

        def confirm_from_independent_session() -> tuple[bool, str | None]:
            with database.SessionLocal() as confirm_db:
                try:
                    _decision, claimed = chat_dispatch.confirm_dispatch_decision(
                        confirm_db,
                        decision_id=decision.id,
                        owner_user_id=user.id,
                        chat_session_id=session.id,
                        client_message_id="cm-confirm-cas",
                        final_intent="image_generate",
                        effective_route="image_generate",
                        route_source="user_override",
                        confidence_band="high",
                        reason_code="user_override",
                        classifier_model=None,
                        classifier_version="media-intent-v2",
                        decision_json={"parameters": {}},
                        classifier_usage_json={},
                    )
                    confirm_db.commit()
                    return claimed, None
                except Exception as exc:
                    confirm_db.rollback()
                    return False, type(exc).__name__

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _index: confirm_from_independent_session(),
                    range(2),
                )
            )
        assert sorted(claimed for claimed, _error in results) == [False, True]
        assert all(error is None for _claimed, error in results)

        with database.SessionLocal() as verify_db:
            persisted = verify_db.get(ChatDispatchDecision, decision.id)
            assert persisted is not None
            assert persisted.status == "awaiting_confirmation"
            assert persisted.final_intent == "image_generate"


@pytest.mark.asyncio
async def test_dispatch_media_task_idempotent_on_same_client_message_id(tmp_path: Path):
    """Same client_message_id returns existing task without calling provider twice."""
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        client_message_id = "cm_test_123"
        assistant_message_id = "chm_assistant_1"

        provider_task = image_service.ProviderTask(
            provider_task_id="provider-1",
            status="pending",
        )

        with patch(
            "app.services.media_task_dispatch.image_generation.create_provider_task",
            new=AsyncMock(return_value=provider_task),
        ) as mock_create:
            # First call
            result1 = await media_task_dispatch.dispatch_media_task(
                db,
                session=session,
                user=user,
                client_message_id=client_message_id,
                assistant_message_id=assistant_message_id,
                intent="image_generate",
                prompt="Test prompt",
                media_params={"resolution": "1k"},
                source_image=None,
            )
            assert result1["created"] is True
            assert result1["task_kind"] == "image"
            assert mock_create.await_count == 1

            # Second call with same client_message_id
            result2 = await media_task_dispatch.dispatch_media_task(
                db,
                session=session,
                user=user,
                client_message_id=client_message_id,
                assistant_message_id="chm_assistant_2",
                intent="image_generate",
                prompt="Test prompt",
                media_params={"resolution": "1k"},
                source_image=None,
            )
            assert result2["created"] is False
            assert result2["task"]["id"] == result1["task"]["id"]
            assert mock_create.await_count == 1  # Provider not called again


@pytest.mark.asyncio
async def test_dispatch_media_task_rejects_conflict(tmp_path: Path):
    """Different prompt/attachment with same client_message_id raises conflict."""
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        client_message_id = "cm_conflict_456"

        provider_task = image_service.ProviderTask(
            provider_task_id="provider-2",
            status="pending",
        )

        with patch(
            "app.services.media_task_dispatch.image_generation.create_provider_task",
            new=AsyncMock(return_value=provider_task),
        ):
            # First call
            await media_task_dispatch.dispatch_media_task(
                db,
                session=session,
                user=user,
                client_message_id=client_message_id,
                assistant_message_id="chm_assistant_1",
                intent="image_generate",
                prompt="Original prompt",
                media_params={"resolution": "1k"},
                source_image=None,
            )

            # Second call with different prompt
            with pytest.raises(chat_dispatch.DispatchConflictError):
                await media_task_dispatch.dispatch_media_task(
                    db,
                    session=session,
                    user=user,
                    client_message_id=client_message_id,
                    assistant_message_id="chm_assistant_2",
                    intent="image_generate",
                    prompt="Different prompt",
                    media_params={"resolution": "1k"},
                    source_image=None,
                )


@pytest.mark.asyncio
async def test_dispatch_media_task_maps_unknown_to_outcome_unknown(tmp_path: Path):
    """Provider 'unknown' status maps to local 'outcome_unknown'."""
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        client_message_id = "cm_unknown_789"

        provider_task = image_service.ProviderTask(
            provider_task_id="provider-3",
            status="unknown",
        )

        with patch(
            "app.services.media_task_dispatch.image_generation.create_provider_task",
            new=AsyncMock(return_value=provider_task),
        ):
            result = await media_task_dispatch.dispatch_media_task(
                db,
                session=session,
                user=user,
                client_message_id=client_message_id,
                assistant_message_id="chm_assistant_1",
                intent="image_generate",
                prompt="Test prompt",
                media_params={"resolution": "1k"},
                source_image=None,
            )
            assert result["task"]["status"] == "outcome_unknown"
            assert result["task"]["error_message"] == "供应商结果未知，请勿重复提交。"


@pytest.mark.asyncio
async def test_dispatch_media_task_preserves_video_source_file_ref_on_unknown(
    tmp_path: Path,
):
    """Video source_file_ref is retained through outcome_unknown."""
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        client_message_id = "cm_video_ref_012"

        provider_task = video_service.ProviderTask(
            provider_task_id="provider-4",
            status="unknown",
            source_file_ref="bailian://file-ref-123",
        )

        with patch(
            "app.services.media_task_dispatch.video_generation.create_provider_task",
            new=AsyncMock(return_value=provider_task),
        ):
            result = await media_task_dispatch.dispatch_media_task(
                db,
                session=session,
                user=user,
                client_message_id=client_message_id,
                assistant_message_id="chm_assistant_1",
                intent="video_generate",
                prompt="Test video",
                media_params={"mode": "std", "duration": 5},
                source_image={
                    "type": "image",
                    "data": "ZnJhbWU=",
                    "mimeType": "image/png",
                },
            )
            assert result["task"]["status"] == "outcome_unknown"
            # source_file_ref is not exposed in API response but persisted in DB
            task_id = result["task"]["id"]
            with database.SessionLocal() as verify_db:
                task = verify_db.scalar(
                    select(VideoGenerationTask).where(VideoGenerationTask.id == task_id)
                )
                assert task.source_file_ref == "bailian://file-ref-123"


@pytest.mark.asyncio
async def test_dispatch_media_task_requires_stable_client_message_id(tmp_path: Path):
    """Empty client_message_id raises ValueError."""
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)

        with pytest.raises(ValueError, match="client_message_id is required"):
            await media_task_dispatch.dispatch_media_task(
                db,
                session=session,
                user=user,
                client_message_id="",
                assistant_message_id="chm_assistant_1",
                intent="image_generate",
                prompt="Test prompt",
                media_params={"resolution": "1k"},
                source_image=None,
            )


def test_historical_nullable_dispatch_id(tmp_path: Path):
    """Historical tasks with NULL dispatch_id remain queryable."""
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        messages = chat_repo.append_messages(
            db,
            session,
            [
                {"role": "user", "content": "Historical prompt"},
                {"role": "assistant", "content": "Done"},
            ],
        )
        db.flush()

        # Create a task without dispatch_id (simulating historical data)
        task = ImageGenerationTask(
            id=f"igt_{new_token(10)}",
            owner_user_id=user.id,
            chat_session_id=session.id,
            user_message_id=messages[0].id,
            assistant_message_id=messages[1].id,
            dispatch_id=None,  # Historical nullable
            provider_task_id="provider-historical",
            operation="generate",
            model="kling/kling-v3-image-generation",
            aspect_ratio="1:1",
            resolution="1k",
            prompt="Historical prompt",
            status="succeeded",
            image_url="https://example.com/image.png",
            created_at=now(),
            updated_at=now(),
            completed_at=now(),
        )
        db.add(task)
        db.commit()

        # Query should work
        queried = db.scalar(
            select(ImageGenerationTask).where(ImageGenerationTask.id == task.id)
        )
        assert queried is not None
        assert queried.dispatch_id is None
        assert queried.status == "succeeded"


def test_recent_media_context_preserves_active_image_after_a_later_chat_turn(
    tmp_path: Path,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        image_messages = chat_repo.append_messages(
            db,
            session,
            [
                {"role": "user", "content": "生成一张机场照片"},
                {"role": "assistant", "content": "图片生成完成"},
            ],
        )
        generated = ImageGenerationTask(
            id="igt-context-window",
            owner_user_id=user.id,
            chat_session_id=session.id,
            user_message_id=image_messages[0].id,
            assistant_message_id=image_messages[1].id,
            dispatch_id=None,
            provider_task_id="provider-context-window",
            operation="generate",
            model="kling/kling-v3-image-generation",
            aspect_ratio="1:1",
            resolution="1k",
            prompt="生成一张机场照片",
            status="succeeded",
            image_url="https://example.com/context-window.png",
            created_at=now(),
            updated_at=now(),
            completed_at=now(),
        )
        db.add(generated)
        db.commit()

        immediate = chat_api._recent_media_route_context(
            db,
            user_id=user.id,
            session_id=session.id,
        )
        assert immediate[0]["is_immediate_context"] is True
        assert immediate[0]["is_active_context"] is True

        chat_repo.append_messages(
            db,
            session,
            [
                {"role": "user", "content": "先介绍一下这个场景"},
                {"role": "assistant", "content": "这是机场值机服务台。"},
            ],
        )
        db.commit()

        later = chat_api._recent_media_route_context(
            db,
            user_id=user.id,
            session_id=session.id,
        )
        assert later[0]["is_immediate_context"] is False
        assert later[0]["is_active_context"] is True


@pytest.mark.parametrize(
    "source_state",
    [
        "available",
        "other_owner",
        "other_session",
        "failed",
        "missing_storage",
        "missing_task",
    ],
)
def test_recent_media_context_includes_cache_only_succeeded_image(
    tmp_path: Path, source_state: str
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        image_messages = chat_repo.append_messages(
            db,
            session,
            [
                {"role": "user", "content": "生成一张缓存图片"},
                {"role": "assistant", "content": "图片生成完成"},
            ],
        )
        generated = ImageGenerationTask(
            id="igt-cache-only-context",
            owner_user_id=user.id,
            chat_session_id=session.id,
            user_message_id=image_messages[0].id,
            assistant_message_id=image_messages[1].id,
            dispatch_id=None,
            provider_task_id="provider-cache-only-context",
            operation="generate",
            model="kling/kling-v3-image-generation",
            aspect_ratio="1:1",
            resolution="1k",
            prompt="生成一张缓存图片",
            status="succeeded",
            image_url=None,
            image_cache_uri="minio://generated-images/cache-only.png",
            created_at=now(),
            updated_at=now(),
            completed_at=now(),
        )
        db.add(generated)
        db.commit()

        context = chat_api._recent_media_route_context(
            db,
            user_id=user.id,
            session_id=session.id,
        )

        assert [item["task_id"] for item in context] == [generated.id]
        assert context[0]["is_immediate_context"] is True
        assert context[0]["is_active_context"] is True

        if source_state == "failed":
            generated.status = "failed"
        elif source_state == "missing_storage":
            generated.image_cache_uri = None
        db.commit()
        pinned = chat_api._recent_media_route_context(
            db,
            user_id="usr-other" if source_state == "other_owner" else user.id,
            session_id="chs-other" if source_state == "other_session" else session.id,
            source_task_id=(
                "igt-missing" if source_state == "missing_task" else generated.id
            ),
        )
        if source_state == "available":
            assert [item["task_id"] for item in pinned] == [generated.id]
        else:
            assert pinned == []


@pytest.mark.asyncio
async def test_dispatch_media_task_creates_decision_and_messages(tmp_path: Path):
    """Dispatch creates decision, user message, and assistant message."""
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        client_message_id = "cm_full_345"
        assistant_message_id = "chm_assistant_full"

        provider_task = image_service.ProviderTask(
            provider_task_id="provider-5",
            status="succeeded",
            image_url="https://example.com/result.png",
        )

        with patch(
            "app.services.media_task_dispatch.image_generation.create_provider_task",
            new=AsyncMock(return_value=provider_task),
        ):
            result = await media_task_dispatch.dispatch_media_task(
                db,
                session=session,
                user=user,
                client_message_id=client_message_id,
                assistant_message_id=assistant_message_id,
                intent="image_generate",
                prompt="Generate an image",
                media_params={"resolution": "1k"},
                source_image=None,
            )

            # Verify decision created
            decision = db.scalar(
                select(ChatDispatchDecision).where(
                    ChatDispatchDecision.client_message_id == client_message_id
                )
            )
            assert decision is not None
            assert decision.predicted_intent == "image_generate"
            assert decision.effective_route == "image_generate"
            assert decision.status == "dispatched"

            # Verify messages created
            user_msg = db.scalar(
                select(ChatMessage).where(
                    ChatMessage.id == result["task"]["user_message_id"]
                )
            )
            assert user_msg is not None
            assert user_msg.role == "user"
            assert user_msg.content == "Generate an image"
            assert user_msg.dispatch_id == decision.id

            assistant_msg = db.scalar(
                select(ChatMessage).where(
                    ChatMessage.id == result["task"]["assistant_message_id"]
                )
            )
            assert assistant_msg is not None
            assert assistant_msg.role == "assistant"


@pytest.mark.asyncio
async def test_reconcile_stale_submission_marks_unknown_and_blocks_retry(
    tmp_path: Path,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        provider_task = image_service.ProviderTask(
            provider_task_id="provider-stale",
            status="pending",
        )
        with patch(
            "app.services.media_task_dispatch.image_generation.create_provider_task",
            new=AsyncMock(return_value=provider_task),
        ):
            result = await media_task_dispatch.dispatch_media_task(
                db,
                session=session,
                user=user,
                client_message_id="cm-stale",
                assistant_message_id="chm-stale-assistant",
                intent="image_generate",
                prompt="Generate a stale image",
                media_params={"resolution": "1k"},
                source_image=None,
            )

        stale_at = now() - timedelta(minutes=10)
        task = db.get(ImageGenerationTask, result["task"]["id"])
        decision = db.get(ChatDispatchDecision, result["decision_id"])
        assert task is not None
        assert decision is not None
        task.status = "submitting"
        task.provider_task_id = None
        task.updated_at = stale_at
        decision.status = "dispatching"
        decision.updated_at = stale_at
        db.commit()

        counts = media_task_dispatch.reconcile_stale_submissions(
            db,
            lease_seconds=60,
        )
        db.commit()

        assert counts == {"image_tasks": 1, "video_tasks": 0, "decisions": 1}
        assert task.status == "outcome_unknown"
        assert decision.status == "outcome_unknown"
        with pytest.raises(ValueError, match="仅确定失败任务可以重试"):
            await media_task_dispatch.retry_media_task(
                task_kind="image",
                task_id=task.id,
                user=user,
            )


@pytest.mark.asyncio
async def test_dispatch_retries_result_commit_without_resubmitting_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        provider_task = image_service.ProviderTask(
            provider_task_id="provider-commit-retry",
            status="pending",
        )
        real_session_local = database.SessionLocal
        commit_attempts = 0

        def flaky_session_local():
            nonlocal commit_attempts
            update_db = real_session_local()
            original_commit = update_db.commit

            def commit_once_fails() -> None:
                nonlocal commit_attempts
                commit_attempts += 1
                if commit_attempts == 1:
                    raise RuntimeError("transient result commit failure")
                original_commit()

            setattr(update_db, "commit", commit_once_fails)
            return update_db

        monkeypatch.setattr(media_task_dispatch, "SessionLocal", flaky_session_local)
        monkeypatch.setattr(image_service, "image_cache_is_available", lambda: False)
        with patch(
            "app.services.media_task_dispatch.image_generation.create_provider_task",
            new=AsyncMock(return_value=provider_task),
        ) as create_provider:
            result = await media_task_dispatch.dispatch_media_task(
                db,
                session=session,
                user=user,
                client_message_id="cm-commit-retry",
                assistant_message_id="chm-commit-retry",
                intent="image_generate",
                prompt="Commit retry",
                media_params={"resolution": "1k"},
                source_image=None,
            )

        assert result["task"]["status"] == "pending"
        assert create_provider.await_count == 1
        assert commit_attempts == 2
        with database.SessionLocal() as verify_db:
            stored = verify_db.get(ImageGenerationTask, result["task"]["id"])
            assert stored is not None
            assert stored.status == "pending"
            assert stored.provider_task_id == "provider-commit-retry"


@pytest.mark.asyncio
async def test_image_cache_metadata_commit_failure_is_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        messages = chat_repo.append_messages(
            db,
            session,
            [
                {"role": "user", "content": "缓存回写"},
                {"role": "assistant", "content": "图片生成任务已完成。"},
            ],
        )
        task = ImageGenerationTask(
            id="igt-cache-commit-failure",
            owner_user_id=user.id,
            chat_session_id=session.id,
            user_message_id=messages[0].id,
            assistant_message_id=messages[1].id,
            dispatch_id=None,
            provider_task_id="provider-cache-commit-failure",
            operation="generate",
            model="kling/kling-v3-image-generation",
            aspect_ratio="1:1",
            resolution="1k",
            prompt="缓存回写",
            status="succeeded",
            image_url="https://p4-fdl.klingai.com/cache.png",
            image_cache_uri=None,
            created_at=now(),
            updated_at=now(),
            completed_at=now(),
        )
        db.add(task)
        db.commit()

        real_session_local = database.SessionLocal

        def failing_session_local():
            update_db = real_session_local()

            def commit_fails() -> None:
                raise RuntimeError("transient cache metadata commit failure")

            setattr(update_db, "commit", commit_fails)
            return update_db

        monkeypatch.setattr(media_task_dispatch, "SessionLocal", failing_session_local)
        monkeypatch.setattr(image_service, "image_cache_is_available", lambda: True)
        monkeypatch.setattr(
            image_service,
            "mirror_provider_image",
            AsyncMock(return_value="minio://dgmeta/cache-repair"),
        )

        await media_task_dispatch._mirror_succeeded_image(
            task,
            provider_image_url=task.image_url or "",
        )

        with database.SessionLocal() as verify_db:
            stored = verify_db.get(ImageGenerationTask, task.id)
            assert stored is not None
            assert stored.image_cache_uri is None


@pytest.mark.asyncio
async def test_image_mirror_race_enqueues_uploaded_uri_after_task_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = MinioSettings(
        endpoint_url="http://minio:9000",
        show_url="http://minio:9000",
        access_key="access",
        secret_key="secret",
        bucket="dgmeta",
        temp_bucket="dgmeta-temp",
        presign_minutes=120,
    )
    monkeypatch.setattr(
        external_object_cleanup.minio_storage,
        "load_minio_settings",
        lambda: config,
    )
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        messages = chat_repo.append_messages(
            db,
            session,
            [
                {"role": "user", "content": "竞态"},
                {"role": "assistant", "content": "图片生成任务已完成。"},
            ],
        )
        task = ImageGenerationTask(
            id="igt-mirror-delete-race",
            owner_user_id=user.id,
            chat_session_id=session.id,
            user_message_id=messages[0].id,
            assistant_message_id=messages[1].id,
            dispatch_id=None,
            provider_task_id="provider-mirror-delete-race",
            operation="generate",
            model="kling/kling-v3-image-generation",
            aspect_ratio="1:1",
            resolution="1k",
            prompt="竞态",
            status="succeeded",
            image_url="https://p4-fdl.klingai.com/cache.png",
            image_cache_uri=None,
            created_at=now(),
            updated_at=now(),
            completed_at=now(),
        )
        db.add(task)
        db.commit()

        mirrored_uri = (
            "minio://dgmeta/image-generation/results/usr_test/igt-mirror-delete-race"
        )

        def delete_then_persist(callback, *, attempts):
            with database.transaction() as delete_db:
                target = delete_db.get(ImageGenerationTask, task.id)
                assert target is not None
                delete_db.delete(target)
            with database.transaction() as persist_db:
                callback(persist_db)

        monkeypatch.setattr(
            media_task_dispatch,
            "run_db_transaction_with_retry",
            delete_then_persist,
        )
        monkeypatch.setattr(image_service, "image_cache_is_available", lambda: True)
        monkeypatch.setattr(
            image_service,
            "mirror_provider_image",
            AsyncMock(return_value=mirrored_uri),
        )

        await media_task_dispatch._mirror_succeeded_image(
            task,
            provider_image_url=task.image_url or "",
        )

        with database.SessionLocal() as verify_db:
            assert verify_db.get(ImageGenerationTask, task.id) is None
            tombstone = verify_db.scalar(
                select(ExternalObjectCleanupOutbox).where(
                    ExternalObjectCleanupOutbox.uri == mirrored_uri
                )
            )
            assert tombstone is not None


@pytest.mark.asyncio
async def test_media_reconciliation_loop_scans_without_restart(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.services.background_workers import (
        BackgroundWorkerService,
        _reconcile_stale_media_submissions,
    )

    scans: list[tuple[object, tuple[object, ...]]] = []
    worker = BackgroundWorkerService("background")

    async def fake_background(function, *args):
        scans.append((function, args))
        return {"image_tasks": 0, "video_tasks": 0, "decisions": 0}

    async def stop_after_scan(_interval: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(worker.resources, "run_background", fake_background)
    monkeypatch.setattr(worker, "_sleep", stop_after_scan)

    try:
        with pytest.raises(asyncio.CancelledError):
            await worker._media_reconciliation_loop()
    finally:
        worker.resources.close()

    assert len(scans) == 1
    assert scans[0][0] is _reconcile_stale_media_submissions


@pytest.mark.asyncio
async def test_stale_scan_does_not_overwrite_concurrent_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        provider_task = image_service.ProviderTask(
            provider_task_id="provider-stale-race",
            status="pending",
        )
        with patch(
            "app.services.media_task_dispatch.image_generation.create_provider_task",
            new=AsyncMock(return_value=provider_task),
        ):
            result = await media_task_dispatch.dispatch_media_task(
                db,
                session=session,
                user=user,
                client_message_id="cm-stale-race",
                assistant_message_id="chm-stale-race-assistant",
                intent="image_generate",
                prompt="stale race",
                media_params={"resolution": "1k"},
                source_image=None,
            )

        task = db.get(ImageGenerationTask, result["task"]["id"])
        decision = db.get(ChatDispatchDecision, result["decision_id"])
        assert task is not None
        assert decision is not None
        task.status = "submitting"
        task.provider_task_id = None
        task.updated_at = now() - timedelta(minutes=10)
        decision.status = "dispatching"
        decision.updated_at = task.updated_at
        db.commit()

        original_scalars = db.scalars
        concurrent_completed = False

        def scalars_with_concurrent_completion(statement, *args, **kwargs):
            nonlocal concurrent_completed
            result_rows = original_scalars(statement, *args, **kwargs)
            if not concurrent_completed:
                concurrent_completed = True
                with database.SessionLocal() as complete_db:
                    completed = complete_db.get(ImageGenerationTask, task.id)
                    assert completed is not None
                    completed.status = "succeeded"
                    completed.provider_task_id = "provider-stale-race"
                    completed.image_url = "https://p4-fdl.klingai.com/race.png"
                    completed.completed_at = now()
                    complete_db.commit()
            return result_rows

        setattr(db, "scalars", scalars_with_concurrent_completion)
        media_task_dispatch.reconcile_stale_submissions(db, lease_seconds=60)
        db.commit()

        with database.SessionLocal() as verify_db:
            stored = verify_db.get(ImageGenerationTask, task.id)
            assert stored is not None
            assert stored.status == "succeeded"
            assert stored.provider_task_id == "provider-stale-race"


async def _sse_payloads(response) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    async for chunk in response.body_iterator:
        text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["image_generate", "image_edit"])
@pytest.mark.parametrize("confirmed", [False, True], ids=["explicit", "confirm"])
async def test_media_turn_summarizes_session_title_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    intent: str,
    confirmed: bool,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        editing = intent == "image_edit"
        message = "把这张图片的主体改成紫色" if editing else "生成一张品牌海报"
        title = "品牌图片改色" if editing else "品牌海报设计"
        media_model = (
            settings.dashscope_image_edit_model
            if editing
            else settings.dashscope_image_model
        )
        skill = "image-edit" if editing else "image-generation"
        source_images = (
            [_valid_edit_source()]
            if editing
            else []
        )
        session.preferred_model = media_model
        db.commit()

        def summarize(*, user_id, messages, model=None):
            if model is not None:
                raise chat_api.ChatTitleSummarizeError("image models cannot summarize")
            assert any(
                item.role == "user" and item.content == message for item in messages
            )
            assert any(item.role == "assistant" and item.content for item in messages)
            return title

        summarizer = Mock(side_effect=summarize)
        monkeypatch.setattr(chat_api, "summarize_session_title", summarizer)
        provider = AsyncMock(
            side_effect=[
                image_service.ProviderTask(
                    provider_task_id=f"provider-title-{index}", status="pending"
                )
                for index in range(2)
            ]
        )
        monkeypatch.setattr(image_service, "create_provider_task", provider)
        request = chat_api.ChatMessageRequest(
            message=message,
            images=source_images,
            model=media_model,
            explicit_skill=None if confirmed else skill,
            local_session_id=session.id,
            client_message_id="cm-media-title",
            client_assistant_message_id="cm-media-title-assistant",
        )
        events = await _sse_payloads(await chat_api.stream_chat_message(request, user))
        done = events[-1]
        assert done["type"] == "done"
        assert done["session_title"] == title
        assert done["session_title_pending"] is False
        assert chat_api.get_session_summary(session.id, user).title == title

        if confirmed:
            assert not any(event["type"] == "media_task" for event in events)
            request = request.model_copy(
                update={
                    "decision_id": events[1]["decision_id"],
                    "final_intent": intent,
                }
            )
            events = await _sse_payloads(
                await chat_api.stream_chat_message(request, user)
            )
        task_id = next(
            event["task"]["id"] for event in events if event["type"] == "media_task"
        )
        replay = await _sse_payloads(await chat_api.stream_chat_message(request, user))
        assert (
            next(
                event["task"]["id"] for event in replay if event["type"] == "media_task"
            )
            == task_id
        )
        assert chat_api.get_session_summary(session.id, user).title == title
        assert summarizer.call_count == 1
        assert provider.await_count == 1

        # An explicit rename stays frozen across later media turns.
        db.expire_all()
        chat_repo.update_session(
            db, session, title="手动命名", override_frozen_title=True
        )
        db.commit()
        follow_up = request.model_copy(
            update={
                "client_message_id": "cm-media-title-next",
                "client_assistant_message_id": "cm-media-title-next-assistant",
                "decision_id": None,
                "final_intent": None,
            }
        )
        await _sse_payloads(await chat_api.stream_chat_message(follow_up, user))
        assert chat_api.get_session_summary(session.id, user).title == "手动命名"
        assert summarizer.call_count == 1


@pytest.mark.asyncio
async def test_media_title_timeout_persists_after_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        monkeypatch.setattr(chat_api, "_CHAT_TITLE_DONE_WAIT_SECONDS", 0.01)
        release = threading.Event()

        def summarize(**_kwargs):
            release.wait(timeout=5)
            return "迟到的图片标题"

        summarizer = Mock(side_effect=summarize)
        monkeypatch.setattr(chat_api, "summarize_session_title", summarizer)
        request = chat_api.ChatMessageRequest(
            message="生成一张品牌海报",
            local_session_id=session.id,
            client_message_id="cm-title-slow",
            client_assistant_message_id="cm-title-slow-assistant",
        )
        try:
            events = await asyncio.wait_for(
                _sse_payloads(await chat_api.stream_chat_message(request, user)),
                timeout=1,
            )
            assert events[-1]["session_title"] is None
            assert events[-1]["session_title_pending"] is True
            assert (
                chat_api.get_session_summary(session.id, user).title == "Test Session"
            )
            # Replaying while the first summary is pending must share that call.
            replay = await asyncio.wait_for(
                _sse_payloads(await chat_api.stream_chat_message(request, user)),
                timeout=1,
            )
            assert replay[-1]["session_title_pending"] is True
            assert summarizer.call_count == 1
        finally:
            release.set()
            if chat_api._BACKGROUND_CHAT_TASKS:
                await asyncio.gather(*tuple(chat_api._BACKGROUND_CHAT_TASKS))
        assert chat_api.get_session_summary(session.id, user).title == "迟到的图片标题"


@pytest.mark.asyncio
async def test_media_title_failure_does_not_break_task_or_prevent_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        summarizer = Mock(
            side_effect=[
                chat_api.ChatTitleSummarizeError("title endpoint unavailable"),
                "重试后的图片标题",
            ]
        )
        monkeypatch.setattr(chat_api, "summarize_session_title", summarizer)
        provider = AsyncMock(
            return_value=image_service.ProviderTask(
                provider_task_id="provider-title-retry",
                status="pending",
            )
        )
        monkeypatch.setattr(image_service, "create_provider_task", provider)
        request = chat_api.ChatMessageRequest(
            message="生成一张品牌海报",
            explicit_skill="image-generation",
            local_session_id=session.id,
            client_message_id="cm-title-retry",
            client_assistant_message_id="cm-title-retry-assistant",
        )
        initial = await _sse_payloads(await chat_api.stream_chat_message(request, user))
        assert initial[-1]["server_persisted"] is True
        assert initial[-1]["session_title"] is None
        assert initial[-1]["session_title_pending"] is False
        task_id = next(
            event["task"]["id"] for event in initial if event["type"] == "media_task"
        )
        replay = await _sse_payloads(await chat_api.stream_chat_message(request, user))
        assert (
            next(
                event["task"]["id"] for event in replay if event["type"] == "media_task"
            )
            == task_id
        )
        assert replay[-1]["session_title"] == "重试后的图片标题"
        assert (
            chat_api.get_session_summary(session.id, user).title == "重试后的图片标题"
        )
        assert provider.await_count == 1


@pytest.mark.asyncio
async def test_chat_auto_route_calls_shared_dispatcher_with_exact_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (_db, user):
        session = _create_session(_db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "auto")
        monkeypatch.setattr(
            settings, "media_intent_auto_calibration_version", "media-intent-v2"
        )
        monkeypatch.setattr(
            settings,
            "media_intent_auto_reason_codes",
            "explicit_image_action",
        )
        monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
        monkeypatch.setattr(
            chat_api,
            "create_capability_token",
            lambda **_kwargs: "capability-token",
        )
        dispatcher = AsyncMock(
            return_value={
                "task_kind": "image",
                "task": {
                    "id": "igt-chat-auto",
                    "status": "pending",
                    "dispatch_id": "dsp-chat-auto",
                },
                "decision_id": "dsp-chat-auto",
                "created": True,
            }
        )
        monkeypatch.setattr(
            chat_api.media_task_dispatch,
            "dispatch_media_task",
            dispatcher,
        )

        response = await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message="生成一张水墨山水图",
                local_session_id=session.id,
                client_message_id="cm-chat-auto",
                client_assistant_message_id="cm-chat-auto-assistant",
            ),
            user,
        )
        payloads = await _sse_payloads(response)

        assert [payload["type"] for payload in payloads] == [
            "session",
            "route_decision",
            "media_task",
            "done",
        ]
        assert payloads[-1]["server_persisted"] is True
        assert dispatcher.await_count == 1
        call = dispatcher.await_args
        assert call is not None
        assert set(call.kwargs) == {
            "session",
            "user",
            "client_message_id",
            "assistant_message_id",
            "intent",
            "prompt",
            "media_params",
            "source_image",
            "decision_id",
        }
        assert call.kwargs["session"].id == session.id
        assert call.kwargs["user"] is user
        assert call.kwargs["client_message_id"] == "cm-chat-auto"
        assert call.kwargs["assistant_message_id"] == "cm-chat-auto-assistant"
        assert call.kwargs["intent"] == "image_generate"
        assert call.kwargs["prompt"] == "生成一张水墨山水图"
    assert call.kwargs["source_image"] is None


@pytest.mark.asyncio
async def test_chat_media_route_replays_existing_decision_without_reclassifying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        classifier_calls = 0

        def classify_once(**_kwargs):
            nonlocal classifier_calls
            classifier_calls += 1
            return (
                media_intent_router.MediaClassifierOutput(
                    predicted_intent="image_generate",
                    confidence_band="high",
                    reason_code="test_classifier",
                ),
                {},
                "test-classifier",
            )

        monkeypatch.setattr(
            media_intent_router,
            "classify_with_llm",
            classify_once,
        )
        request = chat_api.ChatMessageRequest(
            message="海报",
            local_session_id=session.id,
            client_message_id="cm-route-replay",
            client_assistant_message_id="cm-route-replay-assistant",
        )

        first = await chat_api.stream_chat_message(request, user)
        first_iterator = first.body_iterator.__aiter__()
        await first_iterator.__anext__()
        await first_iterator.aclose()

        replay = await chat_api.stream_chat_message(request, user)
        replay_payloads = await _sse_payloads(replay)

        assert classifier_calls == 1
        assert [payload["type"] for payload in replay_payloads] == [
            "session",
            "route_decision",
            "clarification",
            "done",
        ]
        assert replay_payloads[1]["decision_id"] == replay_payloads[2]["decision_id"]


@pytest.mark.asyncio
async def test_chat_media_replay_binds_model_to_client_message_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        classifier_calls = 0

        def classify_once(**_kwargs):
            nonlocal classifier_calls
            classifier_calls += 1
            return (
                media_intent_router.MediaClassifierOutput(
                    predicted_intent="image_generate",
                    confidence_band="high",
                    reason_code="test_classifier",
                ),
                {},
                "test-classifier",
            )

        monkeypatch.setattr(media_intent_router, "classify_with_llm", classify_once)
        first_request = chat_api.ChatMessageRequest(
            message="海报",
            local_session_id=session.id,
            client_message_id="cm-model-replay",
            client_assistant_message_id="cm-model-replay-assistant",
            model="media-model-a",
        )
        first = await chat_api.stream_chat_message(first_request, user)
        await _sse_payloads(first)

        with pytest.raises(HTTPException) as changed_model_error:
            await chat_api.stream_chat_message(
                chat_api.ChatMessageRequest(
                    message="海报",
                    local_session_id=session.id,
                    client_message_id="cm-model-replay",
                    client_assistant_message_id="cm-model-replay-assistant",
                    model="media-model-b",
                ),
                user,
            )
        assert changed_model_error.value.status_code == 409
        assert classifier_calls == 1


@pytest.mark.asyncio
async def test_chat_media_legacy_decision_replay_is_fail_closed(
    tmp_path: Path,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        legacy_json = {
            "request": {
                "prompt": "legacy 海报",
                "attachment": None,
                "media_options": {
                    "resolution": "1k",
                    "video_mode": None,
                    "video_duration": None,
                    "source_image_index": None,
                },
                "requested_skill": "image-generation",
                "model": "legacy-model-a",
            },
            "clarification_choices": [
                {"intent": "image_generate", "label": "生成图片"},
                {"intent": "chat", "label": "只聊天"},
            ],
            "parameters": {"resolution": "1k"},
        }
        decision = chat_dispatch.create_dispatch_decision(
            db,
            owner_user_id=user.id,
            chat_session_id=session.id,
            client_message_id="cm-legacy-replay",
            predicted_intent="image_generate",
            effective_route="clarify",
            route_source="classifier",
            confidence_band="high",
            reason_code="legacy",
            decision_json=legacy_json,
            status="awaiting_confirmation",
            prompt_digest=chat_dispatch.prompt_digest("legacy 海报"),
            attachment_digest=chat_dispatch.attachment_digest(None),
        )
        db.commit()

        changed_requests = (
            ({"resolution": "2k"}, "image-generation", "legacy-model-a"),
            ({"resolution": "1k"}, "image-edit", "legacy-model-a"),
            ({"resolution": "1k"}, "image-generation", "legacy-model-b"),
        )
        for changed_options, changed_skill, changed_model in changed_requests:
            with pytest.raises(HTTPException) as error:
                await chat_api.stream_chat_message(
                    chat_api.ChatMessageRequest(
                        message="legacy 海报",
                        local_session_id=session.id,
                        client_message_id="cm-legacy-replay",
                        client_assistant_message_id="cm-legacy-replay-assistant",
                        decision_id=decision.id,
                        media_options=changed_options,
                        explicit_skill=changed_skill,
                        model=changed_model,
                    ),
                    user,
                )
            assert error.value.status_code == 409

        with database.SessionLocal() as verify_db:
            stored = verify_db.get(ChatDispatchDecision, decision.id)
            assert stored is not None
            assert chat_dispatch.decision_request_digest(stored) is None


@pytest.mark.asyncio
async def test_completed_media_replay_validates_decision_before_shortcut(
    tmp_path: Path,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        decision = chat_dispatch.create_dispatch_decision(
            db,
            owner_user_id=user.id,
            chat_session_id=session.id,
            client_message_id="cm-completed-media",
            predicted_intent="chat",
            effective_route="chat",
            final_intent="chat",
            route_source="user_override",
            confidence_band="high",
            reason_code="user_override_chat",
            decision_json={"request_digest": "legacy-chat-digest"},
            status="resolved",
            prompt_digest=chat_dispatch.prompt_digest("旧聊天"),
            attachment_digest=chat_dispatch.attachment_digest(None),
        )
        chat_repo.append_messages(
            db,
            session,
            [
                {
                    "id": "cm-completed-media",
                    "role": "user",
                    "content": "旧聊天",
                    "dispatch_id": decision.id,
                },
                {
                    "id": "ca-completed-media",
                    "role": "assistant",
                    "content": "旧回答",
                    "usage": {"status": "completed"},
                    "dispatch_id": decision.id,
                },
            ],
        )
        db.commit()

        with pytest.raises(HTTPException) as error:
            await chat_api.stream_chat_message(
                chat_api.ChatMessageRequest(
                    message="旧聊天",
                    local_session_id=session.id,
                    client_message_id="cm-completed-media",
                    client_assistant_message_id="ca-completed-media",
                    decision_id=decision.id,
                    final_intent="image_generate",
                    media_options={"resolution": "2k"},
                ),
                user,
            )
        assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_chat_media_replay_status_matrix_returns_existing_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        monkeypatch.setattr(
            media_intent_router,
            "classify_with_llm",
            lambda **_kwargs: (
                media_intent_router.MediaClassifierOutput(
                    predicted_intent="image_generate",
                    confidence_band="high",
                    reason_code="status_matrix",
                ),
                {},
                "status-matrix-classifier",
            ),
        )
        initial_request = chat_api.ChatMessageRequest(
            message="状态矩阵海报",
            local_session_id=session.id,
            client_message_id="cm-status-matrix",
            client_assistant_message_id="cm-status-matrix-assistant",
        )
        initial = await chat_api.stream_chat_message(initial_request, user)
        initial_payloads = await _sse_payloads(initial)
        decision_id = next(
            payload["decision_id"]
            for payload in initial_payloads
            if payload["type"] == "route_decision"
        )

        awaiting_replay = await chat_api.stream_chat_message(initial_request, user)
        awaiting_payloads = await _sse_payloads(awaiting_replay)
        assert [payload["type"] for payload in awaiting_payloads] == [
            "session",
            "route_decision",
            "clarification",
            "done",
        ]
        provider_calls = 0

        async def create_provider_task(**_kwargs):
            nonlocal provider_calls
            provider_calls += 1
            return image_service.ProviderTask(
                provider_task_id="provider-status-matrix",
                status="pending",
            )

        monkeypatch.setattr(image_service, "create_provider_task", create_provider_task)
        confirmation_request = chat_api.ChatMessageRequest(
            message="状态矩阵海报",
            local_session_id=session.id,
            client_message_id="cm-status-matrix",
            client_assistant_message_id="cm-status-matrix-assistant",
            decision_id=decision_id,
            final_intent="image_generate",
        )
        first_confirmation = await chat_api.stream_chat_message(
            confirmation_request, user
        )
        first_confirmation_payloads = await _sse_payloads(first_confirmation)
        task_id = next(
            payload["task"]["id"]
            for payload in first_confirmation_payloads
            if payload["type"] == "media_task"
        )

        for status in ("dispatching", "dispatched", "failed", "outcome_unknown"):
            with database.SessionLocal() as status_db:
                persisted_decision = status_db.get(ChatDispatchDecision, decision_id)
                persisted_task = status_db.get(ImageGenerationTask, task_id)
                assert persisted_decision is not None
                assert persisted_task is not None
                persisted_decision.status = status
                persisted_task.status = (
                    "failed"
                    if status == "failed"
                    else "outcome_unknown"
                    if status == "outcome_unknown"
                    else "pending"
                )
                status_db.commit()

            replay = await chat_api.stream_chat_message(confirmation_request, user)
            replay_payloads = await _sse_payloads(replay)
            replay_task_id = next(
                payload["task"]["id"]
                for payload in replay_payloads
                if payload["type"] == "media_task"
            )
            assert replay_task_id == task_id
            assert provider_calls == 1

            with pytest.raises(HTTPException) as changed_final_error:
                await chat_api.stream_chat_message(
                    chat_api.ChatMessageRequest(
                        message="状态矩阵海报",
                        local_session_id=session.id,
                        client_message_id="cm-status-matrix",
                        client_assistant_message_id="cm-status-matrix-assistant",
                        decision_id=decision_id,
                        final_intent="video_generate",
                    ),
                    user,
                )
            assert changed_final_error.value.status_code == 409


@pytest.mark.asyncio
async def test_chat_media_confirmation_replays_same_task_without_provider_resubmit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        source_image = _valid_edit_source()
        message = "把这张图片改成产品海报"
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        initial = await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message=message,
                images=[source_image],
                local_session_id=session.id,
                client_message_id="cm-confirm-replay",
                client_assistant_message_id="cm-confirm-replay-assistant",
            ),
            user,
        )
        initial_payloads = await _sse_payloads(initial)
        decision_id = next(
            payload["decision_id"]
            for payload in initial_payloads
            if payload["type"] == "route_decision"
        )
        initial_route = next(
            payload
            for payload in initial_payloads
            if payload["type"] == "route_decision"
        )
        expected_request_inputs = {
            "media_options": {
                "resolution": None,
                "video_mode": None,
                "video_duration": None,
                "source_image_index": None,
            },
            "requested_skill": None,
            "model": None,
        }
        assert initial_route["parameters"]["resolution"] == "2k"
        assert initial_route["parameters"]["source_image_index"] == 0
        assert initial_route["request_inputs"] == expected_request_inputs
        db.expire_all()
        persisted_session = chat_repo.get_owned_session(db, session.id, user.id)
        assert persisted_session is not None
        detail = chat_api._session_detail_response(persisted_session, db)
        persisted_assistant = next(
            item for item in detail.messages if item.id == "cm-confirm-replay-assistant"
        )
        assert persisted_assistant.dispatch_decision is not None
        assert persisted_assistant.dispatch_decision.request_inputs is not None
        assert (
            persisted_assistant.dispatch_decision.request_inputs.model_dump()
            == expected_request_inputs
        )
        provider_calls = 0

        async def create_provider_task(**_kwargs):
            nonlocal provider_calls
            provider_calls += 1
            return image_service.ProviderTask(
                provider_task_id="provider-confirm-replay",
                status="pending",
            )

        monkeypatch.setattr(
            image_service,
            "create_provider_task",
            create_provider_task,
        )
        request = chat_api.ChatMessageRequest(
            message=message,
            images=[source_image],
            local_session_id=session.id,
            client_message_id="cm-confirm-replay",
            client_assistant_message_id="cm-confirm-replay-assistant",
            decision_id=decision_id,
            final_intent="image_edit",
        )

        first_confirmation = await chat_api.stream_chat_message(request, user)
        first_payloads = await _sse_payloads(first_confirmation)
        replay_confirmation = await chat_api.stream_chat_message(request, user)
        replay_payloads = await _sse_payloads(replay_confirmation)
        first_route = next(
            payload for payload in first_payloads if payload["type"] == "route_decision"
        )
        replay_route = next(
            payload
            for payload in replay_payloads
            if payload["type"] == "route_decision"
        )
        assert first_route["request_inputs"] == expected_request_inputs
        assert replay_route["request_inputs"] == expected_request_inputs

        first_task = next(
            payload["task"]["id"]
            for payload in first_payloads
            if payload["type"] == "media_task"
        )
        replay_task = next(
            payload["task"]["id"]
            for payload in replay_payloads
            if payload["type"] == "media_task"
        )
        assert provider_calls == 1
        assert first_task == replay_task
        assert [payload["type"] for payload in replay_payloads] == [
            "session",
            "route_decision",
            "media_task",
            "done",
        ]
        with database.SessionLocal() as verify_db:
            persisted_decision = verify_db.get(ChatDispatchDecision, decision_id)
            assert persisted_decision is not None
            stored_request_inputs = json.loads(persisted_decision.decision_json)[
                "request_inputs"
            ]
            assert stored_request_inputs["media_options"]["resolution"] is None
            projection = chat_dispatch.dispatch_decision_to_dict(persisted_decision)
            assert projection["request_inputs"] == expected_request_inputs
            assert set(projection["request_inputs"]) == {
                "media_options",
                "requested_skill",
                "model",
            }


@pytest.mark.asyncio
async def test_chat_media_replay_rejects_digest_or_final_intent_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        initial = await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message="生成一张产品海报",
                local_session_id=session.id,
                client_message_id="cm-conflict-replay",
                client_assistant_message_id="cm-conflict-replay-assistant",
                media_options={"resolution": "1k"},
            ),
            user,
        )
        initial_payloads = await _sse_payloads(initial)
        decision_id = next(
            payload["decision_id"]
            for payload in initial_payloads
            if payload["type"] == "route_decision"
        )

        with pytest.raises(HTTPException) as changed_options_error:
            await chat_api.stream_chat_message(
                chat_api.ChatMessageRequest(
                    message="生成一张产品海报",
                    local_session_id=session.id,
                    client_message_id="cm-conflict-replay",
                    client_assistant_message_id="cm-conflict-replay-assistant",
                    media_options={"resolution": "2k"},
                ),
                user,
            )
        assert changed_options_error.value.status_code == 409

        with pytest.raises(HTTPException) as changed_attachment_error:
            await chat_api.stream_chat_message(
                chat_api.ChatMessageRequest(
                    message="生成一张产品海报",
                    local_session_id=session.id,
                    client_message_id="cm-conflict-replay",
                    client_assistant_message_id="cm-conflict-replay-assistant",
                    images=[
                        ChatImageContent(
                            data="ZmFrZQ==",
                            mimeType="image/png",
                        )
                    ],
                ),
                user,
            )
        assert changed_attachment_error.value.status_code == 409

        with pytest.raises(HTTPException) as different_final_error:
            await chat_api.stream_chat_message(
                chat_api.ChatMessageRequest(
                    message="生成一张产品海报",
                    local_session_id=session.id,
                    client_message_id="cm-conflict-replay",
                    client_assistant_message_id="cm-conflict-replay-assistant",
                    decision_id=decision_id,
                    final_intent="video_generate",
                ),
                user,
            )
        assert different_final_error.value.status_code == 409


@pytest.mark.asyncio
async def test_chat_media_confirmation_concurrency_returns_one_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        initial = await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message="生成一张产品海报",
                local_session_id=session.id,
                client_message_id="cm-confirm-concurrent",
                client_assistant_message_id="cm-confirm-concurrent-assistant",
            ),
            user,
        )
        initial_payloads = await _sse_payloads(initial)
        decision_id = next(
            payload["decision_id"]
            for payload in initial_payloads
            if payload["type"] == "route_decision"
        )
        provider_calls = 0

        async def create_provider_task(**_kwargs):
            nonlocal provider_calls
            provider_calls += 1
            await asyncio.sleep(0)
            return image_service.ProviderTask(
                provider_task_id="provider-confirm-concurrent",
                status="pending",
            )

        monkeypatch.setattr(
            image_service,
            "create_provider_task",
            create_provider_task,
        )
        request = chat_api.ChatMessageRequest(
            message="生成一张产品海报",
            local_session_id=session.id,
            client_message_id="cm-confirm-concurrent",
            client_assistant_message_id="cm-confirm-concurrent-assistant",
            decision_id=decision_id,
            final_intent="image_generate",
        )
        first, second = await asyncio.gather(
            chat_api.stream_chat_message(request, user),
            chat_api.stream_chat_message(request, user),
        )
        first_payloads, second_payloads = await asyncio.gather(
            _sse_payloads(first),
            _sse_payloads(second),
        )
        task_ids = {
            payload["task"]["id"]
            for payloads in (first_payloads, second_payloads)
            for payload in payloads
            if payload["type"] == "media_task"
        }
        assert provider_calls == 1
        assert len(task_ids) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_operation", "source_prompt", "follow_up", "intervening_chat", "classifier_fails"),
    [
        ("generate", "生成一张机场值机照片", "能不能改成中国国内的", False, False),
        ("edit", "主体颜色改成紫色", "第一个字母D换成P", False, False),
        ("edit", "第一个字母D换成P", "再把M换成W", True, False),
        ("edit", "美化小鸡吃米图，头上加个光圈", "修改光圈，你那个像贴上去的，还有，帮我着色", True, True),
    ],
)
async def test_chat_follow_up_edits_persistent_generated_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_operation: str,
    source_prompt: str,
    follow_up: str,
    intervening_chat: bool,
    classifier_fails: bool,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        if intervening_chat:
            older_messages = chat_repo.append_messages(
                db,
                session,
                [
                    {"role": "user", "content": "主体颜色改成紫色"},
                    {"role": "assistant", "content": "上一张图片编辑完成"},
                ],
            )
            db.add(
                ImageGenerationTask(
                    id="igt-older-edit",
                    owner_user_id=user.id,
                    chat_session_id=session.id,
                    user_message_id=older_messages[0].id,
                    assistant_message_id=older_messages[1].id,
                    operation="edit",
                    model="wan2.7-image-pro",
                    aspect_ratio="source",
                    resolution="1k",
                    prompt="主体颜色改成紫色",
                    status="succeeded",
                    image_cache_uri="minio://dgmeta/image-generation/results/usr_test/igt-older-edit",
                    created_at=now() - timedelta(minutes=5),
                    updated_at=now() - timedelta(minutes=4),
                    completed_at=now() - timedelta(minutes=4),
                )
            )
        messages = chat_repo.append_messages(
            db,
            session,
            [
                {"role": "user", "content": source_prompt},
                {"role": "assistant", "content": "图片生成完成"},
            ],
        )
        generated = ImageGenerationTask(
            id="igt-contextual-edit",
            owner_user_id=user.id,
            chat_session_id=session.id,
            user_message_id=messages[0].id,
            assistant_message_id=messages[1].id,
            dispatch_id=None,
            provider_task_id="provider-contextual-edit",
            operation=source_operation,
            model="kling/kling-v3-image-generation",
            aspect_ratio="1:1",
            resolution="1k",
            prompt=source_prompt,
            status="succeeded",
            image_url=None,
            image_cache_uri="minio://dgmeta/image-generation/results/usr_test/igt-contextual-edit",
            created_at=now(),
            updated_at=now(),
            completed_at=now(),
        )
        db.add(generated)
        db.commit()
        if intervening_chat:
            chat_repo.append_messages(
                db,
                session,
                [
                    {"role": "user", "content": "先聊一下别的事情"},
                    {"role": "assistant", "content": "可以，之后还能继续编辑。"},
                ],
            )
            db.commit()
        monkeypatch.setattr(
            media_intent_router,
            "classify_with_llm",
            lambda **_kwargs: (
                media_intent_router.MediaClassifierOutput(
                    predicted_intent="image_edit", confidence_band="high"
                ),
                {},
                "test-classifier",
            ),
        )
        if classifier_fails:
            monkeypatch.setattr(
                media_intent_router,
                "classify_with_llm",
                Mock(side_effect=media_intent_router.qa_service.QaServiceError(
                    "The read operation timed out"
                )),
            )
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        monkeypatch.setattr(settings, "linkmind_enabled", False)
        vision_route = Mock(
            side_effect=HTTPException(
                status_code=400,
                detail="当前模型暂时无法处理图片：视觉能力测试尚未通过。",
            )
        )
        monkeypatch.setattr(chat_api, "_resolve_chat_image_route", vision_route)
        pending_images = AsyncMock(
            side_effect=AssertionError("Editing must not consume chat image context")
        )
        monkeypatch.setattr(
            chat_api, "_pending_generated_image_context", pending_images
        )
        source_image = {
            "type": "image",
            "data": "c291cmNl",
            "mimeType": "image/png",
        }
        cached_uris: list[str | None] = []
        monkeypatch.setattr(
            image_service,
            "read_cached_image_bytes",
            lambda uri: (
                cached_uris.append(uri)
                or (
                    b"cached-image-bytes"
                    if uri == generated.image_cache_uri
                    else b"older-image-bytes"
                )
            ),
        )
        monkeypatch.setattr(
            image_service,
            "bound_edit_source_to_schema",
            lambda data: (
                source_image
                if data == b"cached-image-bytes"
                else {"type": "image", "data": "bmV3ZXI=", "mimeType": "image/png"}
            ),
        )
        fetch_source = AsyncMock(
            side_effect=AssertionError("provider URL must not be fetched")
        )
        monkeypatch.setattr(image_service, "fetch_edit_source_image", fetch_source)
        dispatcher = AsyncMock(
            return_value={
                "task_kind": "image",
                "task": {
                    "id": "igt-cache-only-confirmed",
                    "status": "pending",
                    "dispatch_id": "dsp-cache-only-confirmed",
                },
                "decision_id": "dsp-cache-only-confirmed",
                "created": True,
            }
        )
        monkeypatch.setattr(
            chat_api.media_task_dispatch,
            "dispatch_media_task",
            dispatcher,
        )

        response = await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message=follow_up,
                local_session_id=session.id,
                client_message_id="cm-contextual-edit",
                client_assistant_message_id="cm-contextual-edit-assistant",
            ),
            user,
        )
        payloads = await _sse_payloads(response)
        route = next(
            payload for payload in payloads if payload["type"] == "route_decision"
        )

        assert [payload["type"] for payload in payloads] == [
            "session",
            "route_decision",
            "clarification",
            "done",
        ]
        assert route["predicted_intent"] == ("clarify" if classifier_fails else "image_edit")
        assert route["effective_route"] == "clarify"
        assert route["route_source"] == (
            "classifier" if intervening_chat else "deterministic_rule"
        )
        assert route["reason_code"] == (
            "classifier_failed" if classifier_fails else "confirmation_required"
        )
        assert route["requires_confirmation"] is True
        assert route["parameters"]["source_kind"] == "recent_generated_image"
        assert cached_uris == [generated.image_cache_uri]
        fetch_source.assert_not_awaited()
        persisted = db.get(ChatDispatchDecision, route["decision_id"])
        assert persisted is not None
        assert json.loads(persisted.decision_json)["source_task_id"] == generated.id

        if intervening_chat:
            for index in range(4):
                later_messages = chat_repo.append_messages(
                    db,
                    session,
                    [
                        {"role": "user", "content": f"生成新的图片 {index}"},
                        {"role": "assistant", "content": "新图片生成完成"},
                    ],
                )
                db.add(
                    ImageGenerationTask(
                        id=f"igt-newer-result-{index}",
                        owner_user_id=user.id,
                        chat_session_id=session.id,
                        user_message_id=later_messages[0].id,
                        assistant_message_id=later_messages[1].id,
                        operation="generate",
                        model=generated.model,
                        aspect_ratio="1:1",
                        resolution="1k",
                        prompt=f"生成新的图片 {index}",
                        status="succeeded",
                        image_cache_uri=f"minio://generated-images/newer-{index}.png",
                        created_at=generated.created_at + timedelta(minutes=index + 1),
                        updated_at=now(),
                        completed_at=now(),
                    )
                )
            db.commit()

        confirmed = await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message=follow_up,
                local_session_id=session.id,
                client_message_id="cm-contextual-edit",
                client_assistant_message_id="cm-contextual-edit-assistant",
                decision_id=str(route["decision_id"]),
                final_intent="image_edit",
            ),
            user,
        )
        confirmed_payloads = await _sse_payloads(confirmed)

        assert [payload["type"] for payload in confirmed_payloads] == [
            "session",
            "route_decision",
            "media_task",
            "done",
        ]
        dispatcher.assert_awaited_once()
        assert dispatcher.await_args.kwargs["decision_id"] == route["decision_id"]
        assert dispatcher.await_args.kwargs["source_image"] == source_image
        assert cached_uris == [generated.image_cache_uri, generated.image_cache_uri]
        fetch_source.assert_not_awaited()
        vision_route.assert_not_called()
        pending_images.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_source_confirmation_cannot_dispatch_a_later_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        monkeypatch.setattr(settings, "linkmind_enabled", False)
        monkeypatch.setattr(
            media_intent_router,
            "classify_with_llm",
            lambda **_kwargs: (
                media_intent_router.MediaClassifierOutput(
                    predicted_intent="image_edit", confidence_band="high"
                ),
                {},
                "test-classifier",
            ),
        )
        dispatcher = AsyncMock(
            side_effect=AssertionError("An unbound confirmation must not dispatch")
        )
        monkeypatch.setattr(media_task_dispatch, "dispatch_media_task", dispatcher)
        monkeypatch.setattr(
            media_task_dispatch,
            "resolve_succeeded_image_edit_source",
            AsyncMock(
                return_value={
                    "type": "image",
                    "data": "bGF0ZXI=",
                    "mimeType": "image/png",
                }
            ),
        )
        request = chat_api.ChatMessageRequest(
            message="再把M换成W",
            local_session_id=session.id,
            client_message_id="cm-no-original-source",
            client_assistant_message_id="cm-no-original-source-assistant",
        )
        initial = await _sse_payloads(await chat_api.stream_chat_message(request, user))
        initial_route = next(p for p in initial if p["type"] == "route_decision")
        assert initial_route["reason_code"] == "missing_source_image"

        later_messages = chat_repo.append_messages(
            db,
            session,
            [
                {"role": "user", "content": "主体颜色改成紫色"},
                {"role": "assistant", "content": "新图片编辑完成"},
            ],
        )
        db.add(
            ImageGenerationTask(
                id="igt-later-result",
                owner_user_id=user.id,
                chat_session_id=session.id,
                user_message_id=later_messages[0].id,
                assistant_message_id=later_messages[1].id,
                operation="edit",
                model="wan2.7-image-pro",
                aspect_ratio="source",
                resolution="1k",
                prompt="主体颜色改成紫色",
                status="succeeded",
                image_cache_uri="minio://dgmeta/image-generation/results/usr_test/later",
                created_at=now(),
                updated_at=now(),
                completed_at=now(),
            )
        )
        db.commit()
        confirmation = request.model_copy(
            update={
                "decision_id": initial_route["decision_id"],
                "final_intent": "image_edit",
            }
        )
        confirmed = await _sse_payloads(
            await chat_api.stream_chat_message(confirmation, user)
        )
        route = next(p for p in confirmed if p["type"] == "route_decision")
        assert route["effective_route"] == "clarify"
        assert route["reason_code"] == "missing_source_image"
        assert not any(p["type"] == "media_task" for p in confirmed)
        dispatcher.assert_not_awaited()
        persisted = db.get(ChatDispatchDecision, initial_route["decision_id"])
        assert persisted is not None
        assert json.loads(persisted.decision_json).get("source_task_id") is None
        replayed = await _sse_payloads(
            await chat_api.stream_chat_message(confirmation, user)
        )
        replayed_route = next(p for p in replayed if p["type"] == "route_decision")
        assert replayed_route["effective_route"] == "clarify"
        assert not any(p["type"] == "media_task" for p in replayed)
        dispatcher.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_route_persists_clarification_then_dispatches_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
        monkeypatch.setattr(
            chat_api,
            "create_capability_token",
            lambda **_kwargs: "capability-token",
        )
        dispatcher = AsyncMock(
            return_value={
                "task_kind": "image",
                "task": {
                    "id": "igt-chat-confirm",
                    "status": "pending",
                    "dispatch_id": "dsp-chat-confirm",
                },
                "decision_id": "dsp-chat-confirm",
                "created": True,
            }
        )
        monkeypatch.setattr(
            chat_api.media_task_dispatch,
            "dispatch_media_task",
            dispatcher,
        )

        initial = await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message="生成一张产品海报",
                local_session_id=session.id,
                client_message_id="cm-chat-confirm",
                client_assistant_message_id="cm-chat-confirm-assistant",
            ),
            user,
        )
        initial_payloads = await _sse_payloads(initial)
        route = next(
            payload
            for payload in initial_payloads
            if payload["type"] == "route_decision"
        )
        assert [payload["type"] for payload in initial_payloads] == [
            "session",
            "route_decision",
            "clarification",
            "done",
        ]
        assert initial_payloads[-1]["server_persisted"] is True
        assert dispatcher.await_count == 0
        db.expire_all()
        persisted_session = chat_repo.get_owned_session(db, session.id, user.id)
        assert persisted_session is not None
        detail = chat_api._session_detail_response(persisted_session, db)
        persisted_assistant = next(
            message
            for message in detail.messages
            if message.id == "cm-chat-confirm-assistant"
        )
        assert persisted_assistant.dispatch_decision is not None
        assert persisted_assistant.dispatch_decision.status == "awaiting_confirmation"
        assert persisted_assistant.dispatch_decision.clarification_message
        assert persisted_assistant.dispatch_decision.clarification_choices

        confirmed = await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message="生成一张产品海报",
                local_session_id=session.id,
                client_message_id="cm-chat-confirm",
                client_assistant_message_id="cm-chat-confirm-assistant",
                decision_id=str(route["decision_id"]),
                final_intent="image_generate",
            ),
            user,
        )
        confirmed_payloads = await _sse_payloads(confirmed)

        assert [payload["type"] for payload in confirmed_payloads] == [
            "session",
            "route_decision",
            "media_task",
            "done",
        ]
        assert dispatcher.await_count == 1
        assert dispatcher.await_args.kwargs["decision_id"] == route["decision_id"]
        persisted_messages = list(
            db.scalars(select(ChatMessage).where(ChatMessage.session_id == session.id))
        )
        assert [message.id for message in persisted_messages] == [
            "cm-chat-confirm",
            "cm-chat-confirm-assistant",
        ]


@pytest.mark.asyncio
async def test_confirm_route_chat_override_replaces_persisted_clarification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.services.pi_runtime.runner import PiRunResult

    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        monkeypatch.setattr(settings, "media_intent_mode", "confirm")
        monkeypatch.setattr(
            settings, "pi_nexus_capability_base_url", "http://nexus.test"
        )
        monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_args: [])
        monkeypatch.setattr(
            chat_api,
            "create_capability_token",
            lambda **_kwargs: "capability-token",
        )

        class FakePiClient:
            effective_model = "test-chat-model"

            def __init__(self, **_kwargs):
                pass

            async def chat_async(self, message, **_kwargs):
                assert message.startswith("生成一张产品海报\n\n【本轮处理约束】")
                assert "请仅用文字回答" in message
                assert "不要调用工具执行这些媒体动作" in message
                return (
                    PiRunResult(
                        reply="下面只提供文字版海报方案。",
                        stats={},
                        state={},
                        events=[],
                        token_usage={
                            "prompt_tokens": 8,
                            "completion_tokens": 6,
                            "total_tokens": 14,
                        },
                    ),
                    object(),
                )

        monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
        initial = await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message="生成一张产品海报",
                local_session_id=session.id,
                client_message_id="cm-chat-override",
                client_assistant_message_id="cm-chat-override-assistant",
            ),
            user,
        )
        initial_payloads = await _sse_payloads(initial)
        route = next(
            payload
            for payload in initial_payloads
            if payload["type"] == "route_decision"
        )

        override = await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message="生成一张产品海报",
                local_session_id=session.id,
                client_message_id="cm-chat-override",
                client_assistant_message_id="cm-chat-override-assistant",
                decision_id=str(route["decision_id"]),
                final_intent="chat",
            ),
            user,
        )
        override_payloads = await _sse_payloads(override)

        assert [payload["type"] for payload in override_payloads] == [
            "session",
            "route_decision",
            "done",
        ]
        assert override_payloads[-1]["reply"] == "下面只提供文字版海报方案。"
        assert override_payloads[-1]["server_persisted"] is True
        db.expire_all()
        messages = list(
            db.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == session.id)
                .order_by(ChatMessage.created_at)
            )
        )
        assert [message.id for message in messages] == [
            "cm-chat-override",
            "cm-chat-override-assistant",
        ]
        assert messages[-1].content == "下面只提供文字版海报方案。"
        assert messages[-1].dispatch_id == route["decision_id"]


def test_dispatch_metrics_aggregate_usage_and_correction_without_payloads(
    tmp_path: Path,
):
    with _configured_db(tmp_path) as (db, user):
        session = _create_session(db, user)
        first, _ = chat_dispatch.get_or_create_dispatch_decision(
            db,
            owner_user_id=user.id,
            chat_session_id=session.id,
            client_message_id="cm-metric-first",
            predicted_intent="image_generate",
            effective_route="chat",
            route_source="classifier",
            confidence_band="medium",
            reason_code="classifier_prediction",
            classifier_model="test-model",
            classifier_version="media-intent-v1",
            classifier_usage_json={
                "input_tokens": 11,
                "output_tokens": 4,
                "cost": "0.015",
            },
            decision_json={"requires_confirmation": False},
            prompt_digest=chat_dispatch.prompt_digest("secret prompt"),
            attachment_digest=chat_dispatch.attachment_digest(
                {"data": "secret-base64"}
            ),
        )
        chat_dispatch.record_dispatch_feedback(
            first,
            corrected_intent="video_generate",
        )
        chat_dispatch.get_or_create_dispatch_decision(
            db,
            owner_user_id=user.id,
            chat_session_id=session.id,
            client_message_id="cm-metric-second",
            predicted_intent="chat",
            effective_route="chat",
            route_source="deterministic_rule",
            confidence_band="high",
            reason_code="consultation_request",
            classifier_version="media-intent-v1",
            prompt_digest=chat_dispatch.prompt_digest("another secret"),
            attachment_digest=chat_dispatch.attachment_digest(None),
        )
        db.commit()

        metrics = chat_dispatch.aggregate_dispatch_metrics(db)

        assert metrics["total"] == 2
        assert metrics["reported_wrong"] == 1
        assert metrics["corrected_intents"] == {"video_generate": 1}
        assert metrics["classifier_usage"] == {
            "input_tokens": 11,
            "output_tokens": 4,
            "cost": "0.015",
        }
        serialized = json.dumps(metrics, ensure_ascii=False)
        assert "secret prompt" not in serialized
        assert "secret-base64" not in serialized
