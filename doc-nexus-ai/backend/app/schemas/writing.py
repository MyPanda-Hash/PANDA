from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChapterInput(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class WritingBriefInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audience: str = Field(default="", max_length=4000)
    document_type: str = Field(default="", max_length=128)
    purpose: str = Field(default="", max_length=4000)
    length_hint: str = Field(default="", max_length=500)
    tone: str = Field(default="", max_length=500)
    must_cover: list[str] = Field(default_factory=list, max_length=50)
    forbidden: list[str] = Field(default_factory=list, max_length=50)
    fact_scope: str = Field(default="", max_length=4000)
    citation_style: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=4000)
    preferred_model: str = Field(default="", max_length=255)


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    space_ids: list[str] = Field(max_length=20)
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    budget_limit: str | None = Field(default=None, max_length=32)
    brief: WritingBriefInput | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_retired_template(cls, value: Any) -> Any:
        if isinstance(value, dict) and "pptx_template_revision_id" in value:
            raise ValueError("演示模板库已停用，请移除模板选择后重试")
        return value


class ProjectFromChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    space_ids: list[str] = Field(default_factory=list, max_length=20)
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    seed_markdown: str = Field(default="", max_length=200_000)
    seed_outline: list[ChapterInput] | None = Field(default=None, max_length=100)
    source_chat_hint: str | None = Field(default=None, max_length=500)
    source_chat_session_id: str | None = Field(default=None, max_length=64)
    source_chat_assistant_message_id: str | None = Field(default=None, max_length=64)
    budget_limit: str | None = Field(default=None, max_length=32)
    brief: WritingBriefInput | None = None


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=255)
    space_ids: list[str] | None = Field(default=None, max_length=20)
    document_ids: list[str] | None = Field(default=None, max_length=100)
    budget_limit: str | None = Field(default=None, max_length=32)
    brief: WritingBriefInput | None = None
    # Quote validity, carried onto the issued document.
    valid_until: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_retired_template(cls, value: Any) -> Any:
        if isinstance(value, dict) and "pptx_template_revision_id" in value:
            raise ValueError("演示模板库已停用，不能再绑定或更换模板")
        return value


class OutlineUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapters: list[ChapterInput] = Field(min_length=1, max_length=100)


class ClaimCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    support_state: Literal["supported", "uncertain", "unsupported"]


class ChapterSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(max_length=200000)
    # Revision the client edited from. Omitted by clients that predate
    # multi-editor support, which keeps the historical last-writer-wins save.
    base_revision: int | None = Field(default=None, ge=1)


class ChapterRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_content: str = Field(max_length=200000)


class RewriteFactLock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    citation_ids: list[str] = Field(default_factory=list, max_length=50)


class DocumentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapter_ids: list[str] = Field(default_factory=list, max_length=100)


class RewriteRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapter_id: str = Field(min_length=1, max_length=64)
    instruction: str = Field(min_length=1, max_length=4000)
    selection_start: int = Field(ge=0)
    selection_end: int = Field(gt=0)
    fact_locks: list[RewriteFactLock] = Field(default_factory=list, max_length=100)


class ChapterResponse(BaseModel):
    id: str
    position: int
    title: str
    content: str
    revision: int = 1
    last_editor_user_id: str | None = None
    edit_lock_user_id: str | None = None
    edit_lock_expires_at: datetime | None = None
    updated_at: datetime


class OutlineResponse(BaseModel):
    id: str
    status: Literal["draft", "confirmed"]
    confirmed_at: datetime | None
    chapters: list[ChapterResponse]


class ClaimResponse(BaseModel):
    id: str
    text: str
    support_state: Literal["supported", "uncertain", "unsupported"]
    created_at: datetime


class OperationResponse(BaseModel):
    id: str
    kind: str
    status: Literal[
        "queued",
        "dispatching",
        "running",
        "completed",
        "failed",
        "cancelling",
        "cancelled",
        "stopped",
    ]
    usage: dict
    result: dict
    attempt: int = 0
    last_event_cursor: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None


class WritingBriefResponse(BaseModel):
    revision: int
    audience: str = ""
    document_type: str = ""
    purpose: str = ""
    length_hint: str = ""
    tone: str = ""
    must_cover: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    fact_scope: str = ""
    citation_style: str = ""
    notes: str = ""
    preferred_model: str = ""


class CitationBindingResponse(BaseModel):
    """存为文稿时的证据阻断摘要，与 exports:preflight 的证据分支同源。

    只在创建响应里计算一次，用于保存后的一行提示；不阻止草稿保存，
    也不改变正式导出闸门。逐处位置与来源明细在 exports:preflight 里查看。
    """

    blocking_reasons: list[str] = Field(default_factory=list)
    issue_count: int = 0


class ProjectResponse(BaseModel):
    id: str
    title: str
    doc_status: Literal["draft", "in_review", "approved", "issued"] = "draft"
    doc_number: str | None = None
    issued_at: datetime | None = None
    valid_until: datetime | None = None
    space_ids: list[str]
    document_ids: list[str] = Field(default_factory=list)
    budget_limit: str | None
    supported_export_formats: list[Literal["docx", "pptx"]]
    # Read-only historical binding; no longer used by ordinary exports.
    pptx_template_revision_id: str | None = None
    # 仅存为文稿的创建响应计算，用于保存后提示；其余接口恒为 null。
    citation_binding: CitationBindingResponse | None = None
    brief: WritingBriefResponse
    outline: OutlineResponse
    claims: list[ClaimResponse]
    versions: list[VersionResponse]
    operations: list[OperationResponse]
    created_at: datetime
    updated_at: datetime


class ProjectSummaryResponse(BaseModel):
    """列表项只回计数与状态：正文、历史版本和操作明细留给单项目详情。"""

    id: str
    title: str
    space_ids: list[str]
    supported_export_formats: list[Literal["docx", "pptx"]]
    outline_status: Literal["draft", "confirmed"]
    chapter_count: int
    version_count: int
    unsupported_claim_count: int
    completed_export_count: int
    updated_at: datetime


class ProjectSummaryListResponse(BaseModel):
    items: list[ProjectSummaryResponse]
    # 按 updated_at,id 倒序的游标；为空表示已到末页。
    next_cursor: str | None = None


WritingMemberRole = Literal["editor", "commenter", "viewer"]


class WritingMemberCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=64)
    role: WritingMemberRole


class WritingMemberUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: WritingMemberRole


class WritingMemberDirectoryUserResponse(BaseModel):
    id: str
    username: str
    display_name: str


class WritingMemberDirectoryResponse(BaseModel):
    items: list[WritingMemberDirectoryUserResponse]


class WritingMemberResponse(BaseModel):
    id: str
    project_id: str
    user_id: str
    user_display_name: str
    username: str | None = None
    role: str
    invited_by_user_id: str
    created_at: datetime
    updated_at: datetime


class WritingMemberListResponse(BaseModel):
    """Members of one project.

    ``owner`` is the implicit owner grant (no row in ``writing_project_members``);
    ``items`` holds the explicit collaborators. ``my_role`` is the acting user's
    effective role, so the client can render capabilities without re-deriving
    them.
    """

    owner: WritingMemberResponse
    items: list[WritingMemberResponse]
    my_role: str


class WritingSuggestionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapter_id: str = Field(min_length=1, max_length=64)
    proposed_content: str = Field(min_length=1, max_length=200000)


class WritingProjectPublicationResponse(BaseModel):
    id: str
    source_project_id: str
    source_owner_user_id: str
    source_owner_name: str
    shared_by_user_id: str
    shared_by_name: str
    title: str
    summary: str
    version: int
    share_scope: str
    allow_cite: bool
    allow_remix: bool
    status: str
    chapter_count: int
    content_length: int
    can_revoke: bool = False
    # published_by_me：我发布（或原稿属于我）的快照；shared_with_me：别人共享给我的。
    relation: Literal["published_by_me", "shared_with_me"] = "shared_with_me"
    published_at: str
    revoked_at: str | None = None


class WritingProjectPublicationDetailResponse(WritingProjectPublicationResponse):
    chapters: list[dict[str, Any]] = Field(default_factory=list)
    evidence_sources: list[dict[str, Any]] = Field(default_factory=list)


class ChapterConflictResponse(BaseModel):
    """409 body for a chapter save that raced another editor."""

    error_code: Literal["chapter_revision_conflict"] = "chapter_revision_conflict"
    chapter_id: str
    current_revision: int
    current_content: str
    submitted_content: str
    last_editor_user_id: str | None = None
    last_editor_name: str | None = None
    updated_at: datetime


class ChapterEditLockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ttl_seconds: int = Field(default=120, ge=30, le=600)


class ChapterEditLockResponse(BaseModel):
    chapter_id: str
    edit_lock_user_id: str | None = None
    edit_lock_user_name: str | None = None
    edit_lock_expires_at: datetime | None = None
    held_by_me: bool = False


class ExportPreflightResponse(BaseModel):
    blocking_claim_ids: list[str]
    blocking_reasons: list[str] = Field(default_factory=list)
    can_export: bool
    format: Literal["docx", "pptx"] = "docx"
    # Stable codes with a safe message and the affected chapter. Never mixed
    # into blocking_reasons: a warning informs, it does not block.
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    # Concrete numbers for the current ordinary export plan.
    page_count: int | None = None
    coverage: dict[str, Any] = Field(default_factory=dict)
    # Digest of the frozen content view and renderer configuration. Used to
    # detect drift between preflight and export; it is not an authorization.
    preflight_fingerprint: str | None = None
    evidence_blockers: list[dict[str, Any]] = Field(default_factory=list)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["docx", "pptx"] = "docx"
    # Carried back from the preflight the user actually saw.
    preflight_fingerprint: str | None = Field(default=None, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def reject_retired_template(cls, value: Any) -> Any:
        if isinstance(value, dict) and (
            "template" in value or "presentation_style" in value
        ):
            raise ValueError("演示模板库已停用，请移除模板和套版样式后普通导出")
        return value


class EvidenceExpandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    citation_id: str = Field(min_length=1, max_length=500)


class EvidenceExpandResponse(BaseModel):
    citation_id: str
    document_id: str
    document_name: str
    document_version_id: str
    version_number: int
    space_id: str | None = None
    char_start: int
    char_end: int
    window_start: int
    window_end: int
    excerpt: str
    highlighted: str = ""
    truncated: bool = False
    page_or_section: str = ""
    untrusted: bool = True
    support_state: Literal["supported", "uncertain", "unsupported"] = "uncertain"
    page_number: int | None = None
    page_block_id: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    page_image_path: str | None = None
    text_sha256: str | None = None
    binding_state: Literal["verified", "stale"] = "stale"
    stale_reason: str | None = None


class VersionResponse(BaseModel):
    id: str
    chapter_id: str
    chapter_title: str
    content: str
    kind: str
    created_at: datetime


# ── Knowledge Graph integration ────────────────────────────────────


class KnowledgeMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000, description="写作需求/查询文本")
    chapter_title: str | None = Field(
        default=None, max_length=255, description="当前章节标题（可选，用于概念提取）"
    )
    entity_types: list[str] | None = Field(
        default=None, max_length=10, description="限定实体类型"
    )
    concepts: list[str] = Field(
        default_factory=list, max_length=20, description="需检测覆盖度的概念列表"
    )


class KnowledgeEntityResponse(BaseModel):
    name: str
    entity_type: str
    description: str
    aliases: list[str]
    score: float
    match_reason: str


class KnowledgeCommunityResponse(BaseModel):
    topic: str
    cohesion: float
    entities: list[KnowledgeEntityResponse]


class KnowledgeSurpriseResponse(BaseModel):
    entity_a: str
    entity_b: str
    relevance_score: float
    common_neighbors: list[str]
    suggestion: str


class KnowledgeGapResponse(BaseModel):
    concept: str
    coverage_score: float
    suggestion: str


class WritingContextResponse(BaseModel):
    system_prompt_enrichment: str
    key_entities: list[str]
    community_briefs: list[str]
    suggested_claims: list[str]
    warnings: list[str]
    token_estimate: int


class KnowledgeMatchResponse(BaseModel):
    matched_entities: list[KnowledgeEntityResponse]
    communities: list[KnowledgeCommunityResponse]
    surprises: list[KnowledgeSurpriseResponse]
    gaps: list[KnowledgeGapResponse]
    writing_context: WritingContextResponse | None = None


class OperationEventResponse(BaseModel):
    event_id: int
    type: str
    stage: str | None = None
    agent_name: str | None = None
    tool_name: str | None = None
    chapter_id: str | None = None
    payload: dict[str, Any]
    created_at: str


class OperationEventListResponse(BaseModel):
    items: list[OperationEventResponse]
    next_cursor: int


class WritingChapterPreview(BaseModel):
    """Latest cumulative Markdown preview of one chapter attempt."""

    operation_id: str
    chapter_id: str
    preview_id: str
    attempt: int
    base_revision: int
    event_id: int
    status: Literal[
        "preparing",
        "streaming",
        "validating",
        "saved",
        "candidate",
        "failed",
        "cancelled",
        "unavailable",
    ]
    markdown: str
    candidate_id: str | None = None
    revision: int | None = None
    error_message: str | None = None
    updated_at: str


class WritingChapterPreviewListResponse(BaseModel):
    items: list[WritingChapterPreview]
    #: Ordered chapter ids this operation generates (outline order; a subset
    #: run lists only what it asked for), so a reloaded client knows the run's
    #: actual targets even for chapters that have no preview yet.
    target_chapter_ids: list[str] = Field(default_factory=list)


class CandidateResponse(BaseModel):
    id: str
    writing_operation_id: str
    kind: Literal["outline", "section", "verification", "document"]
    chapter_id: str | None
    candidate_revision: int
    payload: dict[str, Any]
    status: Literal["pending", "published", "rejected"]
    origin: Literal["agent", "human"] = "agent"
    author_user_id: str | None = None
    author_name: str | None = None
    created_at: datetime
    published_at: datetime | None
    stale: bool = False


class RecommendDocumentsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill: str = Field(default="", max_length=128)
    goal: str = Field(default="", max_length=4000)
    space_ids: list[str] = Field(min_length=1, max_length=20)
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    top_k: int = Field(default=8, ge=1, le=50)
    threshold: float = Field(default=0.3, ge=0.0, le=1.0)


class RecommendDocumentItem(BaseModel):
    document_id: str
    score: float
    lex_score: float
    graph_score: float
    vector_score: float


class RecommendDocumentsResponse(BaseModel):
    skill: str
    query: str
    recommendations: list[RecommendDocumentItem]
    selected_count: int
    total: int
    threshold: float
