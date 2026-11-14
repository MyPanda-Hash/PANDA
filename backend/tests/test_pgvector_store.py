"""Tests for PostgreSQL pgvector and SQLite sqlite-vec knowledge chunk storage."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from app.services.pgvector_store import (
    PgVectorStore,
    _OpenAICompatibleEmbeddingFunction,
    _embedding_literal,
)


class _EmbeddingResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def test_embedding_function_accepts_openai_response(monkeypatch):
    from app.services import pgvector_store

    monkeypatch.setattr(
        pgvector_store.httpx,
        "post",
        lambda *args, **kwargs: _EmbeddingResponse(
            {
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            }
        ),
    )

    embed = _OpenAICompatibleEmbeddingFunction(
        "http://embedding.test", "secret", "test-model"
    )

    assert embed(["文本"]) == [[0.1, 0.2]]
    assert embed.last_usage == {"prompt_tokens": 2, "total_tokens": 2}


def test_embedding_function_omits_authorization_when_keyless(monkeypatch):
    from app.services import pgvector_store
    monkeypatch.setattr(pgvector_store.settings, "linkmind_base_url", "http://linkmind.example:8080")
    monkeypatch.setattr(pgvector_store.settings, "linkmind_enabled", True)
    monkeypatch.setattr(
        pgvector_store,
        "resolve_embedding_config",
        lambda **_kwargs: ("http://linkmind.example:8080/v1", "", "bge-m3", 1024),
    )

    calls: list[dict] = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        return _EmbeddingResponse(
            {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        )

    monkeypatch.setattr(pgvector_store.httpx, "post", post)
    embed = pgvector_store._build_embedding_function()

    assert embed(["文本"]) == [[0.1, 0.2]]
    assert calls[0]["headers"] == {"Content-Type": "application/json"}


def test_embedding_function_accepts_llama_cpp_response(monkeypatch):
    from app.services import pgvector_store

    monkeypatch.setattr(
        pgvector_store.httpx,
        "post",
        lambda *args, **kwargs: _EmbeddingResponse(
            [
                {"index": 0, "embedding": [[0.1, 0.2]]},
                {"index": 1, "embedding": [[0.3, 0.4]]},
            ]
        ),
    )

    embed = _OpenAICompatibleEmbeddingFunction(
        "http://embedding.test", "secret", "bge-m3"
    )

    assert embed(["第一段", "第二段"]) == [[0.1, 0.2], [0.3, 0.4]]
    assert embed.last_usage == {}


def test_pgvector_chunk_add_uses_normalized_llama_response(monkeypatch):
    from app.services import pgvector_store

    class FakeDb:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))

    db = FakeDb()

    @contextmanager
    def fake_transaction():
        yield db

    monkeypatch.setattr(
        pgvector_store.settings,
        "database_url",
        "postgresql+psycopg://user:pass@db/app",
    )
    monkeypatch.setattr(pgvector_store, "transaction", fake_transaction)
    monkeypatch.setattr(
        pgvector_store.httpx,
        "post",
        lambda *args, **kwargs: _EmbeddingResponse(
            [{"index": 0, "embedding": [[0.1, 0.2]]}]
        ),
    )
    monkeypatch.setattr(
        pgvector_store,
        "_build_embedding_function",
        lambda: _OpenAICompatibleEmbeddingFunction(
            "http://embedding.test", "secret", "bge-m3"
        ),
    )
    monkeypatch.setattr(pgvector_store.settings, "embedding_dimensions", 2)

    vector_id = PgVectorStore().chunk_add(
        "docnexus_space",
        file_id="42",
        document="pgvector 嵌入分块",
        vector_id="vector-pg",
    )

    assert vector_id == "vector-pg"
    _, params = db.calls[0]
    assert params["embedding"] == "[0.1,0.2]"


def test_embedding_literal_rejects_non_finite_values():
    with pytest.raises(ValueError, match="有限数值"):
        _embedding_literal([0.1, float("nan")])


def test_chunk_add_persists_embedding_and_metadata(monkeypatch):
    from app.services import pgvector_store

    class FakeDb:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))

    db = FakeDb()

    @contextmanager
    def fake_transaction():
        yield db

    monkeypatch.setattr(
        pgvector_store.settings,
        "database_url",
        "postgresql+psycopg://user:pass@db/app",
    )
    monkeypatch.setattr(pgvector_store, "transaction", fake_transaction)
    monkeypatch.setattr(
        pgvector_store,
        "_build_embedding_function",
        lambda: lambda texts: [[0.1, 0.2] for _ in texts],
    )
    monkeypatch.setattr(pgvector_store.settings, "embedding_dimensions", 2)

    vector_id = PgVectorStore().chunk_add(
        "docnexus_space",
        file_id="42",
        document="知识分块",
        vector_id="vector-1",
        metadata={"chunk_index": 0, "source": "test"},
    )

    assert vector_id == "vector-1"
    statement, params = db.calls[0]
    assert "INSERT INTO document_embeddings" in statement
    assert params["category"] == "docnexus_space"
    assert params["file_id"] == "42"
    assert params["embedding"] == "[0.1,0.2]"
    assert params["metadata"] == {"chunk_index": 0, "source": "test"}


def test_chunk_add_accepts_private_1024_dimensional_embedding(monkeypatch):
    from app.services import pgvector_store

    class FakeDb:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))

    db = FakeDb()

    @contextmanager
    def fake_transaction():
        yield db

    monkeypatch.setattr(
        pgvector_store.settings,
        "database_url",
        "postgresql+psycopg://user:pass@db/app",
    )
    monkeypatch.setattr(pgvector_store, "transaction", fake_transaction)
    monkeypatch.setattr(
        pgvector_store,
        "_build_embedding_function",
        lambda: lambda texts: [[float(index) for index in range(1024)] for _ in texts],
    )
    monkeypatch.setattr(pgvector_store.settings, "embedding_dimensions", 1024)

    vector_id = PgVectorStore().chunk_add(
        "docnexus_space",
        file_id="42",
        document="私有化 embedding",
        vector_id="private-1024",
    )

    assert vector_id == "private-1024"
    _, params = db.calls[0]
    assert params["embedding"].count(",") == 1023


def test_delete_by_file_skips_unsupported_databases(monkeypatch):
    from app.services import pgvector_store

    monkeypatch.setattr(
        pgvector_store.settings, "database_url", "mysql+pymysql://user:pass@db/app"
    )
    monkeypatch.setattr(
        pgvector_store,
        "_build_embedding_function",
        lambda: pytest.fail("删除向量不应初始化 Embedding API 客户端"),
    )

    assert PgVectorStore().delete_by_file("docnexus_space", "42", strict=True) == 0


def test_delete_by_file_returns_deleted_count(monkeypatch):
    from app.services import pgvector_store

    class FakeResult:
        rowcount = 3

    class FakeDb:
        def execute(self, statement, params):
            assert "DELETE FROM document_embeddings" in str(statement)
            assert params == {"category": "docnexus_space", "file_id": "42"}
            return FakeResult()

    @contextmanager
    def fake_transaction():
        yield FakeDb()

    monkeypatch.setattr(
        pgvector_store.settings,
        "database_url",
        "postgresql+psycopg://user:pass@db/app",
    )
    monkeypatch.setattr(pgvector_store, "transaction", fake_transaction)
    monkeypatch.setattr(
        pgvector_store,
        "_build_embedding_function",
        lambda: pytest.fail("删除向量不应初始化 Embedding API 客户端"),
    )

    assert PgVectorStore().delete_by_file("docnexus_space", "42") == 3


def test_delete_by_file_can_remove_only_failed_generation(monkeypatch):
    from app.services import pgvector_store

    class FakeResult:
        rowcount = 2

    class FakeDb:
        def execute(self, statement, params):
            assert "id IN" in str(statement)
            assert params == {"category": "docnexus_space", "file_id": "42"}
            return FakeResult()

    @contextmanager
    def fake_transaction():
        yield FakeDb()

    monkeypatch.setattr(
        pgvector_store.settings,
        "database_url",
        "postgresql+psycopg://user:pass@db/app",
    )
    monkeypatch.setattr(pgvector_store, "transaction", fake_transaction)
    assert (
        PgVectorStore().delete_by_file(
            "docnexus_space",
            "42",
            only_ids=["failed-1", "failed-2"],
            strict=True,
        )
        == 2
    )


def test_delete_by_file_can_keep_successful_generation(monkeypatch):
    from app.services import pgvector_store

    class FakeResult:
        rowcount = 3

    class FakeDb:
        def execute(self, statement, params):
            assert "id NOT IN" in str(statement)
            assert params == {"category": "docnexus_space", "file_id": "42"}
            return FakeResult()

    @contextmanager
    def fake_transaction():
        yield FakeDb()

    monkeypatch.setattr(
        pgvector_store.settings,
        "database_url",
        "postgresql+psycopg://user:pass@db/app",
    )
    monkeypatch.setattr(pgvector_store, "transaction", fake_transaction)
    assert (
        PgVectorStore().delete_by_file(
            "docnexus_space",
            "42",
            exclude_ids=["new-1", "new-2"],
            strict=True,
        )
        == 3
    )


def test_sqlite_vec_roundtrip_search_and_delete(tmp_path, monkeypatch):
    from app.core import database
    from app.core.config import settings
    from app.services import pgvector_store

    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        pytest.skip("sqlite-vec is not installed")

    original_path, original_url = settings.db_path, settings.database_url
    path = tmp_path / "vectors.sqlite3"
    settings.db_path = path
    settings.database_url = f"sqlite:///{path}"
    monkeypatch.setattr(pgvector_store.settings, "db_path", path)
    monkeypatch.setattr(pgvector_store.settings, "database_url", f"sqlite:///{path}")
    monkeypatch.setattr(pgvector_store.settings, "embedding_dimensions", 2)
    monkeypatch.setattr(
        pgvector_store,
        "_build_embedding_function",
        lambda **_kwargs: lambda texts: [[1.0, 0.0] for _ in texts],
    )
    try:
        database.reset_database(path)
        database.initialize_database()
        store = PgVectorStore()
        vector_id = store.chunk_add(
            "docnexus_space",
            file_id="42",
            document="知识分块",
            vector_id="vector-1",
            metadata={"chunk_index": 0},
        )
        hits = store.search(
            query="知识",
            scopes=[("docnexus_space", ["42"])],
            top_k=5,
        )
        assert vector_id == "vector-1"
        assert len(hits) == 1
        assert hits[0]["id"] == "vector-1"
        assert hits[0]["file_id"] == "42"
        assert hits[0]["distance"] == pytest.approx(0.0)
        assert hits[0]["score"] == pytest.approx(1.0)
        assert store.delete_by_file("docnexus_space", "42", strict=True) == 1
        assert store.search(query="知识", scopes=[("docnexus_space", ["42"])]) == []
    finally:
        settings.db_path, settings.database_url = original_path, original_url
        database.reset_database(path)


def test_embedding_function_uses_explicit_timeout(monkeypatch):
    from app.services import pgvector_store

    calls: list[dict] = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        return _EmbeddingResponse(
            {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        )

    monkeypatch.setattr(pgvector_store.httpx, "post", post)
    embed = _OpenAICompatibleEmbeddingFunction(
        "http://embedding.test",
        "secret",
        "test-model",
        timeout_seconds=7.5,
    )

    assert embed(["查询"]) == [[0.1, 0.2]]
    assert calls[0]["timeout"] == 7.5


def test_query_embedding_concurrency_is_process_bounded(monkeypatch):
    from app.services import pgvector_store

    release = threading.Event()
    saturated = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0
    configured_timeouts: list[float] = []

    def embedding(texts):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                saturated.set()
        try:
            release.wait(timeout=2)
            return [[0.1, 0.2] for _text in texts]
        finally:
            with lock:
                active -= 1

    def build_embedding_function(*, timeout_seconds=120.0):
        configured_timeouts.append(timeout_seconds)
        return embedding

    class EmptyRows:
        def mappings(self):
            return self

        def all(self):
            return []

    class FakeDb:
        def execute(self, _statement, _params):
            return EmptyRows()

    @contextmanager
    def fake_transaction():
        yield FakeDb()

    monkeypatch.setattr(pgvector_store.settings, "embedding_dimensions", 2)
    monkeypatch.setattr(
        pgvector_store.settings, "embedding_query_max_concurrency", 2
    )
    monkeypatch.setattr(
        pgvector_store.settings, "embedding_query_timeout_seconds", 3.5
    )
    monkeypatch.setattr(pgvector_store, "_database_kind", lambda: "sqlite")
    monkeypatch.setattr(
        pgvector_store, "_build_embedding_function", build_embedding_function
    )
    monkeypatch.setattr(pgvector_store, "transaction", fake_transaction)
    monkeypatch.setattr(
        pgvector_store, "_EMBEDDING_QUERY_SEMAPHORE_STATE", None
    )

    stores = [PgVectorStore() for _index in range(5)]
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(
                store.search,
                query=f"查询 {index}",
                scopes=[("docnexus_space", ["1"])],
            )
            for index, store in enumerate(stores)
        ]
        assert saturated.wait(timeout=1)
        with lock:
            assert active == 2
            assert peak == 2
        release.set()
        assert [future.result(timeout=2) for future in futures] == [
            [],
            [],
            [],
            [],
            [],
        ]

    assert peak == 2
    assert configured_timeouts == [3.5] * 5
