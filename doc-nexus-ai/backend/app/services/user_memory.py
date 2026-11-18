"""Policy for durable personal memory.

The repository owns the state machine; this module owns the judgement calls
around it:

* which source text a turn actually refers to, and whether a model-supplied
  excerpt really occurs in it;
* whether a proposal may be saved at all - key whitelist, secret screening,
  self-attribution, temporality, and the forget barrier;
* whether the employee's ``learning_mode`` lets a candidate activate itself or
  leaves it waiting for confirmation;
* what the bounded snapshot handed to the model contains, and which name the
  assistant should use.

Nothing here trusts what the model reports about its own confidence or
approval. A model may say a candidate is certain and pre-approved; that is
diagnostic text, not authorisation.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import as_utc, now
from app.models.auth import User
from app.models.chat import ChatMessage
from app.models.user_memory import (
    UserMemoryEntry,
    UserMemoryJob,
    UserMemoryOperation,
    UserMemoryState,
    UserMemoryTurn,
)
from app.repositories import user_memory as repo
from app.schemas.user_memory import (
    ADDRESSING_KEY_PRIORITY,
    MEMORY_KEY_POLICIES,
    MemoryEntryResponse,
    MemoryErrorCode,
    MemoryOperationReceipt,
    MemoryProposalInput,
    MemorySnapshot,
    MemorySnapshotEntry,
    MemoryTurnStatusResponse,
    MemoryValueError,
    normalize_memory_value,
    sanitize_memory_text,
)

logger = logging.getLogger(__name__)


class MemoryPolicyError(Exception):
    """A proposal or request was refused by policy, with a stable code."""

    def __init__(self, code: MemoryErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: MemoryErrorCode = code


# ---------------------------------------------------------------------------
# Secret screening.
# ---------------------------------------------------------------------------

# Deliberately conservative. A false positive costs one refused candidate; a
# false negative writes a credential into memory and into its audit trail.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:sk|pk|rk|api|key|token|secret|pat|ghp|xox[baprs])[-_][A-Za-z0-9_-]{12,}",
        re.I,
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(r"\b[0-9A-Fa-f]{32,}\b"),  # long hex: hashes, raw keys
    re.compile(r"\b\d{15,19}\b"),  # bank card / long account numbers
    re.compile(r"\b\d{17}[\dXx]\b"),  # mainland China resident ID
    re.compile(r"(?:密码|口令|密钥|令牌|password|passwd|passphrase)\s*[:：=]", re.I),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def contains_secret(text: str) -> bool:
    """True when the text looks like it carries a credential or an ID number.

    Used to refuse a candidate outright. Neither the value nor the surrounding
    original text is written to the entry or to the operation audit when this
    fires, so a rejection leaves no copy of the secret behind.
    """

    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def entry_response(db: Session, entry: UserMemoryEntry) -> MemoryEntryResponse:
    policy = MEMORY_KEY_POLICIES.get(entry.key)
    return MemoryEntryResponse(
        id=entry.id,
        key=entry.key,
        label=policy.label if policy else "",
        value=_decode(entry),
        status=entry.status,  # type: ignore[arg-type]
        origin=entry.origin,  # type: ignore[arg-type]
        version=entry.version,
        expected_active_version=entry.expected_active_version,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        confirmed_at=entry.confirmed_at,
        expires_at=entry.expires_at,
        evidence_count=len(
            repo.list_entry_evidence(
                db, owner_user_id=entry.owner_user_id, memory_id=entry.id, limit=10
            )
        ),
    )


def operation_receipt(
    db: Session, operation: UserMemoryOperation, *, revision: int
) -> MemoryOperationReceipt:
    target = (
        db.scalar(
            select(UserMemoryEntry).where(
                UserMemoryEntry.owner_user_id == operation.owner_user_id,
                UserMemoryEntry.id == operation.result_memory_id,
            )
        )
        if operation.result_memory_id
        else None
    )
    revoked = repo.receipt_is_revoked(db, operation)
    if operation.kind == "forget_proposal":
        # A proposal to forget must not silently retarget a corrected value.
        revoked = revoked or target is None or target.status != "active"
        if target is not None:
            revoked = revoked or (
                operation.completed_at is not None
                and as_utc(target.updated_at) > as_utc(operation.completed_at)
            )
    policy = MEMORY_KEY_POLICIES.get(target.key) if target and not revoked else None
    return MemoryOperationReceipt(
        request_id=operation.request_id,
        kind=operation.kind,
        status=operation.status,  # type: ignore[arg-type]
        memory_id=operation.result_memory_id,
        memory_key=target.key if target and not revoked else None,
        memory_version=target.version if target and not revoked else None,
        memory_label=policy.label if policy else None,
        revision=operation.result_revision or revision,
        error_code=operation.error_code,  # type: ignore[arg-type]
        revoked=revoked,
    )


def read_turn_status(
    db: Session, *, owner_user_id: str, turn_id: str
) -> MemoryTurnStatusResponse:
    """Read committed state shared by Web, Open API and bound private channels."""
    repo.reconcile_equivalent_candidates(db, owner_user_id=owner_user_id)
    turn, entries, operations = repo.get_turn_status(
        db, owner_user_id=owner_user_id, turn_id=turn_id
    )
    state = repo.get_or_create_state(db, owner_user_id)
    moment = now()
    current_entries = [
        entry
        for entry in entries
        if entry.status in {"active", "candidate"}
        and (entry.expires_at is None or as_utc(entry.expires_at) > moment)
    ]
    receipts = [
        operation_receipt(db, operation, revision=state.revision)
        for operation in operations
    ]
    job_states = set(
        db.scalars(
            select(UserMemoryJob.status).where(
                UserMemoryJob.owner_user_id == owner_user_id,
                UserMemoryJob.turn_id == turn.id,
            )
        )
    )
    memory_status = "none"
    if any(entry.status == "candidate" for entry in current_entries) or any(
        receipt.kind == "forget_proposal"
        and not receipt.revoked
        and receipt.memory_id
        and receipt.status == "succeeded"
        for receipt in receipts
    ):
        memory_status = "needs_confirmation"
    elif turn.status != "invalidated" and (
        job_states.intersection({"pending", "running"})
        or any(receipt.status == "pending" for receipt in receipts)
    ):
        memory_status = "pending"
    elif any(entry.status == "active" for entry in current_entries):
        memory_status = "active"
    elif "failed" in job_states or any(
        receipt.status == "failed" and not receipt.revoked for receipt in receipts
    ):
        memory_status = "unavailable"
    return MemoryTurnStatusResponse(
        turn_id=turn.id,
        session_id=turn.chat_session_id,
        memory_status=memory_status,  # type: ignore[arg-type]
        revision=state.revision,
        candidates=[entry_response(db, entry) for entry in current_entries],
        receipts=receipts,
    )


# ---------------------------------------------------------------------------
# Source text and evidence verification.
# ---------------------------------------------------------------------------


def resolve_turn_source_text(db: Session, turn: UserMemoryTurn) -> str | None:
    """The text this turn was registered over, re-read from its live source.

    A web turn keeps only a pointer, so the current message content is read
    back and re-hashed rather than trusted from cached state: if the employee
    edited the message, the turn is no longer a valid source for anything.
    """

    if turn.status == "invalidated":
        return None
    if turn.chat_message_id:
        message = db.get(ChatMessage, turn.chat_message_id)
        if (
            message is None
            or message.owner_user_id != turn.owner_user_id
            or message.role != "user"
            or (
                turn.chat_session_id is not None
                and message.session_id != turn.chat_session_id
            )
        ):
            return None
        if repo.source_hash(message.content) != turn.source_hash:
            return None
        return message.content
    if turn.source_text_safe is None:
        return None
    if turn.source_text_expires_at and as_utc(turn.source_text_expires_at) <= now():
        return None
    if repo.source_hash(turn.source_text_safe) != turn.source_hash:
        return None
    return turn.source_text_safe


def verify_quote(
    source_text: str, *, quote: str, quote_start: int, quote_end: int
) -> str:
    """Check a model-supplied excerpt against the server's own copy.

    Offsets are Unicode code point positions in this exact text. The excerpt
    must occur where the model says it does - "the words appear somewhere in
    the message" is not evidence that the employee said them in that sense.
    """

    if quote_end <= quote_start:
        raise MemoryPolicyError("memory_source_invalid", "empty evidence span")
    if quote_end > len(source_text):
        raise MemoryPolicyError(
            "memory_source_invalid", "evidence span is out of range"
        )
    actual = source_text[quote_start:quote_end]
    if actual != quote:
        raise MemoryPolicyError(
            "memory_source_invalid", "evidence span does not match the source"
        )
    return actual


# ---------------------------------------------------------------------------
# Proposal validation.
# ---------------------------------------------------------------------------

_NEGATION_MARKERS = (
    "不是",
    "别叫",
    "不要叫",
    "不叫",
    "以前叫",
    "曾经叫",
)
_QUOTATION_MARKERS = ("“", "”", "「", "」", "‘", "’", '"', "'", "《", "》")


def _looks_like_reported_speech(source_text: str, span_start: int) -> bool:
    """Cheap guard against a quoted or ghost-written sentence.

    This deliberately rejects ambiguous reported speech; the employee can save
    the value through the explicit management form instead.
    """

    window = source_text[max(0, span_start - 24) : span_start]
    if any(marker in window for marker in _QUOTATION_MARKERS):
        return True
    return any(
        marker in window for marker in ("帮我写", "代写", "扮演", "假设", "如果")
    )


def validate_memory_proposal(
    db: Session,
    *,
    owner_user_id: str,
    turn: UserMemoryTurn,
    proposal: MemoryProposalInput,
) -> tuple[str, str]:
    """Validate one model proposal against the server-bound current input.

    Returns ``(normalized_value, verified_quote)``. Raises
    :class:`MemoryPolicyError` for anything refused. The model never supplies
    the owner, the turn or the message: those are bound by the backend before
    this is called, so a forged identifier cannot reach here at all.
    """

    if turn.owner_user_id != owner_user_id:  # pragma: no cover - caller binds it
        raise MemoryPolicyError("memory_forbidden", "turn belongs to another owner")

    source_text = resolve_turn_source_text(db, turn)
    if source_text is None:
        raise MemoryPolicyError("memory_source_invalid", "source is no longer valid")
    if len(source_text) > settings.personal_memory_source_text_max_chars:
        # Never silently truncate and then claim the whole input was analysed.
        raise MemoryPolicyError(
            "memory_source_too_large", "source exceeds the evidence limit"
        )

    verified = verify_quote(
        source_text,
        quote=proposal.quote,
        quote_start=proposal.quote_start,
        quote_end=proposal.quote_end,
    )
    if _looks_like_reported_speech(
        source_text, proposal.quote_start + max(0, verified.find(proposal.value))
    ):
        raise MemoryPolicyError(
            "memory_value_invalid", "reported speech is not a personal statement"
        )

    # A secret in either the value or its evidence stops the candidate here, so
    # neither the entry nor the receipt ever holds a copy of it.
    if contains_secret(proposal.value) or contains_secret(verified):
        raise MemoryPolicyError(
            "memory_value_invalid", "value contains secret material"
        )

    try:
        value = normalize_memory_value(proposal.key, proposal.value)
    except MemoryValueError as exc:
        raise MemoryPolicyError(exc.code, str(exc)) from exc
    if proposal.key in ADDRESSING_KEY_PRIORITY:
        if value not in verified or re.search(
            rf"(?:{'|'.join(_NEGATION_MARKERS)})\s*(?:叫|名叫)?\s*{re.escape(value)}",
            verified,
        ):
            raise MemoryPolicyError(
                "memory_value_invalid", "name is not positively stated in the evidence"
            )

    # Only the employee's own persistent statements are facts about them.
    if proposal.self_attribution != "self":
        raise MemoryPolicyError(
            "memory_value_invalid", "only first-person statements become memory"
        )
    if proposal.temporality != "persistent":
        raise MemoryPolicyError(
            "memory_value_invalid", "this-turn instructions do not become memory"
        )
    if any(marker in source_text for marker in _NEGATION_MARKERS) and (
        proposal.explicitness != "explicit"
    ):
        raise MemoryPolicyError(
            "memory_value_invalid", "negated or corrected statement needs confirmation"
        )

    cutoff = repo.automatic_cutoff_for_key(
        db, owner_user_id=owner_user_id, key=proposal.key
    )
    if cutoff is not None and as_utc(turn.created_at) <= cutoff:
        # The employee forgot this key. Re-saving needs input from after the
        # barrier, not a re-reading of the conversation that first taught it.
        raise MemoryPolicyError(
            "memory_source_invalid", "source predates a forget barrier for this key"
        )
    return value, sanitize_memory_text(verified)


_AUTO_PREFERENCE_EVIDENCE = {
    "preference.language": {
        "zh-CN": r"简体|中文|chinese",
        "zh-TW": r"繁[体體]",
        "en": r"英语|英文|english",
        "ja": r"日语|日文|日本語|japanese",
    },
    "preference.response_length": {
        "concise": r"简短|简洁|精简|concise|brief|short",
        "normal": r"适中|正常长度|normal length",
        "detailed": r"详细|详尽|细致|detailed|in.depth",
    },
    "preference.answer_structure": {
        "conclusion_first": r"先.{0,4}结论|结论.{0,4}前|conclusion first",
        "step_by_step": r"分步|步骤|一步.{0,2}步|step.by.step",
        "normal": r"正常结构|普通结构|normal structure",
    },
}


def apply_memory_policy(
    state: UserMemoryState,
    *,
    proposal: MemoryProposalInput,
    has_conflicting_active: bool,
) -> bool:
    """Decide whether a validated candidate may activate itself.

    Returns True only for the narrow ``auto_low_risk`` case the employee turned
    on in advance. Everything else waits for a confirmation the person actually
    gives, because "the model was sure" is not consent.
    """

    if not state.use_enabled or state.learning_mode != "auto_low_risk":
        return False
    policy = MEMORY_KEY_POLICIES.get(proposal.key)
    if policy is None or not policy.auto_admissible:
        return False
    if (
        proposal.explicitness != "explicit"
        or proposal.self_attribution != "self"
        or proposal.temporality != "persistent"
        or proposal.conflict_note
    ):
        # An inference stays a suggestion however often it recurs.
        return False
    if has_conflicting_active:
        # A conflict keeps the old value and shows the new one; the employee
        # decides which of two contradictory statements about them stands.
        return False
    quote = proposal.quote
    if re.search(
        r"这次|这轮|本轮|今天|暂时|临时|this (?:time|turn)|just for now", quote, re.I
    ):
        return False
    if proposal.key in ADDRESSING_KEY_PRIORITY:
        cue = (
            r"(?:叫我|称呼我|call me)\s*"
            if proposal.key == "profile.preferred_name"
            else r"(?:我叫|我的(?:名字|姓名)是|my name is|I am)\s*"
        )
        return re.search(cue + re.escape(proposal.value), quote, re.I) is not None
    patterns = _AUTO_PREFERENCE_EVIDENCE.get(proposal.key)
    if patterns is not None:
        pattern = patterns.get(proposal.value)
        return bool(
            pattern
            and re.search(r"我|请|回答|回复|prefer|answer|respond", quote, re.I)
            and re.search(pattern, quote, re.I)
        )
    return True


def default_expiry(key: str, *, at: datetime | None = None) -> datetime | None:
    policy = MEMORY_KEY_POLICIES.get(key)
    if policy is None or policy.default_ttl_days is None:
        return None
    return (at or now()) + timedelta(days=policy.default_ttl_days)


# ---------------------------------------------------------------------------
# Snapshot and addressing.
# ---------------------------------------------------------------------------


def _decode(entry: UserMemoryEntry) -> str | None:
    if entry.value_json is None:
        return None
    try:
        value = json.loads(entry.value_json)
    except ValueError:  # pragma: no cover - values are written as JSON
        return None
    return value if isinstance(value, str) else None


def resolve_addressing_name(
    db: Session, *, owner_user_id: str, user: User | None = None
) -> str | None:
    """The name the assistant should use, by fixed priority.

    preferred_name > self_reported_name > ``User.display_name``. A this-turn
    request ("call me 李工 just now") outranks all of these but is handled by
    the caller and never written here. Memory never overwrites the account
    profile in the other direction either.
    """

    state = db.get(UserMemoryState, owner_user_id)
    if state is not None and state.use_enabled:
        for key in ADDRESSING_KEY_PRIORITY:
            entry = repo.get_active_entry(db, owner_user_id=owner_user_id, key=key)
            if entry is None:
                continue
            if entry.expires_at is not None and as_utc(entry.expires_at) <= now():
                continue
            value = _decode(entry)
            if value:
                return value
    if user is not None and isinstance(getattr(user, "display_name", None), str):
        return user.display_name or None
    return None


def build_memory_snapshot(
    db: Session, *, owner_user_id: str, user: User | None = None
) -> MemorySnapshot:
    """The bounded block injected as temporary context before a model call.

    Bounded twice over - by entry count and by characters - and ordered so that
    addressing and language survive a tight budget. ``valid_until`` carries the
    earliest expiry, because an unchanged ``revision`` is not enough to keep a
    cached snapshot: an entry can lapse on its own between two calls.
    """

    state = repo.get_or_create_state(db, owner_user_id)
    snapshot = MemorySnapshot(
        owner_user_id=owner_user_id,
        revision=state.revision,
        epoch=state.epoch,
        use_enabled=state.use_enabled and settings.personal_memory_enabled,
        addressing_name=None,
        entries=[],
        valid_until=None,
    )
    if not state.use_enabled or not settings.personal_memory_enabled:
        # No active memory means no extra personal data is exposed at all.
        return snapshot

    moment = now()
    active = repo.list_active_entries(db, owner_user_id=owner_user_id, at=moment)

    # Core keys first so a tight budget keeps addressing and language.
    priority = {key: index for index, key in enumerate(ADDRESSING_KEY_PRIORITY)}
    ordered = sorted(
        active,
        key=lambda item: (priority.get(item.key, len(priority) + 1), item.key),
    )

    max_entries = settings.personal_memory_snapshot_max_entries
    max_chars = settings.personal_memory_snapshot_max_chars
    used = 0
    earliest_expiry: datetime | None = None
    for entry in ordered:
        if len(snapshot.entries) >= max_entries:
            break
        value = _decode(entry)
        if not value:
            continue
        cost = len(entry.key) + len(value)
        if used + cost > max_chars:
            continue
        used += cost
        evidence = repo.list_entry_evidence(
            db, owner_user_id=owner_user_id, memory_id=entry.id, limit=1
        )
        snapshot.entries.append(
            MemorySnapshotEntry(
                key=entry.key,
                value=value,
                version=entry.version,
                expires_at=as_utc(entry.expires_at) if entry.expires_at else None,
                # A minimal source identifier only. The snapshot never carries
                # an excerpt or a stretch of conversation back to the model.
                evidence_id=evidence[0][0].id if evidence else None,
            )
        )
        if entry.expires_at is not None:
            expires_at = as_utc(entry.expires_at)
            earliest_expiry = (
                expires_at
                if earliest_expiry is None
                else min(earliest_expiry, expires_at)
            )

    snapshot.valid_until = earliest_expiry
    snapshot.addressing_name = resolve_addressing_name(
        db, owner_user_id=owner_user_id, user=user
    )
    return snapshot


def snapshot_is_current(
    db: Session,
    *,
    owner_user_id: str,
    revision: int,
    epoch: int,
    valid_until: datetime | None,
) -> bool:
    """Whether a cached snapshot may still be used for the next model call.

    Revision equality alone is not sufficient, which is the whole reason
    ``valid_until`` exists: an entry can expire without any write happening.
    """

    state = db.get(UserMemoryState, owner_user_id)
    if state is None or not state.use_enabled:
        return False
    if state.revision != revision or state.epoch != epoch:
        return False
    if valid_until is not None and as_utc(valid_until) <= now():
        return False
    return True


def sanitize_source_snapshot(text: str) -> tuple[str, bool]:
    """Bound and clean text kept for channels that have no ChatMessage.

    Returns ``(safe_text, too_large)``. An oversized input is reported rather
    than truncated behind the employee's back, so the UI can ask them to pick
    the short passage they actually want saved.
    """

    cleaned = "".join(
        char
        for char in text
        if char == "\n" or unicodedata.category(char) not in {"Cc", "Cf", "Zl", "Zp"}
    )
    if len(cleaned) > settings.personal_memory_source_text_max_chars:
        return "", True
    return cleaned, False


def source_retention() -> timedelta:
    return timedelta(days=settings.personal_memory_source_retention_days)


def describe_error(code: MemoryErrorCode) -> tuple[int, str]:
    """Map an error code onto an HTTP status and a message safe to return.

    Missing and not-yours both answer 404 with the same text: a distinct 403
    would confirm that another employee's memory exists.
    """

    mapping: dict[str, tuple[int, str]] = {
        "memory_not_found": (404, "记忆不存在"),
        "memory_forbidden": (403, "没有权限"),
        "memory_version_conflict": (409, "记忆已被更新，请刷新后重新确认"),
        "memory_revision_conflict": (409, "记忆设置已被更新，请刷新后重试"),
        "memory_request_conflict": (409, "该请求编号已用于其他内容"),
        "memory_key_not_allowed": (422, "该字段不可写入记忆"),
        "memory_value_invalid": (422, "内容不符合该字段的保存规则"),
        "memory_limit_reached": (409, "记忆数量已达上限"),
        "memory_source_invalid": (409, "来源已失效，请重新说明后保存"),
        "memory_source_too_large": (422, "内容过长，请选择要保存的片段"),
        "memory_turn_not_live": (409, "本轮已结束，请重新发起保存"),
        "memory_learning_disabled": (409, "当前未开启记忆学习"),
        "memory_unavailable": (503, "记忆服务暂时不可用"),
    }
    return mapping.get(code, (503, "记忆服务暂时不可用"))


__all__ = [
    "MemoryPolicyError",
    "apply_memory_policy",
    "build_memory_snapshot",
    "contains_secret",
    "default_expiry",
    "describe_error",
    "resolve_addressing_name",
    "resolve_turn_source_text",
    "sanitize_source_snapshot",
    "snapshot_is_current",
    "source_retention",
    "validate_memory_proposal",
    "verify_quote",
]
