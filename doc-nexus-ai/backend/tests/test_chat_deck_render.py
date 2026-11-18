"""Historical template results stay readable; no template work can restart."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select

from app.api import chat as chat_api, files as files_api
from app.core import database
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import now
from app.models import Base
from app.models.auth import User
from app.models.chat import ChatDispatchDecision, ChatMessage, ChatSession
from app.models.chat_deck_render import ChatDeckRender
from app.models.writing import WritingDocTemplate, WritingDocTemplateRevision
from app.services.presentation_deck import render_project_pptx


@pytest.fixture()
def render_env(tmp_path, monkeypatch):
    original_url, original_path = settings.database_url, settings.db_path
    path = tmp_path / "historical-render.sqlite3"
    settings.db_path, settings.database_url = path, f"sqlite:///{path}"
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    stamp = now()
    owner = User(id="usr-owner", username="owner", display_name="Owner", password_hash="x",
                 role="user", status="active", created_at=stamp)
    other = User(id="usr-other", username="other", display_name="Other", password_hash="x",
                 role="admin", status="active", created_at=stamp)
    raw, _ = render_project_pptx("历史成果", [("实施安排", "保持历史原字节")])
    href = "/api/files/chat-deck-renders/cdr-history/history.pptx"
    metadata = {"type": "presentation_template", "revision_id": "rev-history"}
    with SessionLocal() as db:
        db.add_all([owner, other])
        db.flush()
        db.add(ChatSession(id="chs-history", owner_user_id=owner.id, title="历史会话",
                           title_finalized=True, messages_revision=3, created_at=stamp, updated_at=stamp))
        db.add(WritingDocTemplate(id="tpl-history", name="历史模板", created_by_user_id=owner.id,
                                 create_idempotency_key="history", create_request_sha256="a" * 64,
                                 created_at=stamp, updated_at=stamp))
        db.flush()
        db.add(WritingDocTemplateRevision(id="rev-history", template_id="tpl-history", revision_no=1,
                                          created_by_user_id=owner.id, request_sha256="b" * 64,
                                          source_format="pptx", state="published", created_at=stamp, updated_at=stamp))
        db.add(ChatMessage(id="history-user", session_id="chs-history", owner_user_id=owner.id,
                           role="user", content="按固定主题生成", parts_json=json.dumps([metadata]),
                           created_at=stamp))
        db.add(ChatMessage(id="history-assistant", session_id="chs-history", owner_user_id=owner.id,
                           role="assistant", content=f"[历史成果]({href})",
                           parts_json=json.dumps([metadata, {"type": "file", "href": href,
                                                              "name": "history.pptx"}]),
                           usage=json.dumps({"status": "completed", "model": "chat-model", "total_tokens": 7}),
                           created_at=stamp + timedelta(seconds=1)))
        db.flush()
        db.add(ChatDeckRender(id="cdr-history", owner_user_id=owner.id, session_id="chs-history",
                             message_id="history-assistant", template_revision_id="rev-history",
                             source_task_id="historical-source", source_file_path="old.deck.json",
                             source_sha256="c" * 64, status="ready", output_name="history.pptx",
                             output_mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                             output_content=raw, output_size=len(raw), output_sha256=hashlib.sha256(raw).hexdigest(),
                             created_at=stamp, updated_at=stamp, completed_at=stamp))
        db.commit()
    calls = []

    def no_model(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("retired template requests must never call a model")

    monkeypatch.setattr(chat_api, "LocalPiAgentClient", no_model)
    monkeypatch.setattr(chat_api, "route_media_intent", no_model)
    api = FastAPI()
    api.include_router(files_api.router)
    api.include_router(chat_api.router)
    current = {"user": owner}
    api.dependency_overrides[chat_api.current_user] = lambda: current["user"]
    api.dependency_overrides[files_api.current_user_from_bearer_or_cookie] = lambda: current["user"]
    try:
        with TestClient(api) as client:
            yield SimpleNamespace(client=client, current=current, owner=owner, other=other,
                                  raw=raw, href=href.removeprefix("/api"), calls=calls)
    finally:
        database.reset_database(path)
        settings.database_url, settings.db_path = original_url, original_path


def _request(env, **changes):
    payload = {"message": "按固定主题生成", "local_session_id": "chs-history",
               "client_message_id": "history-user", "client_assistant_message_id": "history-assistant"}
    payload.update(changes)
    return env.client.post("/chat/messages", json=payload)


def test_historical_render_keeps_original_bytes_and_owner_acl(render_env):
    env = render_env
    response = env.client.get(env.href)
    assert response.status_code == 200 and response.content == env.raw
    assert env.client.get(env.href, params={"preview": "true"}).status_code == 415
    env.current["user"] = env.other
    assert env.client.get(env.href).status_code == 404  # no admin bypass
    env.current["user"] = env.owner
    with SessionLocal() as db:
        row = db.get(ChatDeckRender, "cdr-history")
        assert row is not None
        row.status = "failed"
        db.commit()
    assert env.client.get(env.href).status_code == 409


@pytest.mark.parametrize("explicit", [False, True])
def test_completed_template_turn_replays_without_render_or_model(render_env, explicit):
    env = render_env
    response = _request(env, **({"template_revision_id": "rev-history"} if explicit else {}))
    assert response.status_code == 200, response.text
    done = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")][-1]
    assert done["type"] == "done"
    assert done["template_revision_id"] == "rev-history"
    assert "history.pptx" in done["reply"]
    assert "template_delivery" not in done
    assert env.calls == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ChatDeckRender)) == 1
        assert db.scalar(select(func.count()).select_from(ChatDispatchDecision)) == 0
        row = db.get(ChatDeckRender, "cdr-history")
        session = db.get(ChatSession, "chs-history")
        assert row is not None and session is not None
        assert row.output_content == env.raw and session.messages_revision == 3
    assert _request(env, message="偷换任务").status_code == 409
    assert _request(env, template_revision_id="different").status_code == 409


@pytest.mark.parametrize("attempt", ["new", "retry", "regenerate"])
def test_retired_template_requests_never_create_work_or_call_model(render_env, attempt):
    env = render_env
    if attempt == "new":
        response = _request(env, client_message_id="new-user", client_assistant_message_id="new-assistant",
                            template_revision_id="rev-history")
    elif attempt == "retry":
        with SessionLocal() as db:
            message = db.get(ChatMessage, "history-assistant")
            assert message is not None
            message.usage = json.dumps({"status": "failed", "model": "chat-model"})
            db.commit()
        response = _request(env)  # omitting the deprecated field must not bypass retirement
    else:
        response = _request(env, client_assistant_message_id="new-assistant",
                            regenerate_from_message_id="history-assistant")
    assert response.status_code == 410 and "演示模板库已停用" in response.text
    assert env.calls == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ChatMessage)) == 2
        assert db.scalar(select(func.count()).select_from(ChatDeckRender)) == 1
        assert db.scalar(select(func.count()).select_from(ChatDispatchDecision)) == 0
    assert env.client.get(env.href).content == env.raw


@pytest.mark.parametrize("selected_skill", [None, "pptx"])
def test_historical_regeneration_digest_and_terminal_replay_stay_stable(render_env, selected_skill):
    from app.repositories import chat_dispatch
    from app.schemas.chat import ChatMessageRequest

    env = render_env
    message = "/skill:pptx\n按固定主题生成" if selected_skill else "按固定主题生成"
    body = ChatMessageRequest(
        message=message, local_session_id="chs-history", selected_skill=selected_skill,
        client_message_id="history-user", client_assistant_message_id="history-regen",
        regenerate_from_message_id="history-assistant", template_revision_id="rev-history",
    )
    legacy_payload = body.model_dump(mode="json", exclude={"session_id"})
    # This field was part of historical request identity, including when null.
    legacy_payload["template_revision_id"] = "rev-history"
    legacy_payload["selected_skill"] = None
    legacy_digest = chat_dispatch.digest_payload(legacy_payload)
    assert chat_api._regeneration_request_digest(
        body.model_copy(update={"selected_skill": None}), None,
    ) == legacy_digest
    decision_id = chat_api._regeneration_decision_id("history-regen")
    with SessionLocal() as db:
        user_message = db.get(ChatMessage, "history-user")
        assert user_message is not None
        user_message.content = message
        db.add(ChatDispatchDecision(
            id=decision_id, owner_user_id=env.owner.id, chat_session_id="chs-history",
            client_message_id=chat_api._regeneration_execution_key(body),
            predicted_intent="chat", effective_route="chat", route_source="explicit",
            confidence_band="high", reason_code="chat_regeneration", status="dispatched",
            decision_json=json.dumps({"request_digest": legacy_digest}),
            created_at=now(), updated_at=now(),
        ))
        db.flush()
        db.add(ChatMessage(
            id="history-regen", session_id="chs-history", owner_user_id=env.owner.id,
            role="assistant", content="历史重新生成结果", dispatch_id=decision_id,
            parts_json=json.dumps([{"type": "presentation_template", "revision_id": "rev-history"}]),
            usage=json.dumps({"status": "completed"}), created_at=now() + timedelta(seconds=2),
        ))
        db.commit()
    replay = _request(env, client_assistant_message_id="history-regen",
                      regenerate_from_message_id="history-assistant", message=message,
                      selected_skill=selected_skill)
    assert replay.status_code == 200, replay.text
    assert "历史重新生成结果" in replay.text
    changed = _request(env, client_assistant_message_id="history-regen",
                       regenerate_from_message_id="history-assistant", model="different-model",
                       message=message, selected_skill=selected_skill)
    assert changed.status_code == 409
    assert env.calls == []
    with SessionLocal() as db:
        decision = db.get(ChatDispatchDecision, decision_id)
        assert decision is not None
        assert chat_dispatch.decision_request_digest(decision) == legacy_digest
        assert db.scalar(select(func.count()).select_from(ChatDeckRender)) == 1
