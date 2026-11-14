from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.auth import Base


TEMPLATE_VISIBILITIES = ("private", "company_shared")
TEMPLATE_OUTPUT_FORMATS = ("docx", "pptx")
TEMPLATE_STATUSES = ("active", "archived", "deleted")
TEMPLATE_SOURCE_FORMATS = ("pptx", "potx")
TEMPLATE_REVISION_STATES = (
    "uploading",
    "pending",
    "compiling",
    "awaiting_confirmation",
    "published",
    "failed",
)
TEMPLATE_PREVIEW_STATES = ("missing", "pending", "generating", "ready", "failed")
# Every state in which a revision still occupies an upload lease or a worker.
TEMPLATE_ACTIVE_STATES = ("uploading", "pending", "compiling", "awaiting_confirmation")


class WritingProject(Base):
    __tablename__ = "writing_projects"
    __table_args__ = (
        Index("ix_writing_projects_owner_updated", "owner_user_id", "updated_at"),
        UniqueConstraint(
            "owner_user_id",
            "source_chat_session_id",
            "source_chat_assistant_message_id",
            name="uq_writing_projects_chat_auto_export",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    selected_space_ids: Mapped[str] = mapped_column(Text)
    selected_document_ids: Mapped[str] = mapped_column(Text, default="[]")
    budget_limit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    brief_json: Mapped[str] = mapped_column(Text, default="{}")
    brief_revision: Mapped[int] = mapped_column(Integer, default=1)
    pi_agent_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pi_session_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pi_lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pi_lease_operation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pi_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_chat_session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    source_chat_assistant_message_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    # Current document lifecycle is draft -> issued. Legacy review statuses are
    # retained for existing rows and are never used to gate export.
    doc_status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default="draft"
    )
    # Historical review metadata remains stored, with no active API controls.
    review_required: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, server_default=sa.text("0")
    )
    approved_review_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    doc_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Optional PPTX layout preference. ``None`` means "use the built-in theme".
    # Deliberately format-specific so a future DOCX template cannot be bound
    # through the same column.
    pptx_template_revision_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("writing_doc_template_revisions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outline: Mapped[WritingOutline | None] = relationship(
        back_populates="project", uselist=False, cascade="all, delete-orphan"
    )
    claims: Mapped[list[WritingClaim]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    operations: Mapped[list[WritingOperation]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class WritingProjectMember(Base):
    """Per-project collaborator grant.

    The project owner is implicit (``WritingProject.owner_user_id``) and never
    materialised as a row, so existing projects need no data backfill.
    """

    __tablename__ = "writing_project_members"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "user_id", name="uq_writing_member_project_user"
        ),
        CheckConstraint(
            "role in ('owner', 'editor', 'commenter', 'viewer')",
            name="ck_writing_members_role",
        ),
        CheckConstraint(
            "status in ('active', 'removed')", name="ck_writing_members_status"
        ),
        Index("ix_writing_members_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("writing_projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    invited_by_user_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WritingReview(Base):
    """One round of "please approve this document".

    ``content_hash`` records the body the reviewer was asked to look at, so an
    approval can never silently apply to text written after the request.
    """

    __tablename__ = "writing_reviews"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_writing_reviews_status",
        ),
        Index("ix_writing_reviews_project_round", "project_id", "round"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("writing_projects.id", ondelete="CASCADE"), index=True
    )
    round: Mapped[int] = mapped_column(Integer)
    requested_by_user_id: Mapped[str] = mapped_column(String(64))
    reviewer_user_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    decision_note: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    open_comment_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WritingComment(Base):
    """A review comment bound to a range of chapter text.

    Historical rows only: the comment surfaces are disabled, so nothing writes
    or re-anchors these records any more. The anchor keeps the same
    character-offset contract as ``RewriteRunRequest.selection_start/end``;
    ``anchor_quote`` plus the neighbouring context hashes recorded where the
    anchor sat when it was written.
    """

    __tablename__ = "writing_comments"
    __table_args__ = (
        CheckConstraint(
            "status in ('open', 'resolved', 'outdated')",
            name="ck_writing_comments_status",
        ),
        CheckConstraint(
            "anchor_state in ('exact', 'shifted', 'lost', 'chapter')",
            name="ck_writing_comments_anchor_state",
        ),
        Index(
            "ix_writing_comments_project_status",
            "project_id",
            "status",
            "created_at",
        ),
        Index("ix_writing_comments_chapter_status", "chapter_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("writing_projects.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[str] = mapped_column(String(64), index=True)
    # First comment of a thread points at itself; replies carry the root id.
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    author_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    mentions_json: Mapped[str] = mapped_column(Text, default="[]")
    anchor_version_id: Mapped[str] = mapped_column(String(64), default="")
    anchor_start: Mapped[int] = mapped_column(Integer, default=0)
    anchor_end: Mapped[int] = mapped_column(Integer, default=0)
    anchor_quote: Mapped[str] = mapped_column(Text, default="")
    anchor_prefix_sha256: Mapped[str] = mapped_column(String(64), default="")
    anchor_suffix_sha256: Mapped[str] = mapped_column(String(64), default="")
    anchor_state: Mapped[str] = mapped_column(String(16), default="exact")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    resolved_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WritingProjectPublication(Base):
    __tablename__ = "writing_project_publications"
    __table_args__ = (
        Index("ix_writing_publications_status_published", "status", "published_at"),
        Index("ix_writing_publications_source_project", "source_project_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_project_id: Mapped[str] = mapped_column(
        ForeignKey("writing_projects.id", ondelete="CASCADE"), index=True
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
    # Frozen at publish time: {"items": [citation id...], "truncated": bool}.
    # NULL means "never recorded" (pre-existing rows) and is deliberately
    # distinct from a recorded empty set. Never recomputed at read time —
    # receipts get pruned and runs change, which would drift old versions.
    citation_refs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    allow_cite: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    allow_remix: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WritingOutline(Base):
    __tablename__ = "writing_outlines"
    __table_args__ = (
        CheckConstraint(
            "status in ('draft', 'confirmed')", name="ck_writing_outlines_status"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("writing_projects.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="draft")
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    project: Mapped[WritingProject] = relationship(back_populates="outline")
    chapters: Mapped[list[WritingChapter]] = relationship(
        back_populates="outline", cascade="all, delete-orphan"
    )


class WritingChapter(Base):
    __tablename__ = "writing_chapters"
    __table_args__ = (
        Index("ix_writing_chapters_outline_position", "outline_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    outline_id: Mapped[str] = mapped_column(
        ForeignKey("writing_outlines.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, default="")
    # Optimistic concurrency: bumped on every persisted content change.
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_editor_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Advisory ("soft") edit lock — informs other editors, never blocks a save.
    edit_lock_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    edit_lock_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outline: Mapped[WritingOutline] = relationship(back_populates="chapters")
    versions: Mapped[list[WritingVersion]] = relationship(
        back_populates="chapter", cascade="all, delete-orphan"
    )


class WritingVersion(Base):
    __tablename__ = "writing_versions"
    __table_args__ = (
        Index("ix_writing_versions_chapter_created", "chapter_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chapter_id: Mapped[str] = mapped_column(
        ForeignKey("writing_chapters.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    chapter: Mapped[WritingChapter] = relationship(back_populates="versions")


class WritingClaim(Base):
    __tablename__ = "writing_claims"
    __table_args__ = (
        CheckConstraint(
            "support_state in ('supported', 'uncertain', 'unsupported')",
            name="ck_writing_claims_support_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("writing_projects.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    support_state: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    project: Mapped[WritingProject] = relationship(back_populates="claims")


class WritingOperation(Base):
    __tablename__ = "writing_operations"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued', 'dispatching', 'running', 'completed', 'failed', "
            "'cancelling', 'cancelled', 'stopped')",
            name="ck_writing_operations_status",
        ),
        UniqueConstraint(
            "project_id", "idempotency_key", name="uq_writing_operation_idempotency"
        ),
        Index("ix_writing_operations_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("writing_projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    input_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_payload: Mapped[str] = mapped_column(Text, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message_safe: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_event_cursor: Mapped[int] = mapped_column(Integer, default=0)
    usage: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    project: Mapped[WritingProject] = relationship(back_populates="operations")


class AgentRunBinding(Base):
    __tablename__ = "agent_run_bindings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    writing_operation_id: Mapped[str] = mapped_column(
        ForeignKey("writing_operations.id", ondelete="CASCADE"), unique=True, index=True
    )
    platform_run_id: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    team_id: Mapped[str] = mapped_column(String(128))
    team_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    last_event_cursor: Mapped[int] = mapped_column(Integer, default=0)
    manifest_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    usage_summary: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AgentEventReceipt(Base):
    __tablename__ = "agent_event_receipts"
    __table_args__ = (
        UniqueConstraint("platform_run_id", "event_id", name="uq_agent_event_receipt"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform_run_id: Mapped[str] = mapped_column(String(96), index=True)
    event_id: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WritingToolReceipt(Base):
    __tablename__ = "writing_tool_receipts"
    __table_args__ = (
        UniqueConstraint(
            "platform_run_id", "action_key", name="uq_writing_tool_action"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform_run_id: Mapped[str] = mapped_column(String(96), index=True)
    tool_call_id: Mapped[str] = mapped_column(String(128))
    action_key: Mapped[str] = mapped_column(String(256))
    tool_name: Mapped[str] = mapped_column(String(128))
    request_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    result_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WritingCandidate(Base):
    __tablename__ = "writing_candidates"
    __table_args__ = (
        UniqueConstraint(
            "writing_operation_id",
            "kind",
            "chapter_id",
            "candidate_revision",
            name="uq_writing_candidate_revision",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    writing_operation_id: Mapped[str] = mapped_column(
        ForeignKey("writing_operations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("writing_projects.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    candidate_revision: Mapped[int] = mapped_column(Integer)
    source_platform_run_id: Mapped[str] = mapped_column(String(96))
    source_tool_call_id: Mapped[str] = mapped_column(String(128))
    payload_json: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # A human "suggestion" is the same object as an agent candidate: same
    # pending/published/rejected lifecycle, same accept/reject path, same
    # staleness check. Only the provenance differs.
    origin: Mapped[str] = mapped_column(
        String(16), default="agent", server_default="agent"
    )
    author_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Historical linkage only; new suggestions do not accept comment IDs.
    comment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WritingDispatchOutbox(Base):
    __tablename__ = "writing_dispatch_outbox"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("writing_operations.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_safe: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WritingDocTemplate(Base):
    """A user-owned presentation template asset.

    Metadata only: the bytes live in a revision. Owners manage their own
    templates and may share a published revision with the company; there is no
    template-specific role, approval step or per-person ACL.

    ``visibility`` and ``status`` are mutable metadata. Everything a published
    revision froze (source, pack, mapping) is immutable — a design change
    creates a new revision, never an in-place edit.
    """

    __tablename__ = "writing_doc_templates"
    __table_args__ = (
        UniqueConstraint(
            "created_by_user_id",
            "create_idempotency_key",
            name="uq_writing_doc_templates_create_idempotency",
        ),
        CheckConstraint(
            "visibility in ('private', 'company_shared')",
            name="ck_writing_doc_templates_visibility",
        ),
        CheckConstraint(
            "output_format in ('docx', 'pptx')",
            name="ck_writing_doc_templates_output_format",
        ),
        CheckConstraint(
            "status in ('active', 'archived', 'deleted')",
            name="ck_writing_doc_templates_status",
        ),
        Index(
            "ix_writing_doc_templates_owner_updated",
            "created_by_user_id",
            "updated_at",
        ),
        Index(
            "ix_writing_doc_templates_visibility_status",
            "visibility",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    visibility: Mapped[str] = mapped_column(
        String(32), default="private", server_default="private"
    )
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    create_idempotency_key: Mapped[str] = mapped_column(String(256))
    create_request_sha256: Mapped[str] = mapped_column(String(64))
    # ``*`` means "every 文稿 type that supports this output format".
    skill_key: Mapped[str] = mapped_column(String(64), default="*", server_default="*")
    output_format: Mapped[str] = mapped_column(
        String(16), default="pptx", server_default="pptx"
    )
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revisions: Mapped[list[WritingDocTemplateRevision]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )


class WritingDocTemplateRevision(Base):
    """One immutable-once-published attempt at compiling a source package.

    The row doubles as the durable upload session and the durable compile work
    item: there is no separate upload-session or job table, and template work
    never enters the knowledge-document pipeline or the Pi dispatcher.
    """

    __tablename__ = "writing_doc_template_revisions"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "revision_no", name="uq_writing_doc_revision_number"
        ),
        UniqueConstraint(
            "template_id",
            "idempotency_key",
            name="uq_writing_doc_revision_idempotency",
        ),
        CheckConstraint(
            "state in ('uploading', 'pending', 'compiling', "
            "'awaiting_confirmation', 'published', 'failed')",
            name="ck_writing_doc_revision_state",
        ),
        CheckConstraint(
            "source_format in ('pptx', 'potx')",
            name="ck_writing_doc_revision_source_format",
        ),
        CheckConstraint(
            "preview_state in ('missing', 'pending', 'generating', 'ready', 'failed')",
            name="ck_writing_doc_revision_preview_state",
        ),
        Index("ix_writing_doc_revisions_work", "state", "next_attempt_at"),
        Index("ix_writing_doc_revisions_preview_work", "preview_state", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    template_id: Mapped[str] = mapped_column(
        ForeignKey("writing_doc_templates.id", ondelete="CASCADE"), index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer)
    created_by_user_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    request_sha256: Mapped[str] = mapped_column(String(64), default="")

    # Upload declaration — frozen at creation, never treated as verified truth.
    source_format: Mapped[str] = mapped_column(String(16))
    source_filename: Mapped[str] = mapped_column(String(255), default="")
    expected_source_sha256: Mapped[str] = mapped_column(String(64), default="")
    expected_source_size_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )

    # Upload recovery state. Server-managed; never serialised into a shared view.
    upload_backend: Mapped[str] = mapped_column(String(32), default="")
    staging_object_key: Mapped[str] = mapped_column(String(512), default="")
    multipart_upload_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    part_size_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    upload_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    upload_manifest_json: Mapped[str] = mapped_column(Text, default="{}")

    # Verified original. Empty until the worker re-hashed the merged package.
    source_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_sha256: Mapped[str] = mapped_column(String(64), default="")
    source_size_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )

    # Cleaned base package handed to the renderer. Never the original.
    render_package_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    render_package_sha256: Mapped[str] = mapped_column(String(64), default="")

    compiler_version: Mapped[str] = mapped_column(String(64), default="")
    pack_schema_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    compile_json: Mapped[str] = mapped_column(Text, default="{}")
    pack_sha256: Mapped[str] = mapped_column(String(64), default="")

    # Human configuration. Part of the pack hash once published.
    sample_selection_json: Mapped[str] = mapped_column(Text, default="{}")
    retained_elements_json: Mapped[str] = mapped_column(Text, default="{}")
    slot_map_json: Mapped[str] = mapped_column(Text, default="{}")
    edit_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    prepared_edit_revision: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    # Reserved for the DOCX template path; PPTX keeps it an empty object.
    placeholders_json: Mapped[str] = mapped_column(Text, default="{}")

    state: Mapped[str] = mapped_column(
        String(32), default="uploading", server_default="uploading", index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    compile_stage: Mapped[str] = mapped_column(String(32), default="")

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message_safe: Mapped[str | None] = mapped_column(String(500), nullable=True)

    source_slide_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    sample_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    health_grade: Mapped[str] = mapped_column(String(4), default="")
    health_warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    cleanup_manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    preview_manifest_json: Mapped[str] = mapped_column(Text, default="{}")

    # Derived preview cache. Never part of the pack hash.
    preview_state: Mapped[str] = mapped_column(
        String(16), default="missing", server_default="missing"
    )
    preview_attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    preview_lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    preview_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    preview_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preview_error_message_safe: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    template: Mapped[WritingDocTemplate] = relationship(back_populates="revisions")
