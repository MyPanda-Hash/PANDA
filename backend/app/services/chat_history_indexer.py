"""Background indexing for private chat-history semantic search."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import socket
import time
import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import and_, or_, select

from app.core.config import settings
from app.core.database import transaction
from app.core.security import now
from app.models.chat import ChatMessage
from app.models.chat_search import ChatHistoryIndexJob
from app.repositories import personal_search
from app.services.chat_history_vector_store import ChatHistoryVectorStore

logger = logging.getLogger(__name__)

EmbeddingFunction = Callable[[list[str]], list[list[float]]]


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _content_hash(message: ChatMessage) -> str:
    payload = f"{message.role}\n{message.content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _embedding_function() -> EmbeddingFunction:
    from app.services.pgvector_store import _build_embedding_function

    # 历史消息没有持久化的数据等级，不能按 owner 关系或系统默认值降级。
    # 只有可接收最高 restricted 的实际目标才能处理这类未分类原文。
    with transaction() as db:
        return _build_embedding_function(db=db, data_level="restricted")


class ChatHistoryIndexDispatcher:
    """Claim one queued history job, embed it, and persist an idempotent result."""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        embedding_fn: EmbeddingFunction | None = None,
        max_attempts: int | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        self.worker_id = worker_id or _worker_id()
        self.embedding_fn = embedding_fn
        self.max_attempts = max_attempts or settings.writing_dispatch_max_attempts
        self.lease_seconds = max(60, lease_seconds or settings.writing_dispatch_lease_seconds)
        self.vector_store = ChatHistoryVectorStore()

    def _claim(self) -> tuple[str, str, int] | None:
        stamp = now()
        with transaction() as db:
            statement = (
                select(ChatHistoryIndexJob)
                .where(
                    or_(
                        and_(
                            ChatHistoryIndexJob.status == "pending",
                            or_(
                                ChatHistoryIndexJob.next_attempt_at.is_(None),
                                ChatHistoryIndexJob.next_attempt_at <= stamp,
                            ),
                        ),
                        and_(
                            ChatHistoryIndexJob.status == "running",
                            or_(
                                ChatHistoryIndexJob.lease_expires_at.is_(None),
                                ChatHistoryIndexJob.lease_expires_at <= stamp,
                            ),
                        ),
                    ),
                )
                .order_by(ChatHistoryIndexJob.created_at.asc(), ChatHistoryIndexJob.id.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            job = db.scalar(statement)
            if job is None:
                return None
            job.status = "running"
            job.attempts = int(job.attempts or 0) + 1
            job.lease_owner = self.worker_id
            job.lease_expires_at = stamp + timedelta(seconds=self.lease_seconds)
            job.next_attempt_at = None
            job.updated_at = stamp
            return job.id, job.message_id, job.attempts

    def _mark_failure(
        self,
        job_id: str,
        message_id: str,
        attempts: int,
        error: Exception,
        *,
        latency_ms: int,
        provider: str | None,
        model: str | None,
        usage: dict[str, object] | None,
    ) -> None:
        with transaction() as db:
            job = db.get(ChatHistoryIndexJob, job_id)
            if (
                job is None
                or job.status != "running"
                or job.lease_owner != self.worker_id
            ):
                return
            exhausted = attempts >= max(1, self.max_attempts)
            job.status = "failed" if exhausted else "pending"
            job.last_error = type(error).__name__[:500]
            job.next_attempt_at = (
                None
                if exhausted
                else now() + timedelta(seconds=min(300, 2 ** min(attempts, 8)))
            )
            job.updated_at = now()
            job.lease_owner = None
            job.lease_expires_at = None
            personal_search.record_embedding_usage(
                db,
                owner_user_id=job.owner_user_id,
                message_id=message_id if db.get(ChatMessage, message_id) else None,
                provider=provider,
                model=model,
                usage=usage,
                retry_number=attempts,
                status=job.status,
                latency_ms=latency_ms,
            )

    def run_once(self) -> bool:
        claimed = self._claim()
        if claimed is None:
            return False
        job_id, message_id, attempts = claimed
        started = time.monotonic()
        provider = "embedding_api"
        model = settings.embedding_api_model
        usage: dict[str, object] | None = None
        try:
            with transaction() as db:
                message = db.get(ChatMessage, message_id)
            if message is None or message.role != "user" or not message.content.strip():
                raise RuntimeError("chat message is unavailable for indexing")
            embed = self.embedding_fn or _embedding_function()
            model = getattr(embed, "_model", None) or settings.embedding_api_model
            vectors = embed([f"{message.role}: {message.content}"])
            raw_usage = getattr(embed, "last_usage", None)
            usage = dict(raw_usage) if isinstance(raw_usage, dict) else None
            if len(vectors) != 1 or not vectors[0]:
                raise RuntimeError("embedding response is empty")
            vector = [float(value) for value in vectors[0]]
            if not all(math.isfinite(value) for value in vector):
                raise RuntimeError("embedding response contains non-finite values")
            expected_dimensions = settings.embedding_dimensions
            if (
                isinstance(expected_dimensions, int)
                and expected_dimensions > 0
                and len(vector) != expected_dimensions
            ):
                raise RuntimeError(
                    f"embedding dimension mismatch: expected {expected_dimensions}, got {len(vector)}"
                )
            stamp = now()
            with transaction() as db:
                job = db.get(ChatHistoryIndexJob, job_id)
                message = db.get(ChatMessage, message_id)
                if (
                    job is None
                    or message is None
                    or job.status != "running"
                    or job.lease_owner != self.worker_id
                ):
                    return True
                self.vector_store.upsert(
                    db,
                    message_id=message.id,
                    owner_user_id=message.owner_user_id,
                    embedding=vector,
                    content_hash=_content_hash(message),
                    provider=provider,
                    model=model,
                    status="ready",
                )
                job.status = "succeeded"
                job.last_error = None
                job.lease_owner = None
                job.lease_expires_at = None
                job.updated_at = stamp
                from app.services.pricing import fill_usage_cost, find_active_price

                priced_usage = dict(usage or {})
                priced_usage.setdefault("provider", provider)
                priced_usage.setdefault("model", model)
                fill_usage_cost(db, priced_usage)
                if not priced_usage.get("price_version_id"):
                    price = find_active_price(db, provider=provider, model=model)
                    if price is not None:
                        priced_usage["price_version_id"] = price.id
                        priced_usage["currency"] = price.currency
                personal_search.record_embedding_usage(
                    db,
                    owner_user_id=message.owner_user_id,
                    message_id=message.id,
                    provider=provider,
                    model=model,
                    usage=priced_usage,
                    retry_number=attempts,
                    status="succeeded",
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            return True
        except Exception as exc:
            logger.warning(
                "Chat history indexing failed worker=%s job=%s error=%s",
                self.worker_id,
                job_id,
                type(exc).__name__,
            )
            self._mark_failure(
                job_id,
                message_id,
                attempts,
                exc,
                latency_ms=int((time.monotonic() - started) * 1000),
                provider=provider,
                model=model,
                usage=usage,
            )
            return True


__all__ = ["ChatHistoryIndexDispatcher"]
