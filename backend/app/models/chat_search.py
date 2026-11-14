from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base


class ChatHistoryEmbedding(Base):
    """Durable metadata for an embedding generated from one chat message."""

    __tablename__ = "chat_history_embeddings"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_chat_history_embeddings_message"),
        Index("ix_chat_history_embeddings_owner_status", "owner_user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChatHistoryIndexJob(Base):
    """Idempotent queue entry used to build or refresh chat embeddings."""

    __tablename__ = "chat_history_index_jobs"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_chat_history_index_jobs_message"),
        Index("ix_chat_history_index_jobs_status_next", "status", "next_attempt_at"),
        Index("ix_chat_history_index_jobs_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChatHistoryUsageEvent(Base):
    """Privacy-safe counters for personal history search operations."""

    __tablename__ = "chat_history_usage_events"
    __table_args__ = (
        Index("ix_chat_history_usage_events_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    operation_kind: Mapped[str] = mapped_column(
        String(32), default="search", server_default="search"
    )
    query_type: Mapped[str] = mapped_column(String(32))
    query_hash: Mapped[str] = mapped_column(String(64), default="", server_default="")
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    price_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    input_uncached_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost: Mapped[str] = mapped_column(String(64), default="0", server_default="0")
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    result_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    vector_used: Mapped[bool] = mapped_column(default=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retry_number: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        String(32), default="succeeded", server_default="succeeded"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
