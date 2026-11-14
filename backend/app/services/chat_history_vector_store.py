"""Dedicated storage adapter for private chat-history embeddings.

Document evidence vectors intentionally remain in ``document_embeddings``. This
adapter never reads or writes that table and always applies the message owner
predicate before returning a candidate.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.security import now
from app.models.chat import ChatMessage, ChatSession
from app.models.chat_search import ChatHistoryEmbedding


def _vector(raw: list[float]) -> list[float]:
    values = [float(value) for value in raw]
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("embedding must be a non-empty finite vector")
    return values


def _stored_vector(raw: str | None) -> list[float] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return _vector(parsed) if isinstance(parsed, list) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _literal(values: list[float]) -> str:
    return "[" + ",".join(str(value) for value in _vector(values)) + "]"


class ChatHistoryVectorStore:
    """Portable SQLite fallback; PostgreSQL can migrate the payload to vector later."""

    def upsert(
        self,
        db: Session,
        *,
        message_id: str,
        owner_user_id: str,
        embedding: list[float],
        content_hash: str,
        provider: str | None,
        model: str | None,
        status: str = "ready",
    ) -> ChatHistoryEmbedding:
        values = _vector(embedding)
        row = db.scalar(
            select(ChatHistoryEmbedding).where(
                ChatHistoryEmbedding.message_id == message_id
            )
        )
        stamp = now()
        if row is None:
            row = ChatHistoryEmbedding(
                id=f"che_{uuid.uuid4().hex}",
                message_id=message_id,
                owner_user_id=owner_user_id,
                provider=(provider or "").strip() or None,
                model=(model or "").strip() or None,
                content_hash=content_hash,
                embedding_json=json.dumps(values, ensure_ascii=False, separators=(",", ":")),
                status=status,
                created_at=stamp,
                updated_at=stamp,
            )
            db.add(row)
        else:
            row.owner_user_id = owner_user_id
            row.provider = (provider or "").strip() or None
            row.model = (model or "").strip() or None
            row.content_hash = content_hash
            row.embedding_json = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
            row.status = status
            row.updated_at = stamp
        db.flush()
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            try:
                db.execute(
                    text(
                        "UPDATE chat_history_embeddings SET embedding = "
                        "CAST(:embedding AS vector) WHERE id = :id"
                    ),
                    {"embedding": _literal(values), "id": row.id},
                )
            except Exception:
                # The JSON representation remains a safe fallback for a rolling
                # upgrade where migration 0057 has not run yet.
                pass
        return row

    def delete(self, db: Session, *, message_id: str) -> None:
        row = db.scalar(
            select(ChatHistoryEmbedding).where(
                ChatHistoryEmbedding.message_id == message_id
            )
        )
        if row is not None:
            db.delete(row)

    def search(
        self,
        db: Session,
        *,
        owner_user_id: str,
        query_vector: list[float],
        session_id: str | None = None,
        exclude_session_id: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        needle = _vector(query_vector)
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            try:
                return self._search_postgres(
                    db,
                    owner_user_id=owner_user_id,
                    query_vector=needle,
                    session_id=session_id,
                    exclude_session_id=exclude_session_id,
                    created_after=created_after,
                    created_before=created_before,
                    limit=limit,
                )
            except Exception:
                # Keep search available while an older deployment is being
                # migrated; the portable JSON path is owner-scoped as well.
                pass
        statement = (
            select(ChatHistoryEmbedding, ChatMessage, ChatSession.title)
            .join(ChatMessage, ChatMessage.id == ChatHistoryEmbedding.message_id)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .where(
                ChatHistoryEmbedding.owner_user_id == owner_user_id,
                ChatHistoryEmbedding.status == "ready",
                ChatMessage.owner_user_id == owner_user_id,
                ChatMessage.role == "user",
                ChatMessage.variant_active.is_(True),
                ChatSession.owner_user_id == owner_user_id,
            )
        )
        if session_id:
            statement = statement.where(ChatMessage.session_id == session_id)
        if exclude_session_id:
            statement = statement.where(ChatMessage.session_id != exclude_session_id)
        if created_after:
            statement = statement.where(ChatMessage.created_at >= created_after)
        if created_before:
            statement = statement.where(ChatMessage.created_at <= created_before)
        hits: list[dict[str, Any]] = []
        for embedding, message, title in db.execute(statement).all():
            stored = _stored_vector(embedding.embedding_json)
            if stored is None:
                continue
            score = _cosine(needle, stored)
            if score < 0:
                continue
            hits.append(
                {
                    "message_id": message.id,
                    "session_id": message.session_id,
                    "session_title": title,
                    "role": message.role,
                    "content": message.content,
                    "created_at": message.created_at,
                    "citation": f"chat://{message.session_id}/{message.id}",
                    "score": score,
                }
            )
        hits.sort(
            key=lambda item: (
                -float(item["score"]),
                -item["created_at"].timestamp(),
                str(item["message_id"]),
            )
        )
        return hits[: max(1, min(limit, 50))]

    def _search_postgres(
        self,
        db: Session,
        *,
        owner_user_id: str,
        query_vector: list[float],
        session_id: str | None,
        exclude_session_id: str | None,
        created_after: datetime | None,
        created_before: datetime | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        predicates = [
            "e.owner_user_id = :owner_user_id",
            "e.status = 'ready'",
            "e.embedding IS NOT NULL",
            "m.owner_user_id = :owner_user_id",
            "m.role = 'user'",
            "m.variant_active = true",
            "s.owner_user_id = :owner_user_id",
            "vector_dims(e.embedding) = :dimensions",
        ]
        params: dict[str, Any] = {
            "owner_user_id": owner_user_id,
            "query_vector": _literal(query_vector),
            "dimensions": len(query_vector),
            "limit": max(1, min(limit, 50)),
        }
        if session_id:
            predicates.append("m.session_id = :session_id")
            params["session_id"] = session_id
        if exclude_session_id:
            predicates.append("m.session_id != :exclude_session_id")
            params["exclude_session_id"] = exclude_session_id
        if created_after:
            predicates.append("m.created_at >= :created_after")
            params["created_after"] = created_after
        if created_before:
            predicates.append("m.created_at <= :created_before")
            params["created_before"] = created_before
        statement = text(
            "SELECT m.id AS message_id, m.session_id, s.title AS session_title, "
            "m.role, m.content, m.created_at, "
            "('chat://' || m.session_id || '/' || m.id) AS citation, "
            "(1 - (e.embedding <=> CAST(:query_vector AS vector))) AS score "
            "FROM chat_history_embeddings e "
            "JOIN chat_messages m ON m.id = e.message_id "
            "JOIN chat_sessions s ON s.id = m.session_id "
            f"WHERE {' AND '.join(predicates)} "
            "ORDER BY e.embedding <=> CAST(:query_vector AS vector), "
            "m.created_at DESC, m.id DESC LIMIT :limit"
        )
        return [dict(row) for row in db.execute(statement, params).mappings().all()]


__all__ = ["ChatHistoryVectorStore"]
