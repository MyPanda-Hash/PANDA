from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from app.schemas.evidence import RetrievalTraceResponse


class ChatCategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    sort_order: int = 0


class ChatCategoryUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    sort_order: int | None = None


class ChatCategoryResponse(BaseModel):
    id: str
    name: str
    sort_order: int
    created_at: str
    updated_at: str


MediaIntent = Literal[
    "chat",
    "image_generate",
    "image_edit",
    "video_generate",
    "clarify",
]
ChatExplicitMediaSkill = Literal[
    "video-generation",
    "image-generation",
    "image-edit",
]
MediaRouteSource = Literal[
    "explicit_ui",
    "explicit_slash",
    "deterministic_rule",
    "classifier",
    "user_override",
]
MediaConfidenceBand = Literal["high", "medium", "low"]
MediaIntentMode = Literal["off", "shadow", "confirm", "auto"]
MediaDispatchStatus = Literal[
    "resolved",
    "awaiting_confirmation",
    "dispatching",
    "dispatched",
    "failed",
    "outcome_unknown",
    "superseded",
]


class MediaOptions(BaseModel):
    """User-visible media controls accepted by the unified chat endpoint."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    resolution: Literal["1k", "2k"] | None = None
    video_mode: Literal["std", "pro", "4k"] | None = Field(
        default=None,
        validation_alias=AliasChoices("video_mode", "mode"),
    )
    video_duration: int | None = Field(
        default=None,
        ge=3,
        le=15,
        validation_alias=AliasChoices("video_duration", "duration"),
    )
    source_image_index: int | None = Field(default=None, ge=0, le=3)


class MediaRequestInputs(BaseModel):
    """Minimal history-safe snapshot of the original media request controls."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    media_options: MediaOptions = Field(default_factory=MediaOptions)
    requested_skill: ChatExplicitMediaSkill | None = None
    model: str | None = Field(default=None, max_length=160)


class MediaRouteParameters(BaseModel):
    """Safe, displayable parameters. Trusted resource IDs are intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    resolution: Literal["1k", "2k"] | None = None
    video_mode: Literal["std", "pro", "4k"] | None = None
    video_duration: int | None = Field(default=None, ge=3, le=15)
    source_kind: Literal[
        "none",
        "explicit_upload",
        "recent_generated_image",
        "pending_generated_image",
    ] = "none"
    source_image_index: int | None = Field(default=None, ge=0, le=3)


class MediaClassifierOutput(BaseModel):
    """Strict local contract for the untrusted classifier response."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    predicted_intent: Literal[
        "chat",
        "image_generate",
        "image_edit",
        "video_generate",
    ]
    confidence_band: MediaConfidenceBand = "low"
    reason_code: str = Field(
        default="classifier_prediction", min_length=1, max_length=80
    )
    normalized_prompt: str = Field(default="", max_length=32_000)
    resolution: Literal["1k", "2k"] | None = None
    video_mode: Literal["std", "pro", "4k"] | None = None
    video_duration: int | None = Field(default=None, ge=3, le=15)


class MediaClarificationChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["chat", "image_generate", "image_edit", "video_generate"]
    label: str = Field(min_length=1, max_length=40)


class MediaIntentDecision(BaseModel):
    """Auditable routing result; ``source_task_id`` never comes from the model."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision_id: str = Field(min_length=1, max_length=64)
    request_digest: str | None = Field(default=None, max_length=64)
    predicted_intent: MediaIntent
    effective_route: MediaIntent
    final_intent: MediaIntent | None = None
    route_source: MediaRouteSource
    confidence_band: MediaConfidenceBand
    reason_code: str = Field(min_length=1, max_length=80)
    normalized_prompt: str = Field(default="", max_length=32_000)
    source_kind: Literal[
        "none",
        "explicit_upload",
        "recent_generated_image",
        "pending_generated_image",
    ] = "none"
    source_task_id: str | None = Field(default=None, max_length=128)
    resolution: Literal["1k", "2k"] | None = None
    video_mode: Literal["std", "pro", "4k"] | None = None
    video_duration: int | None = Field(default=None, ge=3, le=15)
    missing_inputs: list[str] = Field(default_factory=list, max_length=8)
    requires_confirmation: bool = False
    classifier_model: str | None = Field(default=None, max_length=160)
    classifier_version: str = Field(default="media-intent-v2", max_length=64)
    classifier_usage: dict[str, object] = Field(default_factory=dict)
    classifier_error_code: str | None = Field(default=None, max_length=80)
    status: MediaDispatchStatus = "resolved"
    parameters: MediaRouteParameters = Field(default_factory=MediaRouteParameters)
    clarification_message: str | None = Field(default=None, max_length=1_000)
    clarification_choices: list[MediaClarificationChoice] = Field(
        default_factory=list, max_length=4
    )


class MediaRouteDecisionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["route_decision"] = "route_decision"
    decision_id: str = Field(min_length=1, max_length=64)
    predicted_intent: MediaIntent
    effective_route: MediaIntent
    final_intent: MediaIntent | None = None
    route_source: MediaRouteSource
    confidence_band: MediaConfidenceBand
    reason_code: str = Field(min_length=1, max_length=80)
    requires_confirmation: bool = False
    parameters: MediaRouteParameters = Field(default_factory=MediaRouteParameters)
    request_inputs: MediaRequestInputs | None = None


class MediaClarificationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["clarification"] = "clarification"
    decision_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1_000)
    choices: list[MediaClarificationChoice] = Field(min_length=1, max_length=4)


class MediaTaskEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["media_task"] = "media_task"
    task_kind: Literal["image", "video"]
    task: dict[str, object]
    decision_id: str = Field(min_length=1, max_length=64)


class MediaDispatchDecisionProjection(BaseModel):
    """History-safe decision projection; never carries prompts or image data."""

    model_config = ConfigDict(extra="ignore")

    decision_id: str
    predicted_intent: MediaIntent
    effective_route: MediaIntent
    final_intent: MediaIntent | None = None
    route_source: MediaRouteSource
    confidence_band: MediaConfidenceBand
    reason_code: str
    requires_confirmation: bool = False
    parameters: MediaRouteParameters = Field(default_factory=MediaRouteParameters)
    request_inputs: MediaRequestInputs | None = None
    status: MediaDispatchStatus = "resolved"
    clarification_message: str | None = None
    clarification_choices: list[MediaClarificationChoice] = Field(default_factory=list)


class MediaDispatchFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corrected_intent: (
        Literal[
            "chat",
            "image_generate",
            "image_edit",
            "video_generate",
        ]
        | None
    ) = None


class MediaDispatchFeedbackResponse(BaseModel):
    decision_id: str
    reported_wrong: Literal[True] = True
    corrected_intent: (
        Literal[
            "chat",
            "image_generate",
            "image_edit",
            "video_generate",
        ]
        | None
    ) = None


class ChatImageContent(BaseModel):
    """Image content exchanged with the Pi chat runtime."""

    type: Literal["image"] = "image"
    data: str = Field(min_length=1, max_length=6_000_000)
    mimeType: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]


class ChatHistoryImageContent(BaseModel):
    """Authenticated history projection; the persisted base64 never leaves inline."""

    type: Literal["image"] = "image"
    mimeType: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    url: str = Field(min_length=1, max_length=512)


class ChatWritingContext(BaseModel):
    """Explicit project-writing intent, never inferred from artifact linkage."""

    project_id: str = Field(min_length=1, max_length=64)
    scope_hash: str | None = Field(default=None, max_length=80)


class ChatMessageRequest(BaseModel):
    """Unified chat request, including optional server-side media routing."""

    message: str = Field(default="", max_length=32_000)
    images: list[ChatImageContent] = Field(default_factory=list, max_length=4)
    session_id: str | None = Field(default=None, max_length=128)
    local_session_id: str | None = Field(
        default=None,
        max_length=64,
        description="数字员工本地会话 id；若提供则校验归属并用其 agent_session_id / preferred_model",
    )
    client_message_id: str | None = Field(default=None, max_length=64)
    client_assistant_message_id: str | None = Field(default=None, max_length=64)
    space_ids: list[str] = Field(default_factory=list, max_length=20)
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    writing_context: ChatWritingContext | None = None
    # Kept only to validate the identity of historical completed-turn replays.
    # New template generation and retries are rejected before dispatch.
    template_revision_id: str | None = Field(
        default=None, min_length=1, max_length=64, deprecated=True,
        description="仅用于历史终态回放；演示模板库已停用，不能发起新生成",
    )
    session_reference_ids: list[str] = Field(default_factory=list, max_length=10)
    writing_publication_ids: list[str] = Field(default_factory=list, max_length=10)
    model: str | None = Field(default=None, max_length=160)
    explicit_skill: ChatExplicitMediaSkill | None = None
    selected_skill: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="首行 /skill:<name> 显式选择的通用 Skill；不得从自然语言推断",
    )
    media_options: MediaOptions = Field(default_factory=MediaOptions)
    decision_id: str | None = Field(default=None, max_length=64)
    regenerate_from_message_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "重新生成：要替换的助手消息 id。新回答会作为同一组的新版本落库，"
            "旧回答保留为可切换的历史版本。"
        ),
    )
    final_intent: (
        Literal[
            "chat",
            "image_generate",
            "image_edit",
            "video_generate",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def _require_message_or_images(self) -> ChatMessageRequest:
        if not (self.message or "").strip() and not self.images:
            raise ValueError("message 与 images 至少提供一项")
        if self.final_intent is not None and not self.decision_id:
            raise ValueError("final_intent requires decision_id")
        if self.selected_skill is not None:
            lines = (self.message or "").splitlines()
            first_line = lines[0] if lines else ""
            if first_line != f"/skill:{self.selected_skill}":
                raise ValueError("selected_skill must match the first message line")
        return self


class ChatSessionBoundaryReferences(BaseModel):
    """The reference ids a boundary-checked turn carries.

    These are pointers, not history: a new session can start with exactly the
    same set, so they never exempt a request from the boundary advice. They are
    bound into the decision digest, so the final request must carry the same set.
    """

    space_ids: list[str] = Field(default_factory=list, max_length=64)
    document_ids: list[str] = Field(default_factory=list, max_length=200)
    session_reference_ids: list[str] = Field(default_factory=list, max_length=64)
    writing_publication_ids: list[str] = Field(default_factory=list, max_length=64)
    writing_context: ChatWritingContext | None = None


class ChatSessionBoundaryCheckRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    client_message_id: str = Field(min_length=1, max_length=64)
    client_assistant_message_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=160)
    # 只有真正无法迁移到新会话的上下文才豁免：本轮图片、绑定到本会话的临时上传，
    # 以及显式 skill。资料/会话/写作引用是指针，随新会话一起带走即可。
    has_attachments: bool = False
    has_explicit_skill: bool = False
    references: ChatSessionBoundaryReferences = Field(
        default_factory=ChatSessionBoundaryReferences
    )
    # "handoff" 是用户在受控停止卡片上主动要求交接，跳过话题启发式直接发一次性
    # decision；正常发送用 "auto"，由服务端判定。
    requested_target: Literal["auto", "handoff"] = "auto"


class ChatSessionBoundaryCheckResponse(BaseModel):
    recommendation: Literal["stay", "confirm_new"]
    reason_code: Literal[
        "below_usage_threshold",
        "explicit_context",
        "writing_session",
        "continuation_signal",
        "insufficient_topic_signal",
        "related_topic",
        "large_session_unrelated",
        "user_requested_handoff",
    ]
    session_input_tokens: int = Field(default=0, ge=0)
    session_context_window: int | None = Field(default=None, ge=1)
    threshold_tokens: int = Field(default=0, ge=0)
    decision_id: str | None = None


class ChatSessionBoundaryConfirmRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    client_message_id: str = Field(min_length=1, max_length=64)
    client_assistant_message_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=160)
    references: ChatSessionBoundaryReferences = Field(
        default_factory=ChatSessionBoundaryReferences
    )
    # handoff 与 new 一样开新会话，区别是首轮携带来源会话的交接上下文与工作区。
    target: Literal["current", "new", "handoff"]


class ChatUsageCall(BaseModel):
    """One provider call contributing to a chat turn's aggregate usage."""

    purpose: Literal["answer_generation", "vision_parse"]
    provider: str = ""
    model: str | None = None
    endpoint_id: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    input_uncached_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost: str = "0"
    latency_ms: int = Field(default=0, ge=0)
    status: Literal["completed", "failed"] = "completed"
    error_code: str | None = None
    price_version_id: str | None = None
    currency: str | None = None


class ChatMessageUsage(BaseModel):
    provider: str = "agent_chat"
    model: str | None = None
    answer_model: str | None = None
    vision_model: str | None = None
    status: Literal["completed", "failed"] = "completed"
    error_code: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    context_input_tokens: int | None = Field(default=None, ge=0)
    context_window: int | None = Field(default=None, ge=1)
    input_uncached_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost: str = "0"
    latency_ms: int = Field(default=0, ge=0)
    token_warning: bool = False
    token_warning_threshold: int | None = Field(default=None, ge=1)
    token_warning_observed_tokens: int | None = Field(default=None, ge=0)
    route_mode: Literal["direct", "vision_fallback", "ocr_assisted"] | None = None
    vision_fallback_used: bool = False
    calls: list[ChatUsageCall] = Field(default_factory=list, max_length=8)


class ChatStreamPart(BaseModel):
    type: Literal["thinking", "content", "tool", "error"]
    text: str = Field(default="", max_length=200_000)
    tool_call_id: str | None = Field(
        default=None,
        max_length=128,
        validation_alias=AliasChoices("tool_call_id", "toolCallId"),
    )
    tool_name: str | None = Field(
        default=None,
        max_length=128,
        validation_alias=AliasChoices("tool_name", "toolName"),
    )
    status: Literal["running", "done", "error"] | None = None
    args_text: str | None = Field(
        default=None,
        max_length=100_000,
        validation_alias=AliasChoices("args_text", "argsText"),
    )
    output_text: str | None = Field(
        default=None,
        max_length=200_000,
        validation_alias=AliasChoices("output_text", "outputText"),
    )
    error_id: str | None = Field(
        default=None,
        max_length=128,
        validation_alias=AliasChoices("error_id", "errorId"),
    )
    error_kind: Literal["model", "runtime", "extension"] | None = Field(
        default=None,
        validation_alias=AliasChoices("error_kind", "errorKind"),
    )
    metadata_text: str | None = Field(
        default=None,
        max_length=100_000,
        validation_alias=AliasChoices("metadata_text", "metadataText"),
    )
    error_code: str | None = Field(
        default=None,
        max_length=128,
        validation_alias=AliasChoices("error_code", "errorCode"),
    )
    retryable: bool | None = None
    progress_preserved: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("progress_preserved", "progressPreserved"),
    )
    retry_after_seconds: int | None = Field(
        default=None,
        ge=1,
        le=3_600,
        validation_alias=AliasChoices("retry_after_seconds", "retryAfterSeconds"),
    )
    auto_retry_scheduled: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "auto_retry_scheduled",
            "autoRetryScheduled",
        ),
    )

    @model_validator(mode="after")
    def _validate_part_shape(self) -> ChatStreamPart:
        if self.type in ("thinking", "content"):
            if not self.text.strip():
                raise ValueError("thinking/content parts require non-empty text")
            return self
        if self.type == "error":
            if not (self.error_id and self.error_kind and self.text.strip()):
                raise ValueError(
                    "error parts require error_id, error_kind, and non-empty text"
                )
            return self
        if not (self.tool_call_id and self.tool_name and self.status):
            raise ValueError("tool parts require tool_call_id, tool_name, and status")
        return self

    @model_serializer
    def _serialize(self) -> dict[str, object]:
        if self.type in ("thinking", "content"):
            return {"type": self.type, "text": self.text}
        if self.type == "error":
            payload: dict[str, object] = {
                "type": "error",
                "error_id": self.error_id,
                "error_kind": self.error_kind,
                "text": self.text,
            }
            if self.metadata_text:
                payload["metadata_text"] = self.metadata_text
            if self.error_code:
                payload["error_code"] = self.error_code
            if self.retryable is not None:
                payload["retryable"] = self.retryable
            if self.progress_preserved is not None:
                payload["progress_preserved"] = self.progress_preserved
            if self.retry_after_seconds is not None:
                payload["retry_after_seconds"] = self.retry_after_seconds
            if self.auto_retry_scheduled is not None:
                payload["auto_retry_scheduled"] = self.auto_retry_scheduled
            return payload
        payload = {
            "type": "tool",
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "text": self.text or "",
        }
        if self.args_text:
            payload["args_text"] = self.args_text
        if self.output_text:
            payload["output_text"] = self.output_text
        return payload


class ChatFilePart(BaseModel):
    type: Literal["file"]
    name: str = Field(min_length=1, max_length=255)
    href: str = Field(pattern=r"^/api/files/", max_length=2048)
    mime: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0, le=128 * 1024 * 1024)


class ChatMessagePayload(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(default="", max_length=200_000)
    thinking: str | None = Field(default=None, max_length=200_000)
    parts: list[ChatStreamPart | ChatFilePart] = Field(default_factory=list, max_length=200)
    space_ids: list[str] = Field(default_factory=list, max_length=20)
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    references: list[dict] = Field(default_factory=list, max_length=2000)
    session_references: list[dict] = Field(default_factory=list, max_length=10)
    writing_references: list[dict] = Field(default_factory=list, max_length=10)
    images: list[ChatImageContent] = Field(default_factory=list, max_length=4)
    retrieval_summary: str | None = Field(default=None, max_length=4000)
    retrieval_trace: RetrievalTraceResponse | None = None
    writing_project_id: str | None = Field(default=None, max_length=64)
    writing_context: ChatWritingContext | None = None
    usage: ChatMessageUsage | None = None
    created_at: str | None = None


class ChatMessageVariant(BaseModel):
    """Position of this answer inside its regenerate history.

    ``ids`` is ordered from the original answer to the newest one so the client
    can step backwards and forwards without another round trip.
    """

    group_id: str
    index: int
    total: int
    active: bool
    ids: list[str] = Field(default_factory=list)


class ChatMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    thinking: str | None = None
    parts: list[ChatStreamPart | ChatFilePart] = Field(default_factory=list)
    space_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    references: list[dict] = Field(default_factory=list)
    citation_statuses: dict[str, Literal["unlinked", "unavailable"]] = Field(default_factory=dict)
    session_references: list[dict] = Field(default_factory=list)
    writing_references: list[dict] = Field(default_factory=list)
    images: list[ChatHistoryImageContent] = Field(default_factory=list)
    retrieval_summary: str | None = None
    writing_project_id: str | None = None
    writing_context: ChatWritingContext | None = None
    template_revision_id: str | None = None
    usage: ChatMessageUsage | None = None
    retrieval_trace: RetrievalTraceResponse | None = None
    dispatch_decision: MediaDispatchDecisionProjection | None = None
    variant: ChatMessageVariant | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ChatSessionCreateRequest(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=255)
    category_id: str | None = Field(default=None, max_length=64)
    agent_session_id: str | None = Field(default=None, max_length=128)
    preferred_model: str | None = Field(default=None, max_length=160)


class ChatSessionForkRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)


class ChatSessionUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    category_id: str | None = Field(default=None, max_length=64)
    clear_category: bool = False
    pinned: bool | None = None
    archived: bool | None = None
    agent_session_id: str | None = Field(default=None, max_length=128)
    clear_agent_session: bool = False
    writing_project_id: str | None = Field(default=None, max_length=64)
    preferred_model: str | None = Field(default=None, max_length=160)


class ChatSessionBatchDeleteRequest(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("session_ids")
    @classmethod
    def normalize_session_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_session_id in value:
            session_id = raw_session_id.strip()
            if not session_id:
                raise ValueError("session_ids 不能包含空值")
            if len(session_id) > 64:
                raise ValueError("session_id 长度不能超过 64")
            if session_id in seen:
                continue
            seen.add(session_id)
            normalized.append(session_id)
        if not normalized:
            raise ValueError("session_ids 至少包含一个会话")
        return normalized


class ChatSessionBatchDeleteFailure(BaseModel):
    session_id: str
    code: Literal["not_found", "session_busy", "delete_failed"]
    detail: str


class ChatSessionBatchDeleteResponse(BaseModel):
    deleted_ids: list[str] = Field(default_factory=list)
    failed: list[ChatSessionBatchDeleteFailure] = Field(default_factory=list)


class ChatSessionSummaryResponse(BaseModel):
    id: str
    title: str
    messages_revision: int = Field(default=0, ge=0)
    agent_session_id: str | None = None
    preferred_model: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    pinned: bool = False
    pinned_at: str | None = None
    archived: bool = False
    archived_at: str | None = None
    writing_project_id: str | None = None
    message_count: int = 0
    created_at: str
    updated_at: str


class ChatSessionDetailResponse(ChatSessionSummaryResponse):
    messages: list[ChatMessageResponse] = Field(default_factory=list)
    has_more_messages: bool = False
    next_before_message_id: str | None = None


class ChatSessionBoundaryConfirmResponse(BaseModel):
    decision_id: str
    target: Literal["current", "new", "handoff"]
    session: ChatSessionDetailResponse


class ChatSessionPublishRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=4000)
    share_scope: Literal["space", "all_internal"] | None = None
    allow_cite: bool = False


class ChatSessionPublicationResponse(BaseModel):
    id: str
    source_session_id: str
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
    message_count: int
    image_count: int = Field(default=0, ge=0)
    can_revoke: bool = False
    published_at: str
    revoked_at: str | None = None


class ChatSessionPublicationDetailResponse(ChatSessionPublicationResponse):
    messages: list[dict] = Field(default_factory=list)


class ChatAppendMessagesRequest(BaseModel):
    messages: list[ChatMessagePayload] = Field(min_length=1, max_length=200)
    base_revision: int | None = Field(default=None, ge=0)
    agent_session_id: str | None = Field(default=None, max_length=128)
    clear_agent_session: bool = False
    title: str | None = Field(default=None, min_length=1, max_length=255)
    writing_project_id: str | None = Field(default=None, max_length=64)
    preferred_model: str | None = Field(default=None, max_length=160)
    replace: bool = False
    snapshot_complete: bool = False


class ChatSkillInfo(BaseModel):
    name: str
    description: str = ""
    path: str | None = None
    label: str | None = None
    group: str | None = None
    skill_id: str | None = None
    version: str | None = None
    model: str | None = None
    approved: bool = False
    enabled: bool = True
    disabled_reason: str | None = None


class MediaCapability(BaseModel):
    enabled: bool = False
    reason: str | None = None


class MediaCapabilitiesResponse(BaseModel):
    asr: MediaCapability = Field(default_factory=MediaCapability)
    image_generation: MediaCapability = Field(default_factory=MediaCapability)
    video_generation: MediaCapability = Field(default_factory=MediaCapability)


class ModelOption(BaseModel):
    id: str
    provider: str
    model: str
    label: str | None = None
    supports_vision: bool = False


class ChatImageLimits(BaseModel):
    max_bytes: int
    max_images: int


class ChatCapabilitiesResponse(BaseModel):
    image_limits: ChatImageLimits
    configured: bool
    agent_id: str | None = None
    default_model: str | None = None
    available_models: list[str] = Field(default_factory=list)
    model_options: list[ModelOption] = Field(default_factory=list)
    enable_skills: bool = False
    tools: list[str] = Field(default_factory=list)
    skills: list[ChatSkillInfo] = Field(default_factory=list)
    media_capabilities: MediaCapabilitiesResponse = Field(
        default_factory=MediaCapabilitiesResponse
    )
    runtime_type: str | None = None


class ChatSteerRequest(BaseModel):
    message: str = Field(default="", max_length=32_000)
    images: list[ChatImageContent] = Field(default_factory=list, max_length=4)
    agent_session_id: str | None = Field(
        default=None,
        max_length=128,
        description="可选：SSE session 事件刚下发时，前端内存中的上游 session id",
    )
    client_message_id: str | None = Field(default=None, max_length=64)
    client_assistant_message_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _require_message_or_images(self) -> ChatSteerRequest:
        if not (self.message or "").strip() and not self.images:
            raise ValueError("message 与 images 至少提供一项")
        return self


class ChatSteerResponse(BaseModel):
    session_id: str
    queued: bool = True
    server_persisted: bool = False
    messages_revision: int | None = Field(default=None, ge=0)
    mode: Literal["steer", "follow_up"]
