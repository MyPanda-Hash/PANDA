"""Tests for LLM-summarized chat session titles and their freeze semantics."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models import Base
from app.models.chat import ChatMessage, ChatSession
from app.repositories import chat as chat_repo
from app.services.chat_title_service import (
    _clean_title,
    _extract_text,
    _load_turns,
    ChatTitleSummarizeError,
    summarize_session_title,
)
from app.services.pi_local import LocalPiEndpoint


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session



@pytest.fixture
def title_endpoint(monkeypatch):
    endpoint = LocalPiEndpoint(
        endpoint_id="title-endpoint",
        provider="openai_compatible",
        adapter="openai_compatible",
        api_protocol="chat_completions",
        base_url="https://title.invalid/v1",
        model="reasoning-model",
        api_key="private-api-key",
        api_version="",
    )
    monkeypatch.setattr(
        "app.services.chat_title_service.resolve_local_pi_endpoint",
        lambda *args, **kwargs: endpoint,
    )
    return endpoint

def _user(session: Session, user_id: str = "usr_title_test") -> str:
    from datetime import datetime, timezone

    from app.core.security import hash_password
    from app.models.auth import User

    existing = session.scalar(select(User).where(User.id == user_id))
    if existing is not None:
        return user_id
    session.add(
        User(
            id=user_id,
            username=user_id,
            display_name="标题测试用户",
            password_hash=hash_password("secret-password"),
            status="active",
            first_login=False,
            created_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    return user_id


def _stub_message(
    session: Session,
    *,
    session_id: str,
    message_id: str,
    owner_user_id: str,
    role: str,
    content: str,
    created_at: str,
) -> ChatMessage:
    from datetime import datetime

    message = ChatMessage(
        id=message_id,
        session_id=session_id,
        owner_user_id=owner_user_id,
        role=role,
        content=content,
        parts_json="[]",
        space_ids_json="[]",
        document_ids_json="[]",
        references_json="[]",
        session_references_json="[]",
        writing_references_json="[]",
        images_json="[]",
        usage=None,
        # SQLite stores naive datetimes; keep stub timestamps naive to match
        # rows read back through the session (chat_repo.now() is aware UTC but
        # sqlite DateTime(timezone=True) round-trips without tz).
        created_at=datetime.fromisoformat(created_at).replace(tzinfo=None),
    )
    session.add(message)
    return message


class TestCreateAndForkFlags:
    def test_create_session_starts_unfinalized(self, db_session):
        user_id = _user(db_session)
        session = chat_repo.create_session(
            db_session,
            owner_user_id=user_id,
            title="新对话",
        )
        db_session.flush()
        assert session.title == "新对话"
        assert session.title_finalized is False

    def test_fork_inherits_finalized_title(self, db_session):
        user_id = _user(db_session)
        source = chat_repo.create_session(
            db_session,
            owner_user_id=user_id,
            title="源会话",
        )
        _stub_message(
            db_session,
            session_id=source.id,
            message_id="src-user-1",
            owner_user_id=user_id,
            role="user",
            content="用户问题",
            created_at="2026-09-04T10:00:00+00:00",
        )
        assistant = _stub_message(
            db_session,
            session_id=source.id,
            message_id="src-assistant-1",
            owner_user_id=user_id,
            role="assistant",
            content="助手回复",
            created_at="2026-09-04T10:00:01+00:00",
        )
        db_session.flush()
        chat_repo.finalize_session_title(
            db_session,
            source,
            title="已总结标题",
        )
        assert source.title_finalized is True

        forked = chat_repo.fork_session(
            db_session,
            source_session=source,
            owner_user_id=user_id,
            fork_message_id=assistant.id,
        )
        db_session.flush()
        assert forked.title.startswith("已总结标题")
        assert forked.title_finalized is True


class TestFreezeSemantics:
    def test_update_ignores_title_once_finalized(self, db_session):
        user_id = _user(db_session)
        session = chat_repo.create_session(
            db_session,
            owner_user_id=user_id,
            title="新对话",
        )
        db_session.flush()
        chat_repo.finalize_session_title(db_session, session, title="总结标题")
        chat_repo.update_session(db_session, session, title="被忽略的回流标题")
        db_session.expire_all()
        reloaded = db_session.get(ChatSession, session.id)
        assert reloaded.title == "总结标题"
        assert reloaded.title_finalized is True

    def test_update_override_replaces_finalized_title(self, db_session):
        user_id = _user(db_session)
        session = chat_repo.create_session(
            db_session,
            owner_user_id=user_id,
            title="新对话",
        )
        db_session.flush()
        chat_repo.finalize_session_title(db_session, session, title="总结标题")
        chat_repo.update_session(
            db_session,
            session,
            title="用户手动改名",
            override_frozen_title=True,
        )
        db_session.expire_all()
        reloaded = db_session.get(ChatSession, session.id)
        assert reloaded.title == "用户手动改名"
        assert reloaded.title_finalized is True

    def test_update_allows_title_before_finalization(self, db_session):
        user_id = _user(db_session)
        session = chat_repo.create_session(
            db_session,
            owner_user_id=user_id,
            title="新对话",
        )
        db_session.flush()
        chat_repo.update_session(db_session, session, title="首轮临时标题")
        db_session.expire_all()
        reloaded = db_session.get(ChatSession, session.id)
        assert reloaded.title == "首轮临时标题"
        assert reloaded.title_finalized is False


class TestFinalizeSessionTitle:
    def test_finalize_once_is_idempotent(self, db_session):
        user_id = _user(db_session)
        session = chat_repo.create_session(
            db_session,
            owner_user_id=user_id,
            title="新对话",
        )
        db_session.flush()
        first = chat_repo.finalize_session_title(
            db_session,
            session,
            title="标题 A",
        )
        second = chat_repo.finalize_session_title(
            db_session,
            session,
            title="标题 B",
        )
        assert first == "标题 A"
        assert second is None
        db_session.expire_all()
        reloaded = db_session.get(ChatSession, session.id)
        assert reloaded.title == "标题 A"
        assert reloaded.title_finalized is True

    def test_finalize_rejects_blank(self, db_session):
        user_id = _user(db_session)
        session = chat_repo.create_session(
            db_session,
            owner_user_id=user_id,
            title="新对话",
        )
        db_session.flush()
        assert chat_repo.finalize_session_title(db_session, session, title="  ") is None
        assert session.title_finalized is False

    def test_finalize_bounds_title_to_255(self, db_session):
        user_id = _user(db_session)
        session = chat_repo.create_session(
            db_session,
            owner_user_id=user_id,
            title="新对话",
        )
        db_session.flush()
        long_title = "长" * 400
        stored = chat_repo.finalize_session_title(db_session, session, title=long_title)
        assert stored == "长" * 255
        assert session.title_finalized is True


class TestTitleRecovery:
    @pytest.mark.parametrize(
        ("scenario", "expected_calls", "expected_title", "finalized"),
        [
            ("recover", 2, "北京古都历史", True),
            ("exhausted", 2, "新对话", False),
            ("http_error", 1, "新对话", False),
            ("manual_before_retry", 1, "用户手工标题", True),
            ("manual_before_save", 2, "用户手工标题", True),
        ],
    )
    def test_background_recovery_preserves_manual_titles(
        self, monkeypatch, db_session, title_endpoint,
        scenario, expected_calls, expected_title, finalized,
    ):
        from sqlalchemy.orm import sessionmaker
        from app.api import chat as chat_api

        factory = sessionmaker(bind=db_session.get_bind())
        monkeypatch.setattr(chat_api, "SessionLocal", factory)
        user_id = _user(db_session)
        session = chat_repo.create_session(
            db_session, owner_user_id=user_id, title="新对话"
        )
        session_id = session.id
        _stub_message(
            db_session, session_id=session_id, message_id="title-message",
            owner_user_id=user_id, role="user", content="北京是几朝古都",
            created_at="2026-10-14T08:41:35+00:00",
        )
        db_session.commit()
        calls = 0

        def provider_post(url, **kwargs):
            nonlocal calls
            calls += 1
            request = httpx.Request("POST", url)
            if scenario == "http_error":
                return httpx.Response(401, request=request)
            if (scenario == "manual_before_retry" and calls == 1) or (
                scenario == "manual_before_save" and calls == 2
            ):
                with factory() as db:
                    current = chat_repo.get_owned_session(db, session_id, user_id)
                    chat_repo.update_session(
                        db, current, title="用户手工标题", override_frozen_title=True
                    )
                    db.commit()
            content = "" if calls == 1 or scenario == "exhausted" else "北京古都历史"
            return httpx.Response(
                200, request=request,
                json={"choices": [{"message": {"content": content}}]},
            )

        monkeypatch.setattr(httpx, "post", provider_post)
        result = chat_api._summarize_session_title_once(
            user_id=user_id, session_id=session_id, model=None
        )
        assert calls == expected_calls
        assert result == ("北京古都历史" if scenario == "recover" else None)
        with factory() as db:
            stored = chat_repo.get_owned_session(db, session_id, user_id)
            assert stored.title == expected_title
            assert stored.title_finalized is finalized


class TestTitleServiceHelpers:
    def test_clean_title_strips_quotes_and_bounds(self):
        assert _clean_title('"关于预算的讨论"') == "关于预算的讨论"
        assert _clean_title("《预算审批流程》") == "预算审批流程"
        assert _clean_title("  多  个  空格  ") == "多 个 空格"
        assert _clean_title("   ") is None
        raw = "字" * 100
        assert len(_clean_title(raw)) == 60

    def test_load_turns_keeps_roles_and_drops_others(self):
        class Item:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        turns = _load_turns(
            messages=[
                Item("user", "第一问"),
                Item("assistant", "第一答"),
                Item("system", "忽略我"),
                Item("tool", "忽略我"),
                Item("user", ""),
            ]
        )
        assert turns == ["user: 第一问", "assistant: 第一答"]

    def test_load_turns_clips_long_content(self):
        class Item:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        turns = _load_turns(messages=[Item("user", "甲" * 3000)])
        assert len(turns) == 1
        assert len(turns[0]) < 2200
        assert turns[0].endswith("…")

    def test_extract_text_three_protocols(self):
        responses = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Responses 标题"}],
                }
            ]
        }
        assert _extract_text(responses, "responses") == "Responses 标题"

        messages = {"content": [{"type": "text", "text": "Messages 标题"}]}
        assert _extract_text(messages, "messages") == "Messages 标题"

        completions = {
            "choices": [
                {"message": {"role": "assistant", "content": "Completions 标题"}}
            ]
        }
        assert _extract_text(completions, "chat_completions") == "Completions 标题"

    def test_summarize_without_transcript_raises(self):
        with pytest.raises(ChatTitleSummarizeError):
            summarize_session_title(user_id="u", messages=[])

    def test_summarize_cleans_and_bounds(self, monkeypatch):
        class FakeEndpoint:
            def __init__(self):
                self.base_url = "https://llm.invalid"
                self.model = "model-x"
                self.provider = "openai_compatible"
                self.adapter = "openai_compatible"
                self.api_protocol = "chat_completions"
                self.api_key = "secret-key"
                self.api_version = ""
                self.endpoint_id = "ep-1"

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "choices": [
                        {"message": {"content": "“这是总结标题”"}},
                    ]
                }

            def raise_for_status(self):
                return None

        import httpx

        monkeypatch.setattr(
            "app.services.chat_title_service.resolve_local_pi_endpoint",
            lambda *a, **k: FakeEndpoint(),
        )
        monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse())

        class Item:
            def __init__(self, role, content):
                self.role = role
                self.content = content

        title = summarize_session_title(
            user_id="u",
            messages=[Item("user", "帮我看看预算"), Item("assistant", "好的")],
        )
        assert title == "这是总结标题"

    def test_reasoning_completions_can_produce_visible_title(
        self, monkeypatch, title_endpoint
    ):
        def provider_post(url, *, json, **kwargs):
            exhausted = json["max_tokens"] <= 256
            return httpx.Response(
                200, request=httpx.Request("POST", url),
                json={"choices": [{
                    "finish_reason": "length" if exhausted else "stop",
                    "message": {
                        "content": "" if exhausted else "北京古都历史",
                        "reasoning_content": "不能作为标题或日志的推理正文",
                    },
                }]},
            )

        monkeypatch.setattr(httpx, "post", provider_post)
        assert summarize_session_title(
            user_id="u",
            messages=[SimpleNamespace(role="user", content="北京是几朝古都")],
        ) == "北京古都历史"

    @pytest.mark.parametrize(
        ("protocol", "body", "reason"),
        [
            ("chat_completions", {
                "choices": [{
                    "finish_reason": "length",
                    "message": {"content": "", "reasoning_content": "private-reasoning"},
                }],
            }, "length"),
            ("responses", {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [{"type": "reasoning", "summary": "private-reasoning"}],
            }, "max_output_tokens"),
            ("messages", {
                "stop_reason": "private-reason",
                "content": [{"type": "thinking", "thinking": "private-reasoning"}],
            }, "unknown"),
        ],
    )
    def test_empty_title_logs_only_safe_diagnostics(
        self, monkeypatch, caplog, title_endpoint, protocol, body, reason
    ):
        from dataclasses import replace

        endpoint = replace(title_endpoint, api_protocol=protocol)
        monkeypatch.setattr(
            "app.services.chat_title_service.resolve_local_pi_endpoint",
            lambda *args, **kwargs: endpoint,
        )
        body = {**body, "usage": {
            "completion_tokens": 128,
            "completion_tokens_details": {"reasoning_tokens": 128},
            "private-usage": "private-usage-value",
        }}
        monkeypatch.setattr(
            httpx, "post",
            lambda url, **kwargs: httpx.Response(
                200, request=httpx.Request("POST", url), json=body
            ),
        )
        with pytest.raises(ChatTitleSummarizeError) as failure:
            summarize_session_title(
                user_id="u",
                messages=[SimpleNamespace(role="user", content="private-question")],
            )
        assert failure.value.retryable is True
        record = next(
            item for item in caplog.records
            if item.getMessage().startswith("chat_title.empty_response")
        )
        diagnostics = json.loads(record.getMessage().split("diagnostics=", 1)[1])
        assert diagnostics["finish_reason"] == reason
        assert diagnostics["reasoning_present"] is True
        assert diagnostics["usage"]["reasoning_tokens"] == 128
        assert "private-" not in caplog.text


    @pytest.mark.parametrize(
        ("adapter", "base_url", "expected_path", "user_text"),
        [
            (
                "azure_openai",
                "https://resource.openai.azure.com",
                "/openai/v1/responses",
                "这上面显示的是什么？",
            ),
            (
                "azure_openai",
                "https://resource.openai.azure.com/openai/v1",
                "/openai/v1/responses",
                "",
            ),
            (
                "azure_openai",
                "https://gateway.invalid/custom/v1",
                "/custom/v1/responses",
                "识别图片",
            ),
            (
                "openai",
                "https://api.openai.com/v1",
                "/v1/responses",
                "识别图片",
            ),
        ],
    )
    def test_reasoning_responses_can_summarize_image_turn(
        self, monkeypatch, adapter, base_url, expected_path, user_text
    ):
        endpoint = LocalPiEndpoint(
            endpoint_id="title-endpoint",
            provider=adapter,
            adapter=adapter,
            api_protocol="responses",
            base_url=base_url,
            model="reasoning-model",
            api_key="fixture-key",
            api_version="v1" if adapter == "azure_openai" else "",
        )

        def provider_post(url, *, json, **kwargs):
            request = httpx.Request("POST", url, json=json)
            if request.url.path != expected_path:
                return httpx.Response(404, request=request)
            if "temperature" in json:
                return httpx.Response(
                    400,
                    request=request,
                    json={"error": {"message": "Unsupported parameter: temperature"}},
                )
            # Reasoning can exhaust a tiny allowance before producing a title.
            if json["max_output_tokens"] <= 256:
                return httpx.Response(
                    200,
                    request=request,
                    json={"status": "incomplete", "output": []},
                )
            return httpx.Response(
                200,
                request=request,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "新增选项检测弹窗说明"}
                            ],
                        }
                    ],
                },
            )

        monkeypatch.setattr(
            "app.services.chat_title_service.resolve_local_pi_endpoint",
            lambda *args, **kwargs: endpoint,
        )
        monkeypatch.setattr(httpx, "post", provider_post)
        title = summarize_session_title(
            user_id="image-user",
            messages=[
                SimpleNamespace(
                    role="user",
                    content=user_text,
                    images_json=json.dumps([{"data": "private-image-bytes"}]),
                ),
                SimpleNamespace(
                    role="assistant",
                    content="图中是新增选项弹窗，可选择检测后新增或跳过检测直接新增。",
                ),
            ],
        )
        assert title == "新增选项检测弹窗说明"


def test_sqlite_bootstrap_backfills_existing_titles_and_keeps_new_default(
    tmp_path,
):
    from app.core.database import _ensure_sqlite_chat_session_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-chat.sqlite3'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE chat_sessions ("
                "id VARCHAR(64) PRIMARY KEY, title VARCHAR(255) NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO chat_sessions (id, title) "
                "VALUES ('existing', 'Existing title')"
            )
        )

    _ensure_sqlite_chat_session_columns(engine)

    with engine.begin() as connection:
        existing = connection.execute(
            text(
                "SELECT messages_revision, title_finalized "
                "FROM chat_sessions WHERE id = 'existing'"
            )
        ).one()
        connection.execute(
            text("INSERT INTO chat_sessions (id, title) VALUES ('new', 'New title')")
        )
        created = connection.execute(
            text(
                "SELECT messages_revision, title_finalized "
                "FROM chat_sessions WHERE id = 'new'"
            )
        ).one()

    assert tuple(existing) == (0, 1)
    assert tuple(created) == (0, 0)
