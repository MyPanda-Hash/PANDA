from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import settings
from app.models.chat import ChatMessage, ChatSession
from app.models.chat_search import (
    ChatHistoryEmbedding,
    ChatHistoryIndexJob,
    ChatHistoryUsageEvent,
)
from app.models.model_endpoints import ModelEndpoint, ModelEndpointModel
from app.models.documents import Document, DocumentVersion
from app.models.auth import User
from app.repositories.personal_search import (
    get_chat_history_context,
    list_owned_uploads,
    search_chat_history,
)
from app.repositories import chat as chat_repository
from app.repositories import personal_search
from app.repositories.usage import summarize_usage_by_user
from app.services.agent_capability import (
    CapabilityGrantError,
    create_capability_token,
    decode_capability_token,
    execute_capability,
)
from app.services.pi_runtime.config import PI_RUNTIME_AGENT_ID
from app.services.chat_history_indexer import ChatHistoryIndexDispatcher


@pytest.fixture
def personal_search_db(tmp_path: Path):
    original = settings.db_path, settings.database_url, settings.embedding_dimensions
    path = tmp_path / "personal-search.sqlite3"
    database.reset_database(path)
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    settings.embedding_dimensions = 2
    database.initialize_database()
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()
        database.reset_database(path)
        settings.db_path, settings.database_url, settings.embedding_dimensions = original


def _seed_personal_data(db: Session) -> tuple[str, str, str]:
    stamp = datetime.now(timezone.utc)
    owner = User(
        id="user-owner",
        username="owner",
        email="owner@example.com",
        display_name="Owner",
        password_hash="unused",
        created_at=stamp,
    )
    other = User(
        id="user-other",
        username="other",
        email="other@example.com",
        display_name="Other",
        password_hash="unused",
        created_at=stamp,
    )
    db.add_all([owner, other])
    owner_session = ChatSession(
        id="chs-owner",
        owner_user_id=owner.id,
        title="Owner session",
        created_at=stamp,
        updated_at=stamp,
    )
    other_session = ChatSession(
        id="chs-other",
        owner_user_id=other.id,
        title="Other session",
        created_at=stamp,
        updated_at=stamp,
    )
    db.add_all([owner_session, other_session])
    db.add_all(
        [
            ChatMessage(
                id="msg-owner-match",
                session_id=owner_session.id,
                owner_user_id=owner.id,
                role="user",
                content="关于合同归档的方案",
                created_at=stamp,
            ),
            ChatMessage(
                id="msg-owner-other",
                session_id=owner_session.id,
                owner_user_id=owner.id,
                role="assistant",
                content="这是无关的回复",
                created_at=stamp,
            ),
            ChatMessage(
                id="msg-other-match",
                session_id=other_session.id,
                owner_user_id=other.id,
                role="user",
                content="关于合同归档的私密方案",
                created_at=stamp,
            ),
        ]
    )
    owner_document = Document(
        id="doc-owner",
        name="合同归档说明",
        status="active",
        created_by_user_id=owner.id,
        created_at=stamp,
        updated_at=stamp,
    )
    other_document = Document(
        id="doc-other",
        name="合同归档私密资料",
        status="active",
        created_by_user_id=other.id,
        created_at=stamp,
        updated_at=stamp,
    )
    db.add_all([owner_document, other_document])
    db.add_all(
        [
            DocumentVersion(
                id="ver-owner",
                document_id=owner_document.id,
                version_number=1,
                processing_stage="ready",
                queryable=True,
                is_current_published=True,
                created_at=stamp,
                updated_at=stamp,
            ),
            DocumentVersion(
                id="ver-other",
                document_id=other_document.id,
                version_number=1,
                processing_stage="ready",
                queryable=True,
                is_current_published=True,
                created_at=stamp,
                updated_at=stamp,
            ),
        ]
    )
    db.commit()
    return owner.id, owner_session.id, "msg-owner-match"


def test_personal_search_is_owner_scoped_and_returns_citations(personal_search_db: Session):
    owner_id, session_id, message_id = _seed_personal_data(personal_search_db)

    results = search_chat_history(
        personal_search_db,
        owner_user_id=owner_id,
        query="合同归档",
        limit=10,
    )

    assert [item.message_id for item in results] == [message_id]
    assert results[0].session_id == session_id
    assert results[0].citation == f"chat://{session_id}/{message_id}"


def test_personal_search_context_and_uploads_are_bounded_and_scoped(
    personal_search_db: Session,
):
    owner_id, session_id, message_id = _seed_personal_data(personal_search_db)

    context = get_chat_history_context(
        personal_search_db,
        owner_user_id=owner_id,
        session_id=session_id,
        message_id=message_id,
        before=1,
        after=1,
        max_chars=4,
    )
    uploads = list_owned_uploads(
        personal_search_db,
        owner_user_id=owner_id,
        query="合同",
        limit=10,
    )

    assert [item.message_id for item in context] == [message_id, "msg-owner-other"]
    assert all(len(item.content) <= 4 for item in context)
    assert [item.document_id for item in uploads] == ["doc-owner"]


def test_personal_uploads_hide_expired_temporary_files(personal_search_db: Session):
    owner_id, _session_id, _message_id = _seed_personal_data(personal_search_db)
    stamp = datetime.now(timezone.utc)
    personal_search_db.add(
        Document(
            id="doc-expired",
            name="过期临时资料",
            status="active",
            is_temporary=True,
            expires_at=stamp.replace(year=stamp.year - 1),
            created_by_user_id=owner_id,
            created_at=stamp,
            updated_at=stamp,
        )
    )
    personal_search_db.commit()

    uploads = list_owned_uploads(
        personal_search_db, owner_user_id=owner_id, limit=10
    )

    assert "doc-expired" not in {item.document_id for item in uploads}


def test_personal_capability_requires_explicit_own_scope(monkeypatch):
    monkeypatch.setattr(settings, "agent_capability_secret", "personal-search-secret")
    monkeypatch.setattr(settings, "environment", "test")

    with pytest.raises(CapabilityGrantError):
        create_capability_token(
            user_id="user-owner",
            agent_id=PI_RUNTIME_AGENT_ID,
            allowed_tools=["search_my_chat_history"],
        )

    token = create_capability_token(
        user_id="user-owner",
        agent_id=PI_RUNTIME_AGENT_ID,
        personal_data_scope="own",
        allowed_tools=[
            "search_my_chat_history",
            "get_chat_history_context",
            "list_my_uploads",
        ],
    )
    claims = decode_capability_token(token)
    assert claims["personal_data_scope"] == "own"


def test_personal_capability_executes_only_for_the_grant_owner(
    personal_search_db: Session, monkeypatch
):
    monkeypatch.setattr(settings, "agent_capability_secret", "personal-search-secret")
    monkeypatch.setattr(settings, "environment", "test")
    owner_id, _session_id, _message_id = _seed_personal_data(personal_search_db)
    user = personal_search_db.get(User, owner_id)
    assert user is not None
    token = create_capability_token(
        user_id=owner_id,
        agent_id=PI_RUNTIME_AGENT_ID,
        personal_data_scope="own",
        allowed_tools=["search_my_chat_history"],
    )

    result = execute_capability(
        personal_search_db,
        user=user,
        claims=decode_capability_token(token),
        tool_name="search_my_chat_history",
        arguments={"query": "合同归档"},
        expected_agent_id=PI_RUNTIME_AGENT_ID,
    )

    assert result["items"][0]["message_id"] == "msg-owner-match"


def test_chat_persistence_enqueues_idempotent_history_index_jobs(
    personal_search_db: Session,
):
    owner = User(
        id="queue-owner",
        username="queue-owner",
        email="queue-owner@example.com",
        display_name="Queue owner",
        password_hash="unused",
        created_at=datetime.now(timezone.utc),
    )
    personal_search_db.add(owner)
    personal_search_db.flush()
    session = chat_repository.create_session(
        personal_search_db, owner_user_id=owner.id, title="Queue session"
    )
    payload = {
        "id": "msg-queue",
        "role": "user",
        "content": "需要建立索引",
    }

    chat_repository.append_messages(
        personal_search_db,
        session,
        [payload, {"id": "msg-assistant", "role": "assistant", "content": "回复"}],
    )
    chat_repository.append_missing_messages(
        personal_search_db, session, [payload], preserve_existing=True
    )
    jobs = list(personal_search_db.query(ChatHistoryIndexJob))

    assert len(jobs) == 1
    assert jobs[0].owner_user_id == owner.id
    assert jobs[0].status == "pending"


def test_history_indexer_persists_embedding_and_supports_semantic_search(
    personal_search_db: Session,
):
    owner = User(
        id="vector-owner",
        username="vector-owner",
        email="vector-owner@example.com",
        display_name="Vector owner",
        password_hash="unused",
        created_at=datetime.now(timezone.utc),
    )
    personal_search_db.add(owner)
    personal_search_db.flush()
    session = chat_repository.create_session(
        personal_search_db, owner_user_id=owner.id, title="Vector session"
    )
    chat_repository.append_messages(
        personal_search_db,
        session,
        [{"id": "msg-vector", "role": "user", "content": "合同归档方案"}],
    )
    personal_search_db.commit()

    class Embed:
        _model = "embed-test"
        last_usage = {"input_tokens": 4, "total_tokens": 4, "cost": "0.004"}

        def __call__(self, texts):
            return [[1.0, 0.0] for _ in texts]

    dispatcher = ChatHistoryIndexDispatcher(embedding_fn=Embed())
    assert dispatcher.run_once() is True
    personal_search_db.expire_all()
    embedding = personal_search_db.query(ChatHistoryEmbedding).one()
    job = personal_search_db.query(ChatHistoryIndexJob).one()
    assert embedding.status == "ready"
    assert embedding.embedding_json == "[1.0,0.0]"
    assert job.status == "succeeded"
    event = personal_search_db.query(ChatHistoryUsageEvent).one()
    assert event.operation_kind == "embedding"
    assert event.model == "embed-test"
    assert event.total_tokens == 4
    summary = summarize_usage_by_user(personal_search_db)
    item = next(item for item in summary.items if item.user_id == owner.id)
    assert item.by_model["embed-test"] == 4

    results = personal_search.search_chat_history_semantic(
        personal_search_db,
        owner_user_id=owner.id,
        query="历史合同",
        embedding_fn=lambda texts: [[1.0, 0.0] for _ in texts],
        limit=5,
    )
    assert [item.message_id for item in results] == ["msg-vector"]


@pytest.mark.parametrize("allowed_level", ["internal", "confidential"])
def test_history_indexer_does_not_export_unclassified_text_below_restricted(
    personal_search_db: Session,
    monkeypatch,
    allowed_level: str,
):
    stamp = datetime.now(timezone.utc)
    owner = User(
        id="guarded-index-owner",
        username="guarded-index-owner",
        email="guarded-index-owner@example.com",
        display_name="Guarded index owner",
        password_hash="unused",
        created_at=stamp,
    )
    personal_search_db.add(owner)
    personal_search_db.flush()
    session = chat_repository.create_session(
        personal_search_db,
        owner_user_id=owner.id,
        title="Guarded index",
    )
    chat_repository.append_messages(
        personal_search_db,
        session,
        [{"id": "msg-guarded-index", "role": "user", "content": "未分类客户报价"}],
    )
    endpoint = ModelEndpoint(
        id="mep-below-restricted-history-index",
        provider="openai-compatible",
        model="history-index-model",
        capability="embedding",
        endpoint_url="https://embedding.example.test/v1",
        secret_ref="env://HISTORY_INDEX_TEST_KEY",
        allowed_data_level=allowed_level,
        status="active",
        created_at=stamp,
        updated_at=stamp,
    )
    endpoint.models = [
        ModelEndpointModel(
            id="mepm-below-restricted-history-index",
            endpoint_id=endpoint.id,
            model="history-index-model",
            status="active",
            is_default=True,
            sort_order=0,
            max_context=8192,
            created_at=stamp,
            updated_at=stamp,
        )
    ]
    personal_search_db.add(endpoint)
    personal_search_db.commit()

    monkeypatch.setattr(
        settings,
        "embedding_api_url",
        "https://embedding.example.test/v1",
    )
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_api_model", "history-index-model")
    outbound: list[str] = []

    def embedding_post(url, **kwargs):
        outbound.append(url)
        raise AssertionError("未分类历史原文不应发往 restricted 以下端点")

    monkeypatch.setattr("app.services.pgvector_store.httpx.post", embedding_post)

    assert ChatHistoryIndexDispatcher(max_attempts=1).run_once() is True
    personal_search_db.expire_all()
    assert outbound == []
    assert personal_search_db.query(ChatHistoryEmbedding).count() == 0
    job = personal_search_db.query(ChatHistoryIndexJob).one()
    assert job.status == "failed"


def test_history_search_records_privacy_safe_usage_event(personal_search_db: Session):
    owner_id, _session_id, _message_id = _seed_personal_data(personal_search_db)

    personal_search.record_search_usage(
        personal_search_db,
        owner_user_id=owner_id,
        query_type="history",
        query="合同归档",
        candidate_count=2,
        result_count=1,
        vector_used=False,
        latency_ms=3,
    )
    personal_search_db.commit()
    event = personal_search_db.query(ChatHistoryUsageEvent).one()
    assert event.owner_user_id == owner_id
    assert event.query_hash != "合同归档"
    assert event.result_count == 1


def test_history_embedding_usage_keeps_price_snapshot_metadata(
    personal_search_db: Session,
):
    owner_id, _session_id, message_id = _seed_personal_data(personal_search_db)

    personal_search.record_embedding_usage(
        personal_search_db,
        owner_user_id=owner_id,
        message_id=message_id,
        provider="embedding_api",
        model="embed-test",
        usage={"price_version_id": "price-v1", "currency": "CNY"},
        retry_number=1,
        status="succeeded",
        latency_ms=4,
    )
    personal_search_db.commit()

    event = personal_search_db.query(ChatHistoryUsageEvent).one()
    assert event.price_version_id == "price-v1"
    assert event.currency == "CNY"


def test_personal_capability_fuses_ready_semantic_hits(
    personal_search_db: Session, monkeypatch
):
    monkeypatch.setattr(settings, "agent_capability_secret", "personal-search-secret")
    monkeypatch.setattr(settings, "environment", "test")
    owner_id, _session_id, message_id = _seed_personal_data(personal_search_db)
    stamp = datetime.now(timezone.utc)
    personal_search_db.add(
        ChatHistoryEmbedding(
            id="embedding-fusion",
            message_id=message_id,
            owner_user_id=owner_id,
            provider="test",
            model="test-embedding",
            content_hash="hash",
            embedding_json="[1.0,0.0]",
            status="ready",
            created_at=stamp,
            updated_at=stamp,
        )
    )
    personal_search_db.commit()
    monkeypatch.setattr(
        "app.services.agent_capability._chat_history_embedding_function",
        lambda: (lambda texts: [[1.0, 0.0] for _ in texts]),
    )
    user = personal_search_db.get(User, owner_id)
    assert user is not None
    token = create_capability_token(
        user_id=owner_id,
        agent_id=PI_RUNTIME_AGENT_ID,
        personal_data_scope="own",
        allowed_tools=["search_my_chat_history"],
    )

    result = execute_capability(
        personal_search_db,
        user=user,
        claims=decode_capability_token(token),
        tool_name="search_my_chat_history",
        arguments={"query": "历史合同"},
        expected_agent_id=PI_RUNTIME_AGENT_ID,
    )

    assert result["vector_used"] is True
    assert result["retrieval_method"] == "hybrid"
    assert result["items"][0]["message_id"] == message_id
    personal_search_db.commit()
    summary = summarize_usage_by_user(personal_search_db)
    item = next(item for item in summary.items if item.user_id == owner_id)
    assert "nexus" not in item.by_model


def test_personal_capability_keeps_lexical_results_when_query_embedding_fails(
    personal_search_db: Session, monkeypatch
):
    monkeypatch.setattr(settings, "agent_capability_secret", "personal-search-secret")
    monkeypatch.setattr(settings, "environment", "test")
    owner_id, _session_id, message_id = _seed_personal_data(personal_search_db)

    class FailingQueryEmbed:
        _model = "query-embed"

        def __call__(self, texts):
            raise RuntimeError("embedding unavailable")

    monkeypatch.setattr(
        "app.services.agent_capability._chat_history_embedding_function",
        lambda: FailingQueryEmbed(),
    )
    user = personal_search_db.get(User, owner_id)
    assert user is not None
    token = create_capability_token(
        user_id=owner_id,
        agent_id=PI_RUNTIME_AGENT_ID,
        personal_data_scope="own",
        allowed_tools=["search_my_chat_history"],
    )

    result = execute_capability(
        personal_search_db,
        user=user,
        claims=decode_capability_token(token),
        tool_name="search_my_chat_history",
        arguments={"query": "合同归档"},
        expected_agent_id=PI_RUNTIME_AGENT_ID,
    )

    assert result["retrieval_method"] == "lexical"
    assert result["vector_used"] is False
    assert result["items"][0]["message_id"] == message_id
    event = personal_search_db.query(ChatHistoryUsageEvent).filter_by(
        operation_kind="embedding", query_type="query"
    ).one()
    assert event.status == "failed"


def test_history_indexer_retries_without_blocking_lexical_search(
    personal_search_db: Session,
):
    owner_id, _session_id, message_id = _seed_personal_data(personal_search_db)
    personal_search.enqueue_chat_history_index_jobs(
        personal_search_db,
        personal_search_db.query(ChatMessage)
        .filter(ChatMessage.id == message_id)
        .all(),
    )
    personal_search_db.commit()

    class FailingEmbed:
        _model = "embed-fail"

        def __call__(self, texts):
            raise RuntimeError("provider unavailable")

    dispatcher = ChatHistoryIndexDispatcher(
        embedding_fn=FailingEmbed(), max_attempts=2
    )
    assert dispatcher.run_once() is True
    personal_search_db.expire_all()
    job = personal_search_db.query(ChatHistoryIndexJob).one()
    assert job.status == "pending"
    assert job.attempts == 1

    job.next_attempt_at = None
    personal_search_db.commit()
    assert dispatcher.run_once() is True
    personal_search_db.expire_all()
    job = personal_search_db.query(ChatHistoryIndexJob).one()
    assert job.status == "failed"
    assert job.attempts == 2
    assert personal_search.search_chat_history(
        personal_search_db,
        owner_user_id=owner_id,
        query="合同归档",
        limit=10,
    )[0].message_id == message_id


def test_chat_session_delete_cascades_history_jobs_vectors_and_embedding_events(
    personal_search_db: Session,
):
    owner_id, session_id, message_id = _seed_personal_data(personal_search_db)
    personal_search.enqueue_chat_history_index_jobs(
        personal_search_db,
        personal_search_db.query(ChatMessage)
        .filter(ChatMessage.id == message_id)
        .all(),
    )
    personal_search_db.commit()
    dispatcher = ChatHistoryIndexDispatcher(
        embedding_fn=lambda texts: [[1.0, 0.0] for _ in texts]
    )
    assert dispatcher.run_once() is True
    personal_search_db.expire_all()
    personal_search_db.delete(personal_search_db.get(ChatSession, session_id))
    personal_search_db.commit()

    assert personal_search_db.query(ChatHistoryIndexJob).count() == 0
    assert personal_search_db.query(ChatHistoryEmbedding).count() == 0
    assert (
        personal_search_db.query(ChatHistoryUsageEvent)
        .filter(ChatHistoryUsageEvent.message_id == message_id)
        .count()
        == 0
    )
    assert owner_id


def test_topic_recall_uses_only_own_active_prior_conversation(personal_search_db: Session):
    owner_id, prior_session_id, _ = _seed_personal_data(personal_search_db)
    stamp = datetime.now(timezone.utc)
    current = ChatSession(
        id="chs-current",
        owner_user_id=owner_id,
        title="新问题",
        created_at=stamp,
        updated_at=stamp,
    )
    personal_search_db.add(current)
    personal_search_db.add_all([
        ChatMessage(
            id="msg-jev-question", session_id=prior_session_id,
            owner_user_id=owner_id, role="user",
            content="JEV 指日本脑炎病毒，它是什么？", created_at=stamp,
        ),
        ChatMessage(
            id="msg-jev-answer", session_id=prior_session_id,
            owner_user_id=owner_id, role="assistant",
            content="JEV 是日本脑炎病毒。", created_at=stamp,
        ),
        ChatMessage(
            id="msg-jev-inactive", session_id=prior_session_id,
            owner_user_id=owner_id, role="assistant",
            content="已淘汰的错误解释", variant_active=False, created_at=stamp,
        ),
        ChatMessage(
            id="msg-jev-inactive-user", session_id=prior_session_id,
            owner_user_id=owner_id, role="user",
            content="JEV 是已撤回的私人结论", variant_active=False, created_at=stamp,
        ),
        ChatMessage(
            id="msg-other-jev", session_id="chs-other",
            owner_user_id="user-other", role="user",
            content="JEV 是另一名员工的私密病历", created_at=stamp,
        ),
        ChatMessage(
            id="msg-current-jev", session_id=current.id,
            owner_user_id=owner_id, role="user",
            content="JEV 是什么？", created_at=stamp,
        ),
    ])
    personal_search_db.commit()

    from app.services.chat_history_recall import recall_prior_topic

    result = recall_prior_topic(
        personal_search_db,
        owner_user_id=owner_id,
        session_id=current.id,
        message_id="msg-current-jev",
    )
    assert len(result) == 1
    assert result[0]["source_user"] == "JEV 指日本脑炎病毒，它是什么？"
    assert result[0]["source_assistant"] == "JEV 是日本脑炎病毒。"
    assert result[0]["citation"] == f"chat://{prior_session_id}/msg-jev-question"
    assert "已淘汰" not in str(result)


def test_topic_recall_uses_existing_vector_index_for_rephrased_question(
    personal_search_db: Session, monkeypatch
):
    owner_id, old_session_id, _ = _seed_personal_data(personal_search_db)
    stamp = datetime.now(timezone.utc)
    personal_search_db.add(ChatSession(
        id="chs-semantic-current", owner_user_id=owner_id, title="复盘",
        created_at=stamp, updated_at=stamp,
    ))
    personal_search_db.add_all([
        ChatMessage(
            id="msg-milestones", session_id=old_session_id,
            owner_user_id=owner_id, role="user",
            content="请回顾产品里程碑", created_at=stamp,
        ),
        ChatMessage(
            id="msg-semantic-current", session_id="chs-semantic-current",
            owner_user_id=owner_id, role="user",
            content="项目复盘用什么格式？", created_at=stamp,
        ),
    ])
    personal_search_db.flush()
    from app.services.chat_history_vector_store import ChatHistoryVectorStore

    ChatHistoryVectorStore().upsert(
        personal_search_db, message_id="msg-milestones", owner_user_id=owner_id,
        embedding=[1.0, 0.0], content_hash="seed", provider="test", model="test",
    )
    personal_search_db.commit()
    monkeypatch.setattr(settings, "qa_vector_enabled", True)
    class QueryEmbedding:
        _model = "test-query-embedding"
        last_usage = {"prompt_tokens": 6, "total_tokens": 6}

        def __call__(self, texts):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(
        "app.services.pgvector_store._build_embedding_function",
        lambda **kwargs: QueryEmbedding(),
    )
    from app.services.chat_history_recall import recall_prior_topic

    items = recall_prior_topic(
        personal_search_db, owner_user_id=owner_id,
        session_id="chs-semantic-current", message_id="msg-semantic-current",
    )
    assert items[0]["message_id"] == "msg-milestones"
    assert items[0]["match_kind"] == "semantic"
    event = personal_search_db.query(ChatHistoryUsageEvent).filter(
        ChatHistoryUsageEvent.query_type == "query",
    ).one()
    assert event.total_tokens == 6
    assert event.model == "test-query-embedding"

    def unavailable(**kwargs):
        raise TimeoutError("controlled timeout")

    monkeypatch.setattr("app.services.pgvector_store._build_embedding_function", unavailable)
    assert recall_prior_topic(
        personal_search_db, owner_user_id=owner_id,
        session_id="chs-semantic-current", message_id="msg-semantic-current",
    ) == []


@pytest.mark.parametrize(
    ("allowed_level", "configured_url", "expected_semantic"),
    [
        ("restricted", "https://embedding.example.test/v1", True),
        ("internal", "https://embedding.example.test/v1", False),
        ("confidential", "https://embedding.example.test/v1", False),
        ("restricted", "https://override.example.test/v1", False),
    ],
)
def test_topic_recall_governs_actual_embedding_url_model_and_data_level(
    personal_search_db: Session,
    monkeypatch,
    allowed_level: str,
    configured_url: str,
    expected_semantic: bool,
):
    owner_id, old_session_id, _ = _seed_personal_data(personal_search_db)
    stamp = datetime.now(timezone.utc)
    personal_search_db.add_all(
        [
            ChatSession(
                id="chs-governed-current",
                owner_user_id=owner_id,
                title="受限复盘",
                created_at=stamp,
                updated_at=stamp,
            ),
            ChatMessage(
                id="msg-governed-source",
                session_id=old_session_id,
                owner_user_id=owner_id,
                role="user",
                content="请回顾产品里程碑",
                created_at=stamp,
            ),
            ChatMessage(
                id="msg-governed-current",
                session_id="chs-governed-current",
                owner_user_id=owner_id,
                role="user",
                content="受限项目复盘用什么格式？",
                created_at=stamp,
            ),
        ]
    )
    endpoint = ModelEndpoint(
        id="mep-governed-embedding",
        provider="openai-compatible",
        model="governed-embedding",
        capability="embedding",
        endpoint_url="https://embedding.example.test/v1",
        secret_ref="env://GOVERNED_EMBEDDING_KEY",
        allowed_data_level=allowed_level,
        status="active",
        created_at=stamp,
        updated_at=stamp,
    )
    endpoint.models = [
        ModelEndpointModel(
            id="mepm-governed-embedding",
            endpoint_id=endpoint.id,
            model="governed-embedding",
            status="active",
            is_default=True,
            sort_order=0,
            max_context=8192,
            created_at=stamp,
            updated_at=stamp,
        )
    ]
    personal_search_db.add(endpoint)
    personal_search_db.flush()
    from app.services.chat_history_vector_store import ChatHistoryVectorStore

    ChatHistoryVectorStore().upsert(
        personal_search_db,
        message_id="msg-governed-source",
        owner_user_id=owner_id,
        embedding=[1.0, 0.0],
        content_hash="governed",
        provider="test",
        model="governed-embedding",
    )
    personal_search_db.commit()

    monkeypatch.setattr(settings, "qa_vector_enabled", True)
    monkeypatch.setattr(settings, "embedding_api_url", configured_url)
    monkeypatch.setattr(settings, "embedding_api_key", "test-key")
    monkeypatch.setattr(settings, "embedding_api_model", "governed-embedding")
    requests: list[dict] = []

    def embedding_post(url, *, json, headers, timeout):
        requests.append({"url": url, "json": json})
        return httpx.Response(
            200,
            json={
                "data": [{"embedding": [1.0, 0.0]}],
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.services.pgvector_store.httpx.post", embedding_post)
    from app.services.chat_history_recall import recall_prior_topic

    items = recall_prior_topic(
        personal_search_db,
        owner_user_id=owner_id,
        session_id="chs-governed-current",
        message_id="msg-governed-current",
    )

    assert bool(items) is expected_semantic
    if expected_semantic:
        assert items[0]["message_id"] == "msg-governed-source"
        assert requests == [
            {
                "url": "https://embedding.example.test/v1/embeddings",
                "json": {
                    "model": "governed-embedding",
                    "input": ["受限项目复盘用什么格式？"],
                    "dimensions": 2,
                },
            }
        ]
    else:
        assert requests == []
