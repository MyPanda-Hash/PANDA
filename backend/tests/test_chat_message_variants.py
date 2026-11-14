"""Regenerated-answer variants: grouping, switching, and what they must not leak.

A regenerated answer is stored as a sibling row in the same variant group. The
superseded answer stays in the table on purpose: the user must be able to switch
back to it, and its token usage must keep counting toward cost reporting. That
second constraint is why these are separate rows and not a single row holding a
JSON history — the usage repository aggregates ``chat_messages.usage`` directly.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession
from app.repositories import chat as chat_repo
from app.repositories import usage as usage_repo


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def owner(db_session: Session) -> str:
    from app.core.security import hash_password

    user_id = "usr_variant_test"
    db_session.add(
        User(
            id=user_id,
            username=user_id,
            display_name="版本测试用户",
            password_hash=hash_password("secret-password"),
            status="active",
            first_login=False,
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.flush()
    return user_id


def _session(db: Session, owner_user_id: str) -> ChatSession:
    return chat_repo.create_session(
        db, owner_user_id=owner_user_id, title="版本会话"
    )


def _message(
    db: Session,
    session: ChatSession,
    *,
    message_id: str,
    role: str,
    content: str,
    at: datetime,
    usage: str | None = None,
) -> ChatMessage:
    message = ChatMessage(
        id=message_id,
        session_id=session.id,
        owner_user_id=session.owner_user_id,
        role=role,
        content=content,
        space_ids_json="[]",
        document_ids_json="[]",
        references_json="[]",
        session_references_json="[]",
        writing_references_json="[]",
        images_json="[]",
        created_at=at,
        usage=usage,
    )
    db.add(message)
    db.flush()
    return message


def _turn(db: Session, session: ChatSession, *, index: int, at: datetime):
    """One user question plus its assistant answer."""
    user = _message(
        db,
        session,
        message_id=f"user-{index}",
        role="user",
        content=f"问题 {index}",
        at=at,
    )
    answer = _message(
        db,
        session,
        message_id=f"answer-{index}",
        role="assistant",
        content=f"回答 {index}",
        at=at + timedelta(milliseconds=1),
    )
    return user, answer


def _reload(db: Session, session: ChatSession) -> ChatSession:
    db.expire_all()
    reloaded = chat_repo.get_owned_session(db, session.id, session.owner_user_id)
    assert reloaded is not None
    return reloaded


def test_register_keeps_old_answer_as_inactive_sibling(db_session, owner):
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _, first_answer = _turn(db_session, session, index=1, at=start)
    _message(
        db_session,
        session,
        message_id="regen-1",
        role="assistant",
        content="回答 1（重生成）",
        at=start + timedelta(seconds=5),
    )

    registered = chat_repo.register_message_variant(
        db_session,
        session,
        source_message_id="answer-1",
        new_message_id="regen-1",
    )

    assert registered is True
    reloaded = _reload(db_session, session)
    by_id = {item.id: item for item in reloaded.messages}
    assert by_id["answer-1"].variant_group_id == "answer-1"
    assert by_id["answer-1"].variant_index == 0
    assert by_id["answer-1"].variant_active is False
    assert by_id["regen-1"].variant_group_id == "answer-1"
    assert by_id["regen-1"].variant_index == 1
    assert by_id["regen-1"].variant_active is True
    # Exactly one member of a group may be active.
    assert sum(
        item.variant_active
        for item in reloaded.messages
        if item.variant_group_id == "answer-1"
    ) == 1
    assert first_answer.id == "answer-1"


def test_register_without_activation_keeps_previous_answer_visible(
    db_session, owner
):
    """A failed regeneration must not replace the answer the user was reading."""
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _, _ = _turn(db_session, session, index=1, at=start)
    _message(
        db_session,
        session,
        message_id="regen-failed",
        role="assistant",
        content="本次处理未完成",
        at=start + timedelta(seconds=5),
    )

    chat_repo.register_message_variant(
        db_session,
        session,
        source_message_id="answer-1",
        new_message_id="regen-failed",
        activate=False,
    )

    reloaded = _reload(db_session, session)
    active = chat_repo.active_messages(reloaded.messages)
    assert [item.id for item in active] == ["user-1", "answer-1"]
    # The failed attempt is still recorded, so its cost keeps counting.
    assert {item.id for item in reloaded.messages} >= {"answer-1", "regen-failed"}


def test_register_is_idempotent_for_a_retried_regeneration(db_session, owner):
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _, _ = _turn(db_session, session, index=1, at=start)
    _message(
        db_session,
        session,
        message_id="regen-1",
        role="assistant",
        content="回答 1（重生成）",
        at=start + timedelta(seconds=5),
    )

    assert chat_repo.register_message_variant(
        db_session, session, source_message_id="answer-1", new_message_id="regen-1"
    )
    # A retry reuses the same new id, so the source is already inactive.
    assert not chat_repo.register_message_variant(
        db_session, session, source_message_id="answer-1", new_message_id="regen-1"
    )
    reloaded = _reload(db_session, session)
    assert sum(
        item.variant_active
        for item in reloaded.messages
        if item.variant_group_id == "answer-1"
    ) == 1


def test_activate_switches_visible_answer(db_session, owner):
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _, _ = _turn(db_session, session, index=1, at=start)
    _message(
        db_session,
        session,
        message_id="regen-1",
        role="assistant",
        content="回答 1（重生成）",
        at=start + timedelta(seconds=5),
    )
    chat_repo.register_message_variant(
        db_session, session, source_message_id="answer-1", new_message_id="regen-1"
    )

    assert chat_repo.activate_message_variant(
        db_session, session, message_id="answer-1"
    )
    reloaded = _reload(db_session, session)
    assert [item.id for item in chat_repo.active_messages(reloaded.messages)] == [
        "user-1",
        "answer-1",
    ]

    # Switching back is a no-op, not a group with zero active members.
    assert not chat_repo.activate_message_variant(
        db_session, session, message_id="answer-1"
    )
    assert chat_repo.activate_message_variant(
        db_session, session, message_id="regen-1"
    )
    reloaded = _reload(db_session, session)
    assert [item.id for item in chat_repo.active_messages(reloaded.messages)] == [
        "user-1",
        "regen-1",
    ]


def test_activate_rejects_message_without_versions(db_session, owner):
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _turn(db_session, session, index=1, at=start)

    assert not chat_repo.activate_message_variant(
        db_session, session, message_id="answer-1"
    )


def test_activate_is_owner_scoped(db_session, owner):
    from app.core.security import hash_password

    db_session.add(
        User(
            id="usr_other",
            username="usr_other",
            display_name="其他用户",
            password_hash=hash_password("secret-password"),
            status="active",
            first_login=False,
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.flush()
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _turn(db_session, session, index=1, at=start)
    _message(
        db_session,
        session,
        message_id="regen-1",
        role="assistant",
        content="回答 1（重生成）",
        at=start + timedelta(seconds=5),
    )
    chat_repo.register_message_variant(
        db_session, session, source_message_id="answer-1", new_message_id="regen-1"
    )
    other = _reload(db_session, session)
    other.owner_user_id = "usr_other"
    db_session.flush()

    assert not chat_repo.activate_message_variant(
        db_session, session, message_id="answer-1"
    )


def test_session_detail_hides_inactive_variant_and_reports_versions(
    db_session, owner
):
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _turn(db_session, session, index=1, at=start)
    _message(
        db_session,
        session,
        message_id="regen-1",
        role="assistant",
        content="回答 1（重生成）",
        at=start + timedelta(seconds=5),
    )
    chat_repo.register_message_variant(
        db_session, session, source_message_id="answer-1", new_message_id="regen-1"
    )

    detail = chat_repo.session_detail_dict(_reload(db_session, session))

    assert [item["id"] for item in detail["messages"]] == ["user-1", "regen-1"]
    active = detail["messages"][-1]
    assert active["variant"] == {
        "group_id": "answer-1",
        "index": 2,
        "total": 2,
        "active": True,
        "ids": ["answer-1", "regen-1"],
    }
    # A message without versions carries no variant payload at all, so
    # pre-existing data keeps its exact response shape.
    assert detail["messages"][0]["variant"] is None


def test_ordered_chat_messages_excludes_superseded_answer(db_session, owner):
    """Context, token budget, titles and forks must not see the old answer."""
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _turn(db_session, session, index=1, at=start)
    _message(
        db_session,
        session,
        message_id="regen-1",
        role="assistant",
        content="回答 1（重生成）",
        at=start + timedelta(seconds=5),
    )
    chat_repo.register_message_variant(
        db_session, session, source_message_id="answer-1", new_message_id="regen-1"
    )

    reloaded = _reload(db_session, session)
    assert [item.id for item in chat_repo.active_messages(reloaded.messages)] == [
        "user-1",
        "regen-1",
    ]
    # Both rows still exist for switching and cost accounting.
    assert {item.id for item in reloaded.messages} == {
        "user-1",
        "answer-1",
        "regen-1",
    }


def test_session_summary_message_count_counts_active_answers(db_session, owner):
    """Sessions count the answers the user can read, not the superseded rows."""
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _turn(db_session, session, index=1, at=start)
    _message(
        db_session,
        session,
        message_id="regen-1",
        role="assistant",
        content="回答 1（重生成）",
        at=start + timedelta(seconds=5),
    )
    chat_repo.register_message_variant(
        db_session, session, source_message_id="answer-1", new_message_id="regen-1"
    )
    db_session.flush()

    # Both sidebar paths attach a counted subquery instead of loading the rows.
    db_session.expire_all()
    counted = chat_repo.get_owned_session_summary(db_session, session.id, owner)
    assert counted is not None
    assert chat_repo.session_summary_dict(counted)["message_count"] == 2
    listed = chat_repo.list_sessions(db_session, owner)
    assert [item.id for item in listed] == [session.id]
    assert chat_repo.session_summary_dict(listed[0])["message_count"] == 2

    # Sessions loaded without the counted expression fall back to active rows.
    assert chat_repo.session_summary_dict(_reload(db_session, session))["message_count"] == 2


def test_superseded_answer_still_counts_toward_cost(db_session, owner):
    """Every attempt costs tokens; hiding a variant must not hide its cost."""
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _turn(db_session, session, index=1, at=start)
    for index, tokens in ((1, 100), (2, 250)):
        _message(
            db_session,
            session,
            message_id=f"regen-{index}",
            role="assistant",
            content=f"回答 1（重生成 {index}）",
            at=start + timedelta(seconds=5 * index),
            usage=(
                '{"provider": "pi", "status": "completed", "total_tokens": '
                f'{tokens}, "latency_ms": 10, "model": "m"}}'
            ),
        )
    chat_repo.register_message_variant(
        db_session, session, source_message_id="answer-1", new_message_id="regen-1"
    )
    chat_repo.register_message_variant(
        db_session, session, source_message_id="regen-1", new_message_id="regen-2"
    )

    operations = usage_repo._chat_operations(db_session, owner)
    # Both regenerated attempts are still billed even though only one is visible.
    billed = {
        item.id: int(item.usage.get("total_tokens") or 0) for item in operations
    }
    assert billed.get("regen-1") == 100
    assert billed.get("regen-2") == 250


def test_fork_uses_only_active_variants(db_session, owner):
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _turn(db_session, session, index=1, at=start)
    _message(
        db_session,
        session,
        message_id="regen-1",
        role="assistant",
        content="回答 1（重生成）",
        at=start + timedelta(seconds=5),
    )
    chat_repo.register_message_variant(
        db_session, session, source_message_id="answer-1", new_message_id="regen-1"
    )
    source = _reload(db_session, session)

    forked = chat_repo.fork_session(
        db_session,
        source_session=source,
        owner_user_id=owner,
        fork_message_id="regen-1",
    )
    forked = _reload(db_session, forked)

    assert [item.content for item in forked.messages] == ["问题 1", "回答 1（重生成）"]
    assert all(item.variant_group_id is None for item in forked.messages)


def test_replace_preserves_inactive_siblings(db_session, owner):
    """A client snapshot only carries the active answer; history must survive."""
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _turn(db_session, session, index=1, at=start)
    _message(
        db_session,
        session,
        message_id="regen-1",
        role="assistant",
        content="回答 1（重生成）",
        at=start + timedelta(seconds=5),
    )
    chat_repo.register_message_variant(
        db_session, session, source_message_id="answer-1", new_message_id="regen-1"
    )
    reloaded = _reload(db_session, session)

    chat_repo.replace_messages(
        db_session,
        reloaded,
        [
            {
                "id": "user-1",
                "role": "user",
                "content": "问题 1",
                "created_at": start.isoformat(),
            },
            {
                "id": "regen-1",
                "role": "assistant",
                "content": "回答 1（重生成）",
                "created_at": (start + timedelta(seconds=5)).isoformat(),
            },
        ],
        base_revision=reloaded.messages_revision,
    )

    after = _reload(db_session, session)
    by_id = {item.id: item for item in after.messages}
    # The superseded answer survived the snapshot write and stayed switchable.
    assert "answer-1" in by_id
    assert by_id["answer-1"].variant_active is False
    assert by_id["regen-1"].variant_active is True
    assert by_id["regen-1"].variant_group_id == "answer-1"
    assert by_id["regen-1"].variant_index == 1


def test_validate_regenerate_target_rejects_older_answer():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api import chat as chat_api

    messages = [
        SimpleNamespace(id="user-1", role="user"),
        SimpleNamespace(id="answer-1", role="assistant"),
        SimpleNamespace(id="user-2", role="user"),
        SimpleNamespace(id="answer-2", role="assistant"),
    ]

    # The newest answer regenerates, and must resend its own question.
    assert (
        chat_api._validate_regenerate_target(
            target_message_id="answer-2",
            client_message_id="user-2",
            local_messages=messages,
        )
        == "answer-2"
    )

    with pytest.raises(HTTPException) as older:
        chat_api._validate_regenerate_target(
            target_message_id="answer-1",
            client_message_id="user-1",
            local_messages=messages,
        )
    assert older.value.status_code == 409

    with pytest.raises(HTTPException) as mismatched:
        chat_api._validate_regenerate_target(
            target_message_id="answer-2",
            client_message_id="user-1",
            local_messages=messages,
        )
    assert mismatched.value.status_code == 409

    with pytest.raises(HTTPException) as user_role:
        chat_api._validate_regenerate_target(
            target_message_id="user-2",
            client_message_id="user-2",
            local_messages=messages,
        )
    assert user_role.value.status_code == 422

    with pytest.raises(HTTPException) as missing:
        chat_api._validate_regenerate_target(
            target_message_id="nope",
            client_message_id="user-2",
            local_messages=messages,
        )
    assert missing.value.status_code == 404


def test_variant_columns_default_for_pre_existing_rows(db_session, owner):
    """Rows written before this feature must read as single active versions."""
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _, answer = _turn(db_session, session, index=1, at=start)

    stored = db_session.scalar(
        select(ChatMessage).where(ChatMessage.id == answer.id)
    )
    assert stored is not None
    assert stored.variant_group_id is None
    assert stored.variant_index == 0
    assert stored.variant_active is True


def _regenerate_harness(db_session, monkeypatch, messages, runtime_calls=None, *, persist_real=False):
    """Wire stream_chat_message to a fake model and record its persistence calls."""
    from types import SimpleNamespace

    from app.api import chat as chat_api
    from app.core.config import settings
    from app.services.pi_runtime.runner import PiRunResult

    monkeypatch.setattr(settings, "pi_nexus_capability_base_url", "http://nexus.test")
    monkeypatch.setattr(settings, "media_intent_mode", "off")

    prompts: list[str] = []
    persisted: list[dict[str, Any]] = []

    class _DbContext:
        def __enter__(self):
            return db_session

        def __exit__(self, *_args):
            return False

    class FakePiClient:
        effective_model = "test-model"

        def __init__(self, **_kwargs):
            pass

        async def chat_async(self, message, **_kwargs):
            prompts.append(message)
            if runtime_calls is not None:
                runtime_calls.append(_kwargs)
            return (
                PiRunResult(
                    reply="重新生成的回答",
                    stats={},
                    state={},
                    events=[],
                    token_usage={
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    },
                ),
                object(),
            )

    async def no_generated_context(**_kwargs):
        return [], []

    monkeypatch.setattr(chat_api, "SessionLocal", lambda: _DbContext())
    monkeypatch.setattr(chat_api, "transaction", lambda: _DbContext())
    monkeypatch.setattr(chat_api, "LocalPiAgentClient", FakePiClient)
    monkeypatch.setattr(chat_api, "_start_session_title_task", lambda **_k: None)
    monkeypatch.setattr(chat_api, "chat_capability_tools", lambda *_a: [])
    monkeypatch.setattr(
        chat_api, "_claim_chat_session_boundary_decision", lambda **_k: False
    )
    monkeypatch.setattr(
        chat_api, "_direct_task_execution_context", lambda *_a, **_k: ""
    )
    monkeypatch.setattr(
        chat_api, "_chat_session_endpoint_context_window", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        chat_api, "_pending_generated_image_context", no_generated_context
    )
    monkeypatch.setattr(
        chat_api.chat_repo, "get_owned_session", lambda *_a, **_k: messages
    )
    def persist(**kwargs):
        persisted.append(kwargs)
        return chat_api._persist_server_chat_messages(**kwargs) if persist_real else 1

    monkeypatch.setattr(chat_api, "_persist_server_chat_messages_with_retry", persist)
    return chat_api, SimpleNamespace, prompts, persisted


async def _drain(response) -> list[dict[str, Any]]:
    return [
        json.loads(chunk.decode()[6:])
        for chunk in [item async for item in response.body_iterator]
        if chunk.decode().startswith("data: ")
    ]


@pytest.mark.anyio
async def test_stream_chat_regenerates_last_answer_with_new_message_ids(
    db_session, owner, monkeypatch
):
    """The regenerate marker must reach persistence for the replaced answer."""
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _turn(db_session, session, index=1, at=start)
    reloaded = _reload(db_session, session)
    chat_api, SimpleNamespace, prompts, persisted = _regenerate_harness(
        db_session, monkeypatch, reloaded
    )

    response = await chat_api.stream_chat_message(
        chat_api.ChatMessageRequest(
            message="问题 1",
            local_session_id=session.id,
            client_message_id="user-1",
            client_assistant_message_id="regen-1",
            regenerate_from_message_id="answer-1",
        ),
        SimpleNamespace(id=owner, auth_version=1),
    )
    events = await _drain(response)

    assert events[-1]["type"] == "done"
    regen_calls = [
        item for item in persisted if item.get("regenerate_from_message_id")
    ]
    assert regen_calls, persisted
    assert regen_calls[-1]["regenerate_from_message_id"] == "answer-1"
    assert regen_calls[-1]["regenerate_activate"] is True
    assert prompts


@pytest.mark.anyio
async def test_stream_chat_rejects_regenerating_an_older_answer(
    db_session, owner, monkeypatch
):
    """Rewriting an older turn would invalidate later messages; branch instead."""
    from fastapi import HTTPException

    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _turn(db_session, session, index=1, at=start)
    _turn(db_session, session, index=2, at=start + timedelta(seconds=10))
    reloaded = _reload(db_session, session)
    chat_api, SimpleNamespace, _prompts, _persisted = _regenerate_harness(
        db_session, monkeypatch, reloaded
    )

    with pytest.raises(HTTPException) as error:
        await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message="问题 1",
                local_session_id=session.id,
                client_message_id="user-1",
                client_assistant_message_id="regen-1",
                regenerate_from_message_id="answer-1",
            ),
            SimpleNamespace(id=owner, auth_version=1),
        )
    assert error.value.status_code == 409


@pytest.mark.anyio
@pytest.mark.parametrize("prior_turn", [False, True])
async def test_regeneration_rebuilds_runtime_before_original_question(
    db_session, owner, monkeypatch, prior_turn
):
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    if prior_turn:
        _, earlier = _turn(db_session, session, index=0, at=start)
        earlier.usage = json.dumps({"status": "completed"})
    _turn(db_session, session, index=1, at=start + timedelta(seconds=1))
    runtime_calls = []
    chat_api, SimpleNamespace, prompts, _ = _regenerate_harness(
        db_session, monkeypatch, _reload(db_session, session), runtime_calls
    )
    request = chat_api.ChatMessageRequest(
        message="问题 1",
        local_session_id=session.id,
        client_message_id="user-1",
        client_assistant_message_id="regen-1",
        regenerate_from_message_id="answer-1",
    )
    for attempt in range(2):
        events = await _drain(await chat_api.stream_chat_message(
            request.model_copy(update={"client_assistant_message_id": f"regen-{attempt}"}),
            SimpleNamespace(id=owner, auth_version=1),
        ))
        assert events[-1]["type"] == "done"

    # 两次显式新尝试都从原问题之前恢复；同一次尝试的重传不允许再次执行。
    assert runtime_calls[0].get("history_context_key")
    assert runtime_calls[0]["history_context_key"] != runtime_calls[1]["history_context_key"]
    for prompt, call in zip(prompts, runtime_calls):
        context = str(call.get("resume_context") or "")
        assert "回答 1" not in context + prompt
        assert "问题 1" not in context
        assert prompt.count("问题 1") == 1
        if prior_turn:
            assert "回答 0" in context


@pytest.mark.anyio
async def test_switching_variants_changes_runtime_history_key_and_active_context(
    db_session, owner, monkeypatch
):
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _, original = _turn(db_session, session, index=1, at=start)
    original.usage = json.dumps({"status": "completed"})
    alternative = _message(
        db_session, session, message_id="regen-1", role="assistant",
        content="另一版本", at=start + timedelta(seconds=1),
        usage=json.dumps({"status": "completed"}),
    )
    chat_repo.register_message_variant(
        db_session, session, source_message_id=original.id, new_message_id=alternative.id
    )
    session = _reload(db_session, session)
    runtime_calls = []
    chat_api, SimpleNamespace, _, _ = _regenerate_harness(
        db_session, monkeypatch, session, runtime_calls
    )
    for active_id in ("answer-1", "regen-1", "answer-1"):
        chat_repo.activate_message_variant(db_session, session, message_id=active_id)
        await _drain(await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message="继续", local_session_id=session.id,
                client_message_id="next-user", client_assistant_message_id="next-answer",
            ),
            SimpleNamespace(id=owner, auth_version=1),
        ))

    keys = [call.get("history_context_key") for call in runtime_calls]
    assert keys[0] and keys[0] != keys[1] and keys[0] == keys[2]
    assert "回答 1" in runtime_calls[0]["resume_context"]
    assert "另一版本" not in runtime_calls[0]["resume_context"]
    assert "另一版本" in runtime_calls[1]["resume_context"]
    assert "回答 1" not in runtime_calls[1]["resume_context"]


def test_cursor_page_resolves_inactive_deep_link_to_active_variant(
    db_session, owner
):
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _turn(db_session, session, index=1, at=start)
    _message(
        db_session,
        session,
        message_id="regen-1",
        role="assistant",
        content="回答 1（重生成）",
        at=start + timedelta(seconds=5),
    )
    chat_repo.register_message_variant(
        db_session,
        session,
        source_message_id="answer-1",
        new_message_id="regen-1",
    )
    _turn(db_session, session, index=2, at=start + timedelta(seconds=10))

    page = chat_repo.get_session_message_page(
        db_session,
        session_id=session.id,
        owner_user_id=owner,
        limit=3,
        around_message_id="answer-1",
    )
    assert page is not None
    detail = chat_repo.session_detail_dict(
        _reload(db_session, session),
        message_page=page,
    )

    assert "answer-1" not in [item["id"] for item in detail["messages"]]
    active = next(item for item in detail["messages"] if item["id"] == "regen-1")
    assert active["variant"] == {
        "group_id": "answer-1",
        "index": 2,
        "total": 2,
        "active": True,
        "ids": ["answer-1", "regen-1"],
    }


@pytest.mark.anyio
@pytest.mark.parametrize("fails", [False, True])
async def test_model_change_regeneration_is_one_attempt_with_one_user_and_replay(
    db_session, owner, monkeypatch, fails,
):
    from fastapi import HTTPException

    from app.core.config import settings
    from app.models.chat import ChatDispatchDecision
    from app.models.user_memory import UserMemoryTurn
    from app.repositories import chat_dispatch, user_memory
    from app.services.pi_agent_client import PiAgentError

    session = _session(db_session, owner)
    session.preferred_model = "old-model"
    user_message, original = _turn(
        db_session, session, index=1, at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    original.content = ""
    original.usage = json.dumps({"status": "failed", "error_code": "model_unavailable"})
    original.parts_json = json.dumps([{
        "type": "error", "error_kind": "model", "error_id": "old-error",
        "error_code": "model_unavailable", "progress_preserved": False,
    }])
    old_decision = chat_dispatch.create_dispatch_decision(
        db_session, owner_user_id=owner, chat_session_id=session.id,
        client_message_id=user_message.id, effective_route="chat",
        request_digest=chat_dispatch.request_digest("问题 1", [], model="old-model", mode="auto"),
    )
    db_session.flush()
    user_message.dispatch_id = old_decision.id
    original.dispatch_id = old_decision.id
    db_session.flush()
    chat_api, Namespace, prompts, _ = _regenerate_harness(
        db_session, monkeypatch, _reload(db_session, session), persist_real=True,
    )
    monkeypatch.setattr(settings, "media_intent_mode", "auto")
    monkeypatch.setattr(settings, "personal_memory_enabled", True)
    original_memory = chat_api._register_memory_turn(
        db_session, user_id=owner, local_session_id=session.id,
        message_id=user_message.id, runtime_session_id=session.id, allow_new=True,
    )
    user_memory.close_turn(db_session, owner_user_id=owner, turn_id=original_memory["turn_id"])

    if fails:
        class UnavailableClient:
            effective_model = "new-model"

            def __init__(self, **_kwargs):
                pass

            async def chat_async(self, message, **_kwargs):
                prompts.append(message)
                raise PiAgentError("404 model_not_found private-provider-detail", code="model_not_found")

        monkeypatch.setattr(chat_api, "LocalPiAgentClient", UnavailableClient)

    request = chat_api.ChatMessageRequest(
        message="问题 1", local_session_id=session.id,
        client_message_id=user_message.id, client_assistant_message_id="recovery-answer",
        regenerate_from_message_id=original.id, model="new-model",
    )
    principal = Namespace(id=owner, auth_version=1)
    first = await _drain(await chat_api.stream_chat_message(request, principal))
    assert first[-1]["type"] == ("error" if fails else "done")
    assert first[-1]["server_persisted"] is True
    after = _reload(db_session, session)
    assert [item.id for item in after.messages if item.role == "user"] == [user_message.id]
    by_id = {item.id: item for item in after.messages}
    assert by_id[original.id].variant_active is fails
    assert by_id["recovery-answer"].variant_active is (not fails)
    assert by_id["recovery-answer"].variant_group_id == original.id
    assert after.preferred_model == "old-model"
    assert by_id[user_message.id].dispatch_id == old_decision.id
    assert by_id["recovery-answer"].dispatch_id != old_decision.id
    replay = await _drain(await chat_api.stream_chat_message(request, principal))
    assert replay[-1]["type"] == first[-1]["type"]
    assert len(prompts) == 1
    if fails:
        assert first[-1]["error_code"] == replay[-1]["error_code"] == "model_unavailable"
        assert first[-1]["progress_preserved"] is False
        assert replay[-1]["progress_preserved"] is False
        assert first[-1]["retryable"] is False
        stored = chat_repo.message_to_dict(by_id["recovery-answer"])
        error_part = next(part for part in stored["parts"] if part["type"] == "error")
        assert error_part["error_code"] == "model_unavailable"
        assert error_part["progress_preserved"] is False
        assert "private-provider-detail" not in json.dumps([first, replay, stored])
    with pytest.raises(HTTPException) as conflict:
        await chat_api.stream_chat_message(request.model_copy(update={"model": "third-model"}), principal)
    assert conflict.value.status_code == 409
    assert conflict.value.detail["code"] == "chat_request_conflict"
    assert conflict.value.detail["retryable"] is False
    assert len(prompts) == 1
    turns = list(db_session.scalars(select(UserMemoryTurn)))
    assert len(turns) == 2
    assert {turn.chat_message_id for turn in turns} == {user_message.id}
    assert {turn.status for turn in turns} == {"closed"}
    assert replay[-1]["memory_turn_id"] != original_memory["turn_id"]
    assert len(list(db_session.scalars(select(ChatDispatchDecision)))) == 2


@pytest.mark.anyio
async def test_regeneration_in_flight_and_message_id_collisions_never_execute(
    db_session, owner, monkeypatch,
):
    from fastapi import HTTPException

    session = _session(db_session, owner)
    _turn(db_session, session, index=1, at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    chat_api, Namespace, prompts, _ = _regenerate_harness(
        db_session, monkeypatch, _reload(db_session, session),
    )
    request = chat_api.ChatMessageRequest(
        message="问题 1", local_session_id=session.id, client_message_id="user-1",
        client_assistant_message_id="regen-1", regenerate_from_message_id="answer-1",
        model="new-model",
    )
    principal = Namespace(id=owner, auth_version=1)
    chat_api._claim_regeneration_attempt(body=request, user_id=owner, model="new-model")
    other = _session(db_session, owner)
    _message(db_session, other, message_id="foreign-answer", role="assistant",
             content="另一个会话", at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    for update in ({}, {"client_assistant_message_id": "user-1"}, {"client_assistant_message_id": "foreign-answer"}):
        with pytest.raises(HTTPException) as conflict:
            await chat_api.stream_chat_message(request.model_copy(update=update), principal)
        assert conflict.value.status_code == 409
        assert conflict.value.detail["code"] == "chat_request_conflict"
        assert conflict.value.detail["retryable"] is False
    assert prompts == []


@pytest.mark.anyio
@pytest.mark.parametrize("source_id", ["answer-1", "failed-variant"])
async def test_retry_zero_progress_latest_group_from_active_or_inactive_source(
    db_session, owner, monkeypatch, source_id,
):
    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _, original = _turn(db_session, session, index=1, at=start)
    original.content = ""
    original.usage = json.dumps({"status": "failed"})
    _message(
        db_session, session, message_id="failed-variant", role="assistant", content="",
        at=start + timedelta(seconds=1), usage=json.dumps({"status": "failed"}),
    )
    chat_repo.register_message_variant(
        db_session, session, source_message_id=original.id,
        new_message_id="failed-variant", activate=False,
    )
    chat_api, Namespace, prompts, _ = _regenerate_harness(
        db_session, monkeypatch, _reload(db_session, session), persist_real=True,
    )
    events = await _drain(await chat_api.stream_chat_message(
        chat_api.ChatMessageRequest(
            message="问题 1", local_session_id=session.id, client_message_id="user-1",
            client_assistant_message_id="recovered", regenerate_from_message_id=source_id,
            model="working-model",
        ), Namespace(id=owner, auth_version=1),
    ))
    assert events[-1]["type"] == "done"
    assert len(prompts) == 1
    messages = {item.id: item for item in _reload(db_session, session).messages}
    assert messages["recovered"].variant_group_id == "answer-1"
    assert messages["recovered"].variant_index == 2
    assert messages["recovered"].variant_active is True
    assert messages["answer-1"].variant_active is False
    assert messages["failed-variant"].variant_active is False


@pytest.mark.anyio
@pytest.mark.parametrize("blocker", ["partial", "unknown-tool", "older-round", "older-inactive"])
@pytest.mark.parametrize("source_id", ["answer-1", "failed-variant"])
async def test_regeneration_source_cannot_bypass_group_progress_or_latest_round(
    db_session, owner, monkeypatch, blocker, source_id,
):
    from fastapi import HTTPException

    session = _session(db_session, owner)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _, original = _turn(db_session, session, index=1, at=start)
    original.content = ""
    original.usage = json.dumps({"status": "failed"})
    failed = _message(
        db_session, session, message_id="failed-variant", role="assistant", content="",
        at=start + timedelta(seconds=1), usage=json.dumps({"status": "failed"}),
    )
    chat_repo.register_message_variant(
        db_session, session, source_message_id=original.id,
        new_message_id=failed.id, activate=False,
    )
    if blocker == "partial":
        failed.parts_json = json.dumps([{"type": "content", "text": "已保存的部分结果"}])
    elif blocker == "unknown-tool":
        failed.parts_json = json.dumps([{
            "type": "tool", "tool_call_id": "write-1", "tool_name": "write", "status": "error",
        }])
    elif blocker == "older-round":
        _turn(db_session, session, index=2, at=start + timedelta(seconds=2))
    else:
        # inactive 来源不能落后于后续尝试；active 来源仅在后续有进展时阻止重做。
        later = _message(
            db_session, session, message_id="later-variant", role="assistant", content="",
            at=start + timedelta(seconds=2), usage=json.dumps({"status": "failed"}),
        )
        later.parts_json = json.dumps([{"type": "content", "text": "后续已执行"}])
        chat_repo.register_message_variant(
            db_session, session, source_message_id=original.id,
            new_message_id=later.id, activate=False,
        )
    db_session.flush()
    chat_api, Namespace, prompts, _ = _regenerate_harness(
        db_session, monkeypatch, _reload(db_session, session),
    )
    with pytest.raises(HTTPException) as conflict:
        await chat_api.stream_chat_message(
            chat_api.ChatMessageRequest(
                message="问题 1", local_session_id=session.id, client_message_id="user-1",
                client_assistant_message_id="blocked", regenerate_from_message_id=source_id,
            ), Namespace(id=owner, auth_version=1),
        )
    assert conflict.value.status_code == 409
    assert conflict.value.detail["code"] == "chat_request_conflict"
    assert prompts == []
