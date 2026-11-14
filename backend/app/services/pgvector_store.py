"""Knowledge-chunk vector storage for PostgreSQL pgvector and SQLite sqlite-vec."""

from __future__ import annotations

from app.services.data_guard import anonymize_text, is_cloud_endpoint

import json
import logging
import math
import struct
import threading
import uuid
from collections.abc import Callable
from typing import Any

import httpx  # noqa: E402

from sqlalchemy import (
    JSON,
    String,
    Text,
    and_,
    bindparam,
    cast,
    column,
    delete,
    func,
    insert,
    or_,
    select,
    table,
)
from sqlalchemy.orm import Session
from sqlalchemy.types import UserDefinedType

from app.core.config import settings
from app.core.database import transaction
from app.services.model_routing import resolve_embedding_endpoint

logger = logging.getLogger(__name__)

EmbeddingFunction = Callable[[list[str]], list[list[float]]]


class _VectorType(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_: Any) -> str:
        if self.dimensions is None:
            return "vector"
        return f"vector({self.dimensions})"


_VECTOR = _VectorType()
_DOCUMENT_EMBEDDINGS = table(
    "document_embeddings",
    column("id", String),
    column("category", String),
    column("file_id", String),
    column("document", Text),
    column("embedding"),
    column("metadata", JSON),
)


class _OpenAICompatibleEmbeddingFunction:
    """Call an OpenAI-compatible /embeddings API (DashScope, vLLM, etc.)."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        dimensions: int | None = None,
        *,
        timeout_seconds: float = 120.0,
    ):
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._timeout_seconds = timeout_seconds
        self.last_usage: dict[str, Any] = {}

    def __call__(self, texts: list[str]) -> list[list[float]]:
        # 向量化会把正文发给 embedding 端点：云端必须匿名化。
        # 这里用「无序号」版本——同一段文本每次结果一致，检索才不会漂移。
        masked_texts = texts
        if is_cloud_endpoint(self._api_url):
            masked_texts = [anonymize_text(item) for item in texts]
        body: dict[str, Any] = {"model": self._model, "input": masked_texts}
        if self._dimensions:
            body["dimensions"] = self._dimensions

        logger.info(
            "embedding.call.start",
            extra={
                "model": self._model,
                "dimensions": self._dimensions,
                "text_count": len(texts),
                "url": f"{self._api_url}/embeddings",
                "span": "embedding.call",
            },
        )
        try:
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            response = httpx.post(
                f"{self._api_url}/embeddings",
                json=body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error(
                "embedding.call.failed",
                extra={
                    "model": self._model,
                    "text_count": len(texts),
                    "url": f"{self._api_url}/embeddings",
                    "error": str(exc),
                    "span": "embedding.call",
                },
            )
            raise
        response_body = response.json()
        if isinstance(response_body, dict):
            raw_embeddings = response_body.get("data", [])
            raw_usage = response_body.get("usage")
        elif isinstance(response_body, list):
            # llama.cpp's OpenAI-compatible endpoint returns the data array
            # directly and wraps each vector in one extra list.
            raw_embeddings = response_body
            raw_usage = None
        else:
            raise RuntimeError("embedding 响应格式无效")
        self.last_usage = raw_usage if isinstance(raw_usage, dict) else {}
        if not isinstance(raw_embeddings, list):
            raise RuntimeError("embedding 响应 data 字段格式无效")
        embeddings: list[list[float]] = []
        for item in raw_embeddings:
            if not isinstance(item, dict):
                raise RuntimeError("embedding 响应 data 项格式无效")
            raw_embedding = item.get("embedding")
            if (
                isinstance(raw_embedding, list)
                and len(raw_embedding) == 1
                and isinstance(raw_embedding[0], list)
            ):
                raw_embedding = raw_embedding[0]
            if not isinstance(raw_embedding, list):
                raise RuntimeError("embedding 响应向量格式无效")
            try:
                embeddings.append([float(value) for value in raw_embedding])
            except (TypeError, ValueError) as exc:
                raise RuntimeError("embedding 响应包含非数值向量") from exc
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"embedding 返回数量不匹配: 期望 {len(texts)}，实际 {len(embeddings)}"
            )
        logger.info(
            "embedding.call.end",
            extra={
                "model": self._model,
                "text_count": len(texts),
                "embedding_count": len(embeddings),
                "span": "embedding.call",
            },
        )
        return embeddings


def _configured_embedding_dimensions() -> int:
    dimensions = settings.embedding_dimensions
    if not isinstance(dimensions, int) or dimensions <= 0:
        raise RuntimeError("embedding_dimensions 必须是正整数")
    return dimensions


def _embedding_literal(embedding: list[float]) -> str:
    """Build a pgvector literal while rejecting invalid values before persistence."""
    if not embedding or not all(math.isfinite(value) for value in embedding):
        raise ValueError("embedding 必须是非空的有限数值向量")
    return "[" + ",".join(str(value) for value in embedding) + "]"


def resolve_embedding_config(
    *,
    db: Session | None = None,
    data_level: str | None = None,
) -> tuple[str, str, str, int | None]:
    """Resolve embedding transport; governed calls bind level, URL, and model."""
    api_url = (settings.embedding_api_url or "").strip().rstrip("/")
    api_key = (settings.embedding_api_key or "").strip()
    model = (settings.embedding_api_model or "text-embedding-v2").strip()
    dimensions = _configured_embedding_dimensions() or None
    if data_level is not None and db is None:
        raise RuntimeError("受治理的 Embedding 调用必须绑定当前数据库事务")

    if api_url and api_key:
        if data_level is None:
            return api_url, api_key, model, dimensions
        endpoint = resolve_embedding_endpoint(
            db,
            data_level=data_level,
            requested_model=model,
            endpoint_url=api_url,
        )
        if endpoint is None:
            raise RuntimeError("Embedding 环境配置未通过当前数据等级治理")
        return api_url, api_key, endpoint.model, dimensions

    from app.core.database import transaction as database_transaction

    try:
        if db is not None:
            endpoint = resolve_embedding_endpoint(db, data_level=data_level)
        else:
            with database_transaction() as route_db:
                endpoint = resolve_embedding_endpoint(route_db)
    except Exception:
        if data_level is not None:
            raise
        endpoint = None
    if endpoint is not None:
        from app.services import linkmind
        from app.services.pi_local import _base_url, _secret

        if endpoint.secret_ref == linkmind.LINKMIND_ENDPOINT_SECRET_REF:
            return (
                _base_url(endpoint.endpoint_url),
                "",
                endpoint.model,
                dimensions,
            )
        resolved_key = _secret(endpoint.secret_ref)
        if resolved_key:
            return (
                _base_url(endpoint.endpoint_url),
                resolved_key,
                endpoint.model,
                dimensions,
            )
    if data_level is not None:
        raise RuntimeError("没有可证明满足当前数据等级的 Embedding 目标")


    return api_url, api_key, model, dimensions


def _is_configured_linkmind_model_url(api_url: str) -> bool:
    base = (settings.linkmind_base_url or "").strip().rstrip("/")
    if not base:
        return False
    expected = base if base.endswith("/v1") else f"{base}/v1"
    return api_url.rstrip("/") == expected

def _is_configured_default_embedding_url(api_url: str) -> bool:
    from app.services.default_endpoints import default_embedding_url

    return api_url.rstrip("/") == default_embedding_url().rstrip("/")


def _build_embedding_function(
    *,
    timeout_seconds: float = 120.0,
    db: Session | None = None,
    data_level: str | None = None,
) -> EmbeddingFunction:
    api_url, api_key, model, dimensions = resolve_embedding_config(
        db=db,
        data_level=data_level,
    )
    if not api_key and not (
        settings.linkmind_enabled
        and (
            _is_configured_linkmind_model_url(api_url)
            or _is_configured_default_embedding_url(api_url)
        )
    ):
        raise RuntimeError(
            "未配置 Embedding API Key。"
            "请设置 DOC_NEXUS_EMBEDDING_API_KEY 或 DASHSCOPE_API_KEY，"
            "或在管理页面配置 capability=embedding 的模型端点。"
        )
    if not api_url:
        raise RuntimeError(
            "未配置 Embedding API URL。"
            "请设置 DOC_NEXUS_EMBEDDING_API_URL，"
            "或在管理页面配置 capability=embedding 的模型端点。"
        )
    logger.info(
        "Knowledge pipeline embedding: url=%s model=%s dims=%s",
        api_url,
        model,
        dimensions,
    )
    return _OpenAICompatibleEmbeddingFunction(
        api_url,
        api_key,
        model,
        dimensions,
        timeout_seconds=timeout_seconds,
    )


def build_embedding_function() -> EmbeddingFunction:
    """Build the configured embedding client for document and personal search."""
    return _build_embedding_function()


def _embedding_literal(embedding: list[float]) -> str:
    """Build a pgvector literal while rejecting invalid values before persistence."""
    if not embedding or not all(math.isfinite(value) for value in embedding):
        raise ValueError("embedding 必须是非空的有限数值向量")
    return "[" + ",".join(str(value) for value in embedding) + "]"


def _embedding_blob(embedding: list[float]) -> bytes:
    """Serialize a float32 little-endian blob for sqlite-vec."""
    _embedding_literal(embedding)
    return struct.pack(f"<{len(embedding)}f", *embedding)


def _file_id(value: int | str) -> str:
    return str(value)


def _database_kind() -> str:
    url = settings.database_url
    if url.startswith("postgresql"):
        return "postgresql"
    if url.startswith("sqlite"):
        return "sqlite"
    return "other"


def _supports_vector_store() -> bool:
    return _database_kind() in {"postgresql", "sqlite"}


def _coerce_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


_EMBEDDING_QUERY_SEMAPHORE_LOCK = threading.Lock()
_EMBEDDING_QUERY_SEMAPHORE_STATE: (
    tuple[int, threading.BoundedSemaphore] | None
) = None


def _embedding_query_semaphore() -> threading.BoundedSemaphore:
    """Return one process-wide gate matching the configured query concurrency."""
    global _EMBEDDING_QUERY_SEMAPHORE_STATE
    limit = settings.embedding_query_max_concurrency
    with _EMBEDDING_QUERY_SEMAPHORE_LOCK:
        current = _EMBEDDING_QUERY_SEMAPHORE_STATE
        if current is None or current[0] != limit:
            current = (limit, threading.BoundedSemaphore(limit))
            _EMBEDDING_QUERY_SEMAPHORE_STATE = current
        return current[1]


class PgVectorStore:
    """Store knowledge chunks in PostgreSQL pgvector or SQLite sqlite-vec."""

    def __init__(self) -> None:
        self._embedding_fn: EmbeddingFunction | None = None
        self._query_embedding_fn: EmbeddingFunction | None = None

    def chunk_add(
        self,
        category: str,
        *,
        file_id: int | str,
        document: str,
        vector_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Embed and persist a single document chunk. Returns the vector ID."""
        kind = _database_kind()
        if kind not in {"postgresql", "sqlite"}:
            raise RuntimeError("本地知识向量存储需要 PostgreSQL/pgvector 或 SQLite/sqlite-vec")
        if self._embedding_fn is None:
            self._embedding_fn = _build_embedding_function()
        embedding = self._embedding_fn([document])[0]
        expected_dimensions = _configured_embedding_dimensions()
        if len(embedding) != expected_dimensions:
            raise RuntimeError(
                "embedding 维度不匹配: "
                f"期望 {expected_dimensions}，实际 {len(embedding)}；"
                "请将 DOC_NEXUS_EMBEDDING_DIMENSIONS 设置为模型实际维度"
            )

        vector_id = vector_id or uuid.uuid4().hex
        safe_metadata = {
            key: value if isinstance(value, (str, int, float, bool)) else str(value)
            for key, value in (metadata or {}).items()
        }
        if kind == "postgresql":
            statement = insert(_DOCUMENT_EMBEDDINGS).values(
                id=bindparam("id"),
                category=bindparam("category"),
                file_id=bindparam("file_id"),
                document=bindparam("document"),
                embedding=cast(bindparam("embedding"), _VECTOR),
                metadata=bindparam("metadata"),
            )
            payload: Any = _embedding_literal(embedding)
        else:
            statement = insert(_DOCUMENT_EMBEDDINGS).values(
                id=bindparam("id"),
                category=bindparam("category"),
                file_id=bindparam("file_id"),
                document=bindparam("document"),
                embedding=bindparam("embedding"),
                metadata=bindparam("metadata"),
            )
            payload = _embedding_blob(embedding)
        with transaction() as db:
            db.execute(
                statement,
                {
                    "id": vector_id,
                    "category": category,
                    "file_id": _file_id(file_id),
                    "document": document,
                    "embedding": payload,
                    "metadata": safe_metadata,
                },
            )
        return vector_id

    def search(
        self,
        *,
        query: str,
        scopes: list[tuple[str, list[str]]],
        top_k: int = 30,
    ) -> list[dict[str, Any]]:
        """Return nearest stored chunks inside pre-authorized category/file scopes.

        The existing ingestion table is intentionally reused. Older rows remain
        readable and are simply selected by the same category/file partition;
        callers map file_id back to the current authorized DocumentVersion.
        """
        kind = _database_kind()
        if kind not in {"postgresql", "sqlite"} or not query.strip() or not scopes:
            return []
        semaphore = _embedding_query_semaphore()
        if not semaphore.acquire(
            timeout=settings.embedding_query_timeout_seconds
        ):
            raise TimeoutError("查询 embedding 并发等待超时")
        try:
            if self._query_embedding_fn is None:
                self._query_embedding_fn = _build_embedding_function(
                    timeout_seconds=settings.embedding_query_timeout_seconds
                )
            embedding = self._query_embedding_fn([query])[0]
        finally:
            semaphore.release()
        expected_dimensions = _configured_embedding_dimensions()
        if len(embedding) != expected_dimensions:
            raise RuntimeError(
                "查询 embedding 维度不匹配: "
                f"期望 {expected_dimensions}，实际 {len(embedding)}；"
                "请将 DOC_NEXUS_EMBEDDING_DIMENSIONS 设置为模型实际维度"
            )

        predicates = [
            and_(
                _DOCUMENT_EMBEDDINGS.c.category == category,
                _DOCUMENT_EMBEDDINGS.c.file_id.in_(
                    [_file_id(file_id) for file_id in file_ids]
                ),
            )
            for category, file_ids in scopes
            if category and file_ids
        ]
        if not predicates:
            return []

        if kind == "postgresql":
            # A dimension change keeps old vectors physically readable, but
            # prevents pgvector from comparing incompatible model outputs.
            predicates = [
                and_(
                    predicate,
                    func.vector_dims(_DOCUMENT_EMBEDDINGS.c.embedding)
                    == expected_dimensions,
                )
                for predicate in predicates
            ]
            vector_type = _VectorType(expected_dimensions)
            distance = cast(
                _DOCUMENT_EMBEDDINGS.c.embedding, vector_type
            ).op("<=>")(
                cast(bindparam("query_embedding"), vector_type)
            )
            query_payload: Any = _embedding_literal(embedding)
        else:
            distance = func.vec_distance_cosine(
                _DOCUMENT_EMBEDDINGS.c.embedding,
                bindparam("query_embedding"),
            )
            query_payload = _embedding_blob(embedding)

        statement = (
            select(
                _DOCUMENT_EMBEDDINGS.c.id,
                _DOCUMENT_EMBEDDINGS.c.category,
                _DOCUMENT_EMBEDDINGS.c.file_id,
                _DOCUMENT_EMBEDDINGS.c.document,
                _DOCUMENT_EMBEDDINGS.c.metadata,
                distance.label("distance"),
            )
            .where(or_(*predicates))
            .order_by(distance)
            .limit(min(max(top_k, 1), 100))
        )
        with transaction() as db:
            rows = (
                db.execute(
                    statement,
                    {"query_embedding": query_payload},
                )
                .mappings()
                .all()
            )

        results: list[dict[str, Any]] = []
        for row in rows:
            try:
                distance_value = float(row["distance"])
            except (TypeError, ValueError):
                continue
            results.append(
                {
                    "id": row["id"],
                    "category": row["category"],
                    "file_id": str(row["file_id"]),
                    "document": row["document"] or "",
                    "metadata": _coerce_metadata(row["metadata"]),
                    "distance": distance_value,
                    "score": max(0.0, 1.0 - distance_value),
                }
            )
        return results

    def delete_by_file(
        self,
        category: str,
        file_id: int | str,
        *,
        strict: bool = False,
        only_ids: list[str] | None = None,
        exclude_ids: list[str] | None = None,
    ) -> int:
        """Delete vectors for a file, optionally keeping a generation.

        ``only_ids`` is used to clean up a failed indexing attempt. Passing
        ``exclude_ids`` removes stale generations after a new generation has
        been written successfully. The two filters are mutually exclusive.
        """
        if only_ids is not None and exclude_ids is not None:
            raise ValueError("only_ids and exclude_ids cannot be combined")
        if only_ids is not None and not only_ids:
            return 0
        if not _supports_vector_store():
            return 0
        try:
            predicates = [
                _DOCUMENT_EMBEDDINGS.c.category == bindparam("category"),
                _DOCUMENT_EMBEDDINGS.c.file_id == bindparam("file_id"),
            ]
            if only_ids is not None:
                predicates.append(
                    _DOCUMENT_EMBEDDINGS.c.id.in_(
                        [str(vector_id) for vector_id in only_ids]
                    )
                )
            elif exclude_ids:
                predicates.append(
                    _DOCUMENT_EMBEDDINGS.c.id.not_in(
                        [str(vector_id) for vector_id in exclude_ids]
                    )
                )
            statement = delete(_DOCUMENT_EMBEDDINGS).where(and_(*predicates))
            with transaction() as db:
                result = db.execute(
                    statement,
                    {"category": category, "file_id": _file_id(file_id)},
                )
                return max(getattr(result, "rowcount", 0) or 0, 0)
        except Exception:
            if strict:
                raise
            return 0
