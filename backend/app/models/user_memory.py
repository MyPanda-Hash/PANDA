"""Durable per-employee personal memory.

Six owner-scoped tables back the feature described in
``docs/TODO/2026-09-09-personal-memory-implementation.md``:

``UserMemoryState``
    One row per employee holding the consent switches, the observable
    ``revision`` and the ``epoch`` revocation fence.
``UserMemoryTurn``
    A backend-issued identity for one authenticated raw employee input. Only
    input that actually reached an authenticated entrypoint gets a turn; client
    history sync, fork prefixes and assistant/tool output never do.
``UserMemoryEntry``
    Candidate and current facts. A conflicting candidate is a separate row from
    the active fact it would supersede.
``UserMemoryEvidence``
    The provenance chain. Composite foreign keys make the database itself
    reject linking one employee's fact to another employee's source.
``UserMemoryJob``
    Versioned incremental extraction work, claimed through a database lease.
``UserMemoryOperation``
    Write receipts giving idempotency and real model usage for memory writes.

Ownership is always ``User.id``; neither the Pi/Harness execution identity nor
any display name is an owner. Values stored here are data, never instructions.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.auth import Base

# ---------------------------------------------------------------------------
# Vocabularies. The database keeps these as plain strings; the service layer is
# what validates them, so a future value never needs a data migration here.
# ---------------------------------------------------------------------------

LEARNING_MODES = ("manual", "suggest", "auto_low_risk")
"""``manual`` never scans ordinary chat; ``suggest`` only queues candidates;
``auto_low_risk`` may activate whitelisted, clearly low-risk self statements."""

MEMORY_CHANNELS = ("web", "open", "wecom", "manual")
"""``manual`` is an authenticated user's own confirmation of a structured value.
It is an independent valid source and never fabricates a ``ChatMessage``."""

TURN_STATUSES = ("live", "closed", "invalidated")
"""``closed`` only ends online proposal authority; ``invalidated`` means the
source itself is gone, which is what cancels queued extraction."""

ENTRY_STATUSES = (
    "candidate",
    "active",
    "rejected",
    "superseded",
    "forgotten",
    "expired",
)

ENTRY_ORIGINS = ("manual", "explicit", "automatic")

JOB_STATUSES = ("pending", "running", "succeeded", "failed", "cancelled")

OPERATION_STATUSES = ("pending", "succeeded", "failed")

EVIDENCE_KINDS = ("user_statement", "manual_confirmation")

ACTIVE_ENTRY_STATUS = "active"


class UserMemoryState(Base):
    """One row per employee: consent switches plus the two version counters.

    ``revision`` is the observable state/snapshot-cache version and moves on any
    effective change to content, settings or candidate state. ``epoch`` is the
    revocation barrier and moves only when work already in flight must be
    fenced off - disabling, clearing, or a policy tightening.
    """

    __tablename__ = "user_memory_states"

    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    use_enabled: Mapped[bool] = mapped_column(
        # ``true()`` renders per dialect: TRUE on PostgreSQL, 1 on SQLite.
        # A literal "1" here is accepted by SQLite and rejected outright by
        # PostgreSQL, so the dialect-neutral construct is the only safe one.
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )
    learning_mode: Mapped[str] = mapped_column(
        String(32), default="manual", server_default="manual", nullable=False
    )
    revision: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    epoch: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    # Set by the server when automatic learning starts. Older input stays out of
    # the queue; re-enabling later must not implicitly clear a forget barrier.
    automatic_source_cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserMemoryTurn(Base):
    """A backend-issued identity for one authenticated raw employee input.

    ``source_hash`` is SHA-256 over the UTF-8 bytes of the employee's original
    input - never over the RAG/vision/system-assembled prompt.

    ``uq_user_memory_turns_owner_id`` is a non-partial unique constraint that
    exists purely as the target of ``UserMemoryEvidence``'s composite foreign
    key; the single-column primary key cannot serve that role.
    """

    __tablename__ = "user_memory_turns"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "channel",
            "request_key",
            name="uq_user_memory_turns_owner_channel_request",
        ),
        # Composite FK target for evidence. Covers every status, never partial.
        UniqueConstraint("owner_user_id", "id", name="uq_user_memory_turns_owner_id"),
        Index("ix_user_memory_turns_owner_created", "owner_user_id", "created_at"),
        Index("ix_user_memory_turns_status_expiry", "status", "source_text_expires_at"),
        Index("ix_user_memory_turns_chat_message", "chat_message_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    request_key: Mapped[str] = mapped_column(String(255), nullable=False)
    runtime_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Web sources keep a pointer only; the original chat is never copied here.
    chat_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True
    )
    chat_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Bounded, redacted snapshot kept only when there is no ChatMessage to point
    # at (open API / WeCom). Cleared as soon as extraction is done.
    source_text_safe: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_text_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="live", server_default="live", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserMemoryEntry(Base):
    """A candidate or a current fact for one owner and one whitelisted key.

    Two distinct constraints guard this table and neither replaces the other:

    * ``uq_user_memory_entries_owner_id`` - non-partial, covers every status,
      and is the composite foreign-key target used by evidence.
    * ``uq_user_memory_entries_active_key`` - a partial unique index allowing at
      most one ``status='active'`` row per ``(owner_user_id, key)``.
    """

    __tablename__ = "user_memory_entries"
    __table_args__ = (
        # Composite FK target for evidence. Covers every status, never partial.
        UniqueConstraint("owner_user_id", "id", name="uq_user_memory_entries_owner_id"),
        # At most one active fact per key, on both PostgreSQL and SQLite.
        Index(
            "uq_user_memory_entries_active_key",
            "owner_user_id",
            "key",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index("ix_user_memory_entries_owner_status", "owner_user_id", "status"),
        Index("ix_user_memory_entries_status_expires", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    # JSON-encoded validated value. Wiped to NULL by a forget barrier so no
    # residual value survives, while the tombstone row itself remains.
    value_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="candidate", server_default="candidate", nullable=False
    )
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    # The active version this candidate was raised against, so a stale candidate
    # is re-checked rather than silently overwriting a newer correction.
    expected_active_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Anti-resurrection barrier: automatic extraction from sources older than
    # this instant may never re-create this key.
    forgotten_source_cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserMemoryEvidence(Base):
    """Minimal provenance for one fact - never a copy of the whole session.

    Both composite foreign keys carry ``owner_user_id``, so the database, not
    only application code, rejects attaching employee A's fact to employee B's
    turn (and the reverse).
    """

    __tablename__ = "user_memory_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_user_id", "memory_id"],
            ["user_memory_entries.owner_user_id", "user_memory_entries.id"],
            name="fk_user_memory_evidence_entry",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "turn_id"],
            ["user_memory_turns.owner_user_id", "user_memory_turns.id"],
            name="fk_user_memory_evidence_turn",
            ondelete="CASCADE",
        ),
        # Re-extraction or a forked copy of the same original turn stays one
        # piece of evidence; assistant restatement adds no confidence.
        UniqueConstraint(
            "owner_user_id",
            "memory_id",
            "turn_id",
            name="uq_user_memory_evidence_memory_turn",
        ),
        Index("ix_user_memory_evidence_turn", "owner_user_id", "turn_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    memory_id: Mapped[str] = mapped_column(String(64), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # The safe excerpt actually verified against the source text. Wiped along
    # with the value when the key is forgotten.
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Offsets are Unicode code point positions in the same text the backend
    # verified, never UTF-16 lengths produced by the extension.
    quote_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserMemoryJob(Base):
    """One versioned incremental extraction unit for one registered source.

    Enqueued in the same transaction as the authenticated input registration -
    an ``asyncio.create_task()`` is never proof that extraction was scheduled.
    """

    __tablename__ = "user_memory_jobs"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "turn_id",
            "source_hash",
            "extractor_version",
            name="uq_user_memory_jobs_source_version",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "turn_id"],
            ["user_memory_turns.owner_user_id", "user_memory_turns.id"],
            name="fk_user_memory_jobs_turn",
            ondelete="CASCADE",
        ),
        Index(
            "ix_user_memory_jobs_claimable",
            "status",
            "next_attempt_at",
            "lease_expires_at",
        ),
        Index("ix_user_memory_jobs_owner_status", "owner_user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Bumped on every claim so a renewed-but-superseded worker cannot commit.
    claim_generation: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Redacted error only. Provider payloads and memory values never land here.
    last_error_safe: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserMemoryOperation(Base):
    """A durable write receipt: idempotency plus real model usage.

    ``turn_id``/``job_id``/``memory_id`` are deliberately soft references with
    no database foreign key. A receipt must survive the tombstoning of the row
    it once touched so a replayed request reports the current revoked state
    instead of resurrecting a deleted value.
    """

    __tablename__ = "user_memory_operations"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "kind",
            "request_id",
            name="uq_user_memory_operations_request",
        ),
        Index("ix_user_memory_operations_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    memory_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending", nullable=False
    )
    result_memory_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Real provider/model/token/cost/calls shape. Plain CRUD writes "{}" here;
    # a fixture or default provider must never render as a model card.
    usage_json: Mapped[str] = mapped_column(
        Text, default="{}", server_default="{}", nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
