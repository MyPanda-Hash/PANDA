from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, query_expression, relationship

if TYPE_CHECKING:
    from app.models.image_generation import ImageGenerationTask
    from app.models.video_generation import VideoGenerationTask

from app.models.auth import Base


class ChatCategory(Base):
    __tablename__ = "chat_categories"
    __table_args__ = (
        Index("ix_chat_categories_owner_sort", "owner_user_id", "sort_order"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sessions: Mapped[list[ChatSession]] = relationship(back_populates="category")


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_owner_updated", "owner_user_id", "updated_at"),
        Index("ix_chat_sessions_owner_pinned", "owner_user_id", "pinned", "pinned_at"),
        Index(
            "ix_chat_sessions_owner_archived_updated",
            "owner_user_id",
            "archived",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    messages_revision: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    title_finalized: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    preferred_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    writing_project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    forked_from_session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    forked_from_message_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    summary_message_count: Mapped[int | None] = query_expression()
    category: Mapped[ChatCategory | None] = relationship(back_populates="sessions")
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at, ChatMessage.id",
    )


class ChatDispatchDecision(Base):
    __tablename__ = "chat_dispatch_decisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "client_message_id",
            name="uq_chat_dispatch_decisions_owner_client_message",
        ),
        CheckConstraint(
            "predicted_intent in "
            "('chat', 'image_generate', 'image_edit', 'video_generate', 'clarify')",
            name="ck_chat_dispatch_decisions_predicted_intent",
        ),
        CheckConstraint(
            "effective_route in "
            "('chat', 'image_generate', 'image_edit', 'video_generate', 'clarify')",
            name="ck_chat_dispatch_decisions_effective_route",
        ),
        CheckConstraint(
            "status in "
            "('resolved', 'awaiting_confirmation', 'dispatching', "
            "'dispatched', 'failed', 'outcome_unknown', 'superseded')",
            name="ck_chat_dispatch_decisions_status",
        ),
        Index(
            "ix_chat_dispatch_decisions_owner_created",
            "owner_user_id",
            "created_at",
        ),
        Index(
            "ix_chat_dispatch_decisions_session_created",
            "chat_session_id",
            "created_at",
        ),
        Index(
            "ix_chat_dispatch_decisions_status_updated",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chat_session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    client_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_skill: Mapped[str | None] = mapped_column(String(64), nullable=True)
    predicted_intent: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_route: Mapped[str] = mapped_column(
        String(32), nullable=False, default="chat", server_default="chat"
    )
    final_intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    route_source: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence_band: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    classifier_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    classifier_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    classifier_usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    attachment_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="resolved", server_default="resolved"
    )
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="dispatch",
        foreign_keys="ChatMessage.dispatch_id",
    )
    image_tasks: Mapped[list[ImageGenerationTask]] = relationship(
        back_populates="dispatch",
        foreign_keys="ImageGenerationTask.dispatch_id",
    )
    video_tasks: Mapped[list[VideoGenerationTask]] = relationship(
        back_populates="dispatch",
        foreign_keys="VideoGenerationTask.dispatch_id",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_session_created", "session_id", "created_at"),
        Index(
            "ix_chat_messages_owner_role_created",
            "owner_user_id",
            "role",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    dispatch_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_dispatch_decisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    thinking: Mapped[str | None] = mapped_column(Text, nullable=True)
    parts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    space_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    document_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    variant_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    variant_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    variant_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    references_json: Mapped[str] = mapped_column(Text, default="[]")
    session_references_json: Mapped[str] = mapped_column(Text, default="[]")
    writing_references_json: Mapped[str] = mapped_column(Text, default="[]")
    images_json: Mapped[str] = mapped_column(Text, default="[]")
    retrieval_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieval_trace_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    writing_project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    writing_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session: Mapped[ChatSession] = relationship(back_populates="messages")
    dispatch: Mapped[ChatDispatchDecision | None] = relationship(
        back_populates="messages"
    )


class ChatSessionPublication(Base):
    __tablename__ = "chat_session_publications"
    __table_args__ = (
        Index("ix_chat_publications_status_published", "status", "published_at"),
        Index("ix_chat_publications_source_session", "source_session_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    source_owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    shared_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    snapshot_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    share_scope: Mapped[str] = mapped_column(String(32), default="all_internal")
    space_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    allow_cite: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_remix: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
