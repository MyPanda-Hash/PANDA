from __future__ import annotations

import re
import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Callable

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.chat import ChatMessage, ChatSession
from app.repositories import chat as chat_repo
from app.models.chat_search import ChatHistoryIndexJob, ChatHistoryUsageEvent
from app.models.documents import Document


@dataclass(frozen=True)
class ChatHistorySearchResult:
    message_id: str
    session_id: str
    session_title: str
    role: str
    content: str
    created_at: datetime
    citation: str
    score: float


@dataclass(frozen=True)
class ChatHistoryContextItem:
    message_id: str
    session_id: str
    role: str
    content: str
    created_at: datetime
    citation: str


@dataclass(frozen=True)
class PersonalUploadResult:
    document_id: str
    name: str
    status: str
    space_id: str | None
    chat_session_id: str | None
    is_temporary: bool
    created_at: datetime
    updated_at: datetime
    citation: str


EmbeddingFunction = Callable[[list[str]], list[list[float]]]


def enqueue_chat_history_index_jobs(
    db: Session, messages: Iterable[ChatMessage]
) -> list[ChatHistoryIndexJob]:
    """Create one durable indexing job per newly persisted user/assistant message."""
    jobs: list[ChatHistoryIndexJob] = []
    stamp = now()
    for message in messages:
        if message.role != "user" or not message.content.strip():
            continue
        existing = db.scalar(
            select(ChatHistoryIndexJob).where(
                ChatHistoryIndexJob.message_id == message.id
            )
        )
        if existing is not None:
            continue
        job = ChatHistoryIndexJob(
            id=f"chj_{new_token(10)}",
            message_id=message.id,
            owner_user_id=message.owner_user_id,
            status="pending",
            attempts=0,
            next_attempt_at=stamp,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(job)
        jobs.append(job)
    if jobs:
        db.flush()
    return jobs


def _query_terms(query: str) -> list[str]:
    return [term for term in re.split(r"\s+", query.strip()) if term][:8]


def search_chat_history(
    db: Session,
    *,
    owner_user_id: str,
    query: str,
    session_id: str | None = None,
    exclude_session_id: str | None = None,
    limit: int = 10,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> list[ChatHistorySearchResult]:
    """Search only messages whose denormalized and session owner both match."""
    terms = _query_terms(query)
    if not terms:
        return []
    predicates = [ChatMessage.content.ilike(f"%{term}%") for term in terms]
    statement = (
        select(ChatMessage, ChatSession.title)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatMessage.owner_user_id == owner_user_id,
            ChatMessage.role == "user",
            ChatSession.owner_user_id == owner_user_id,
            ChatMessage.variant_active.is_(True),
            or_(*predicates),
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
    rows = db.execute(
        statement.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(
            50
        )
    ).all()
    ranked = [
        ChatHistorySearchResult(
            message_id=message.id,
            session_id=message.session_id,
            session_title=title,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            citation=f"chat://{message.session_id}/{message.id}",
            score=sum(term.casefold() in message.content.casefold() for term in terms)
            / len(terms),
        )
        for message, title in rows
    ]
    ranked.sort(key=lambda item: (-item.score, -item.created_at.timestamp(), item.message_id))
    return ranked[: max(1, min(limit, 50))]


def search_chat_history_semantic(
    db: Session,
    *,
    owner_user_id: str,
    query: str,
    embedding_fn: EmbeddingFunction,
    session_id: str | None = None,
    exclude_session_id: str | None = None,
    limit: int = 10,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> list[ChatHistorySearchResult]:
    """Rank ready private embeddings after applying the same owner filters."""
    query_vector = embedding_fn([query])[0]
    if not query_vector:
        return []
    from app.services.chat_history_vector_store import ChatHistoryVectorStore

    hits = ChatHistoryVectorStore().search(
        db,
        owner_user_id=owner_user_id,
        query_vector=query_vector,
        session_id=session_id,
        exclude_session_id=exclude_session_id,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
    )
    return [ChatHistorySearchResult(**hit) for hit in hits]


def fuse_chat_history_results(
    lexical: list[ChatHistorySearchResult],
    semantic: list[ChatHistorySearchResult],
    *,
    limit: int,
) -> list[ChatHistorySearchResult]:
    """Fuse lexical and semantic rankings using reciprocal rank fusion."""
    by_id = {item.message_id: item for item in lexical}
    by_id.update({item.message_id: item for item in semantic})
    scores: dict[str, float] = {}
    for rank, item in enumerate(lexical, start=1):
        scores[item.message_id] = scores.get(item.message_id, 0.0) + 1 / (60 + rank)
    for rank, item in enumerate(semantic, start=1):
        scores[item.message_id] = scores.get(item.message_id, 0.0) + 1 / (60 + rank)
    ranked = sorted(
        by_id.values(),
        key=lambda item: (-scores[item.message_id], -item.created_at.timestamp(), item.message_id),
    )
    return [
        ChatHistorySearchResult(
            message_id=item.message_id,
            session_id=item.session_id,
            session_title=item.session_title,
            role=item.role,
            content=item.content,
            created_at=item.created_at,
            citation=item.citation,
            score=scores[item.message_id],
        )
        for item in ranked[: max(1, min(limit, 50))]
    ]


def record_search_usage(
    db: Session,
    *,
    owner_user_id: str,
    query_type: str,
    query: str,
    candidate_count: int,
    result_count: int,
    vector_used: bool,
    latency_ms: int,
) -> ChatHistoryUsageEvent:
    stamp = now()
    event = ChatHistoryUsageEvent(
        id=f"chue_{new_token(10)}",
        owner_user_id=owner_user_id,
        message_id=None,
        operation_kind="search",
        query_type=query_type,
        query_hash=hashlib.sha256(query.strip().encode("utf-8")).hexdigest(),
        provider="nexus",
        model=None,
        input_tokens=0,
        input_uncached_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cost="0",
        candidate_count=max(0, candidate_count),
        result_count=max(0, result_count),
        vector_used=bool(vector_used),
        latency_ms=max(0, latency_ms),
        retry_number=0,
        status="succeeded",
        created_at=stamp,
    )
    db.add(event)
    db.flush()
    return event


def record_embedding_usage(
    db: Session,
    *,
    owner_user_id: str,
    message_id: str | None,
    provider: str | None,
    model: str | None,
    usage: dict[str, object] | None,
    retry_number: int,
    status: str,
    latency_ms: int,
    operation_kind: str = "embedding",
    query_type: str = "index",
    vector_used: bool = True,
) -> ChatHistoryUsageEvent:
    raw = usage or {}

    def number(*keys: str) -> int:
        for key in keys:
            value = raw.get(key)
            if value in (None, ""):
                continue
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return 0

    def text_value(key: str) -> str | None:
        value = raw.get(key)
        if value in (None, ""):
            return None
        return str(value).strip() or None

    event = ChatHistoryUsageEvent(
        id=f"chue_{new_token(10)}",
        owner_user_id=owner_user_id,
        message_id=message_id,
        operation_kind=operation_kind,
        query_type=query_type,
        query_hash="",
        provider=(str(provider).strip() or None),
        model=(str(model).strip() or None),
        price_version_id=text_value("price_version_id"),
        currency=text_value("currency"),
        input_tokens=number("input_tokens", "prompt_tokens"),
        input_uncached_tokens=number(
            "input_uncached_tokens", "input_tokens", "prompt_tokens"
        ),
        cache_read_tokens=number("cache_read_tokens"),
        cache_write_tokens=number("cache_write_tokens"),
        output_tokens=number("output_tokens", "completion_tokens"),
        total_tokens=number("total_tokens"),
        cost=str(raw.get("cost") or "0"),
        candidate_count=0,
        result_count=0,
        vector_used=vector_used,
        latency_ms=max(0, latency_ms),
        retry_number=max(0, retry_number),
        status=status,
        created_at=now(),
    )
    if event.total_tokens == 0:
        event.total_tokens = event.input_tokens + event.output_tokens
    db.add(event)
    db.flush()
    return event


def get_chat_history_context(
    db: Session,
    *,
    owner_user_id: str,
    session_id: str,
    message_id: str,
    before: int = 2,
    after: int = 2,
    max_chars: int = 4_000,
) -> list[ChatHistoryContextItem]:
    """Return a small owner-scoped chronological window around one message."""
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.owner_user_id == owner_user_id,
        )
    )
    if session is None:
        return []
    messages = chat_repo.active_messages(
        list(
            db.scalars(
                select(ChatMessage).where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.owner_user_id == owner_user_id,
                )
            ).all()
        )
    )
    index = next((position for position, item in enumerate(messages) if item.id == message_id), None)
    if index is None:
        return []
    start = max(0, index - max(0, min(before, 5)))
    stop = min(len(messages), index + max(0, min(after, 5)) + 1)
    return [
        ChatHistoryContextItem(
            message_id=message.id,
            session_id=message.session_id,
            role=message.role,
            content=message.content[: max(1, min(max_chars, 16_000))],
            created_at=message.created_at,
            citation=f"chat://{message.session_id}/{message.id}",
        )
        for message in messages[start:stop]
    ]


def list_owned_uploads(
    db: Session,
    *,
    owner_user_id: str,
    query: str | None = None,
    space_id: str | None = None,
    include_temporary: bool = True,
    visible_space_ids: set[str] | None = None,
    offset: int = 0,
    limit: int = 20,
) -> list[PersonalUploadResult]:
    """List active documents created by the caller and still in visible scope."""
    owned_session = exists(
        select(ChatSession.id).where(
            ChatSession.id == Document.chat_session_id,
            ChatSession.owner_user_id == owner_user_id,
        )
    )
    statement = select(Document).where(
        Document.created_by_user_id == owner_user_id,
        Document.status.notin_(("archived", "deleted")),
        Document.temporary_cleanup_at.is_(None),
        or_(
            Document.is_temporary.is_(False),
            and_(
                Document.is_temporary.is_(True),
                Document.chat_session_id.is_not(None),
                owned_session,
            ),
        ),
        or_(
            Document.is_temporary.is_(False),
            Document.expires_at.is_(None),
            Document.expires_at > now(),
        ),
    )
    if not include_temporary:
        statement = statement.where(Document.is_temporary.is_(False))
    if space_id:
        statement = statement.where(Document.space_id == space_id)
    if visible_space_ids is not None:
        statement = statement.where(
            or_(Document.space_id.is_(None), Document.space_id.in_(visible_space_ids))
        )
    if query and query.strip():
        statement = statement.where(Document.name.ilike(f"%{query.strip()}%"))
    documents = list(
        db.scalars(
            statement.order_by(Document.updated_at.desc(), Document.id.desc())
            .offset(max(0, offset))
            .limit(max(1, min(limit, 50)))
        )
    )
    return [
        PersonalUploadResult(
            document_id=document.id,
            name=document.name,
            status=document.status,
            space_id=document.space_id,
            chat_session_id=document.chat_session_id,
            is_temporary=document.is_temporary,
            created_at=document.created_at,
            updated_at=document.updated_at,
            citation=f"document://{document.id}",
        )
        for document in documents
    ]
