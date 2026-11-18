"""Strict contracts for durable personal memory.

Every request model is ``extra="forbid"``: a client or a model may never smuggle
in ``owner_user_id``, ``source_hash``, ``turn_id`` or an ``approved`` flag. The
key whitelist below is the whole writable surface; there is no free-form field
and no writable ``scope`` parameter, because the current scope is fixed to the
individual employee.

Text that reaches memory stays data. Storing a value never grants it
system/tool authority, and an input containing "ignore the previous rules"
changes neither the whitelist nor the owner check.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Error codes. The API maps these onto 403/404/409/422/503; a code never leaks
# whether another employee's resource exists.
# ---------------------------------------------------------------------------

MemoryErrorCode = Literal[
    "memory_not_found",
    "memory_forbidden",
    "memory_version_conflict",
    "memory_revision_conflict",
    "memory_request_conflict",
    "memory_key_not_allowed",
    "memory_value_invalid",
    "memory_limit_reached",
    "memory_source_invalid",
    "memory_source_too_large",
    "memory_turn_not_live",
    "memory_learning_disabled",
    "memory_unavailable",
    "memory_budget_exceeded",
    "memory_budget_configuration_invalid",
    "memory_sensitive_source",
    "memory_endpoint_unavailable",
    "memory_context_limit",
    "memory_provider_timeout",
    "memory_provider_response_too_large",
    "memory_provider_temporary",
    "memory_provider_rejected",
    "memory_provider_network",
    "memory_extraction_invalid",
    "memory_evidence_invalid",
    "memory_extractor_version_changed",
    "memory_worker_failed",
    "memory_usage_unavailable",
    "memory_budget_unavailable",
    "memory_commit_failed",
    "memory_outcome_unknown",
    "memory_attempts_exhausted",
]

LearningMode = Literal["manual", "suggest", "auto_low_risk"]
EntryStatus = Literal[
    "candidate", "active", "rejected", "superseded", "forgotten", "expired"
]
EntryOrigin = Literal["manual", "explicit", "automatic"]
MemoryChannel = Literal["web", "open", "wecom", "manual"]
TurnMemoryStatus = Literal[
    "none", "pending", "active", "needs_confirmation", "unavailable"
]
EvidenceKind = Literal["user_statement", "manual_confirmation"]

# ---------------------------------------------------------------------------
# Bounds. Kept here so the repository, service, extractor and API agree.
# ---------------------------------------------------------------------------

MAX_ACTIVE_ENTRIES = 100
MAX_OPEN_CANDIDATES = 100
MAX_SOURCE_TEXT_CHARS = 12_000
SOURCE_TEXT_RETENTION_DAYS = 7
MAX_SNAPSHOT_ENTRIES = 20
MAX_SNAPSHOT_CHARS = 3_000
MAX_EVIDENCE_PER_ENTRY = 3
MAX_QUOTE_CHARS = 500

SUPPORTED_LANGUAGE_CODES = ("zh-CN", "zh-TW", "en", "ja")
RESPONSE_LENGTHS = ("concise", "normal", "detailed")
ANSWER_STRUCTURES = ("conclusion_first", "step_by_step", "normal")


def sanitize_memory_text(value: str) -> str:
    """Drop control characters and collapse surrounding whitespace.

    Applied to every stored value and every stored excerpt. Note that the
    sanitized form may differ from the original text, so a caller must never
    reuse original offsets against sanitized text (or the reverse).
    """

    cleaned = "".join(
        char
        for char in value
        if char == "\n" or unicodedata.category(char) not in {"Cc", "Cf", "Zl", "Zp"}
    )
    return cleaned.strip()


@dataclass(frozen=True)
class MemoryKeyPolicy:
    """The per-key data and privacy policy.

    ``auto_admissible`` marks the keys that ``auto_low_risk`` may activate on
    its own. Work descriptions stay suggestion-only whatever the mode, and a
    conflict or an inference is always left for the employee to confirm.
    """

    key: str
    value_kind: Literal["text", "enum"]
    max_length: int = 0
    choices: tuple[str, ...] = ()
    default_ttl_days: int | None = None
    auto_admissible: bool = False
    label: str = ""


MEMORY_KEY_POLICIES: dict[str, MemoryKeyPolicy] = {
    policy.key: policy
    for policy in (
        MemoryKeyPolicy(
            key="profile.self_reported_name",
            value_kind="text",
            max_length=80,
            auto_admissible=True,
            label="自述姓名",
        ),
        MemoryKeyPolicy(
            key="profile.preferred_name",
            value_kind="text",
            max_length=80,
            auto_admissible=True,
            label="希望的称呼",
        ),
        MemoryKeyPolicy(
            key="preference.language",
            value_kind="enum",
            choices=SUPPORTED_LANGUAGE_CODES,
            auto_admissible=True,
            label="语言偏好",
        ),
        MemoryKeyPolicy(
            key="preference.response_length",
            value_kind="enum",
            choices=RESPONSE_LENGTHS,
            auto_admissible=True,
            label="回答长度偏好",
        ),
        MemoryKeyPolicy(
            key="preference.answer_structure",
            value_kind="enum",
            choices=ANSWER_STRUCTURES,
            auto_admissible=True,
            label="回答组织偏好",
        ),
        MemoryKeyPolicy(
            key="work.role_description",
            value_kind="text",
            max_length=200,
            default_ttl_days=180,
            auto_admissible=False,
            label="工作描述",
        ),
        MemoryKeyPolicy(
            key="work.current_focus",
            value_kind="text",
            max_length=300,
            default_ttl_days=30,
            auto_admissible=False,
            label="近期关注",
        ),
    )
}

MEMORY_KEYS = tuple(MEMORY_KEY_POLICIES)

# Highest priority first. A this-turn addressing request outranks all of these
# and never writes long-term memory; ``User.display_name`` is the final
# fallback and is never overwritten by memory.
ADDRESSING_KEY_PRIORITY = (
    "profile.preferred_name",
    "profile.self_reported_name",
)


class MemoryValueError(ValueError):
    """A whitelisted key received a value its policy rejects."""

    def __init__(self, code: MemoryErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: MemoryErrorCode = code


def normalize_memory_value(key: str, value: Any) -> str:
    """Validate one whitelisted ``key``/``value`` pair and return safe text.

    Raises :class:`MemoryValueError` with ``memory_key_not_allowed`` for an
    unknown key and ``memory_value_invalid` for anything the policy rejects.
    """

    policy = MEMORY_KEY_POLICIES.get(key)
    if policy is None:
        raise MemoryValueError("memory_key_not_allowed", "key is not writable")
    if not isinstance(value, str):
        raise MemoryValueError("memory_value_invalid", "value must be a string")
    cleaned = sanitize_memory_text(value)
    if not cleaned:
        raise MemoryValueError("memory_value_invalid", "value must not be empty")
    if policy.value_kind == "enum":
        if cleaned not in policy.choices:
            raise MemoryValueError(
                "memory_value_invalid", "value is not an allowed option"
            )
        return cleaned
    if "\n" in cleaned:
        raise MemoryValueError("memory_value_invalid", "value must be a single line")
    if len(cleaned) > policy.max_length:
        raise MemoryValueError("memory_value_invalid", "value is too long")
    return cleaned


# ---------------------------------------------------------------------------
# Request contracts. None of these accept owner, source or approval fields.
# ---------------------------------------------------------------------------


class StrictModel(BaseModel):
    """Base for every memory contract: unknown fields are refused, not ignored.

    This is what stops a client or a model smuggling in ``owner_user_id``,
    ``source_hash``, ``turn_id`` or an ``approved`` flag.
    """

    model_config = ConfigDict(extra="forbid")


class MemorySettingsUpdateRequest(StrictModel):
    use_enabled: bool | None = None
    learning_mode: LearningMode | None = None
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128)


class MemoryCreateRequest(StrictModel):
    """An explicit save from the management UI. Becomes a manual source."""

    key: str = Field(max_length=64)
    value: str = Field(max_length=2_000)
    request_id: str = Field(min_length=1, max_length=128)

    @field_validator("key")
    @classmethod
    def _known_key(cls, value: str) -> str:
        if value not in MEMORY_KEY_POLICIES:
            raise ValueError("key is not writable")
        return value


class MemoryUpdateRequest(StrictModel):
    value: str = Field(max_length=2_000)
    expected_version: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)


class MemoryConfirmRequest(StrictModel):
    expected_version: int = Field(ge=1)
    # The active version the employee saw. Re-checked inside the activation
    # transaction so a concurrent correction is not silently overwritten.
    expected_active_version: int | None = Field(default=None, ge=1)
    request_id: str = Field(min_length=1, max_length=128)


class MemoryRejectRequest(StrictModel):
    expected_version: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)


class MemoryForgetRequest(StrictModel):
    expected_version: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)


class MemoryClearRequest(StrictModel):
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128)
    explicit_confirmation: Literal[True]


class MemoryProposalInput(StrictModel):
    """What the model may submit through ``propose_my_memory``.

    It carries a quote and offsets inside the server-bound current input only.
    Owner, turn and message identifiers are bound by the backend and are not
    accepted here.
    """

    key: str = Field(max_length=64)
    value: str = Field(max_length=2_000)
    quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)
    quote_start: int = Field(ge=0)
    quote_end: int = Field(ge=1)
    expected_active_version: int | None = Field(default=None, ge=1)
    self_attribution: Literal["self", "other", "uncertain"] = "uncertain"
    temporality: Literal["persistent", "current_turn", "temporary"] = "temporary"
    explicitness: Literal["explicit", "implied", "inferred"] = "inferred"
    conflict_note: str = Field(default="", max_length=500)

    @field_validator("key")
    @classmethod
    def _known_key(cls, value: str) -> str:
        if value not in MEMORY_KEY_POLICIES:
            raise ValueError("key is not writable")
        return value


class MemoryForgetProposalInput(StrictModel):
    """A forget target the employee asked for in conversation.

    It waits for UI confirmation by default; the model may never clear
    everything on its own, so there is no "all keys" option here.
    """

    key: str = Field(max_length=64)
    quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)
    quote_start: int = Field(ge=0)
    quote_end: int = Field(ge=1)

    @field_validator("key")
    @classmethod
    def _known_key(cls, value: str) -> str:
        if value not in MEMORY_KEY_POLICIES:
            raise ValueError("key is not writable")
        return value


# ---------------------------------------------------------------------------
# Response contracts.
# ---------------------------------------------------------------------------


class MemorySettingsResponse(StrictModel):
    use_enabled: bool
    learning_mode: LearningMode
    revision: int
    receipt: MemoryOperationReceipt | None = None


class MemoryEvidenceResponse(StrictModel):
    """The minimal source view. Never a stretch of the original conversation."""

    id: str
    turn_id: str
    channel: MemoryChannel
    evidence_kind: EvidenceKind
    quote: str | None = None
    created_at: datetime
    # True when the underlying chat/source snapshot is gone or expired; the UI
    # says so plainly instead of pretending the excerpt is still verifiable.
    source_available: bool = True


class MemoryEntryResponse(StrictModel):
    id: str
    key: str
    label: str = ""
    value: str | None = None
    status: EntryStatus
    origin: EntryOrigin
    version: int
    expected_active_version: int | None = None
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None
    expires_at: datetime | None = None
    evidence_count: int = 0


class MemoryListResponse(StrictModel):
    items: list[MemoryEntryResponse]
    revision: int
    next_cursor: str | None = None


class MemoryEvidenceListResponse(StrictModel):
    items: list[MemoryEvidenceResponse]
    next_cursor: str | None = None


class MemoryOperationReceipt(StrictModel):
    """The authoritative answer to "was it saved?".

    An assistant sentence is never this receipt, and a replayed request reads
    the stored receipt back rather than repeating the mutation.
    """

    request_id: str
    kind: str
    status: Literal["pending", "succeeded", "failed"]
    memory_id: str | None = None
    memory_key: str | None = None
    memory_version: int | None = None
    memory_label: str | None = None
    revision: int
    error_code: MemoryErrorCode | None = None
    # True when a previously successful receipt now points at a forgotten fact.
    revoked: bool = False


class MemoryMutationResponse(StrictModel):
    entry: MemoryEntryResponse | None = None
    receipt: MemoryOperationReceipt
    revision: int


class MemoryTurnStatusResponse(StrictModel):
    """What the chat UI reads back for one turn - never guessed from text."""

    turn_id: str
    session_id: str | None = None
    memory_status: TurnMemoryStatus
    revision: int
    candidates: list[MemoryEntryResponse] = Field(default_factory=list)
    receipts: list[MemoryOperationReceipt] = Field(default_factory=list)


class MemoryTurnListResponse(StrictModel):
    items: list[MemoryTurnStatusResponse]
    revision: int


class MemorySnapshotEntry(StrictModel):
    key: str
    value: str
    version: int
    expires_at: datetime | None = None
    evidence_id: str | None = None


class MemorySnapshot(StrictModel):
    """The bounded block injected as temporary Pi context before a model call.

    ``valid_until`` exists because an unchanged ``revision`` is not enough: an
    entry can expire on its own, so a client may never cache on revision alone.
    """

    owner_user_id: str
    revision: int
    epoch: int
    use_enabled: bool
    addressing_name: str | None = None
    entries: list[MemorySnapshotEntry] = Field(default_factory=list)
    valid_until: datetime | None = None
