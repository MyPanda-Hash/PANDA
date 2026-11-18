"""Owner-scoped persistence for durable personal memory.

Rules this module holds to, so callers cannot break them by accident:

* Every read and write is filtered by ``owner_user_id``. A caller that only has
  an ``id`` cannot reach another employee's row from here.
* Nothing commits on its own except :func:`claim_extraction_job`, which is an
  explicitly independent transaction because a lease has to become durable
  before the extraction call leaves the process. Every other function flushes
  inside the caller's transaction so the state change, the receipt and the
  revision bump land together or not at all.
* State transitions run as conditional ``UPDATE`` statements whose affected row
  count is asserted. A row count that does not match is a conflict, never a
  silent overwrite.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import as_utc, new_token, now
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession
from app.models.user_memory import (
    UserMemoryEntry,
    UserMemoryEvidence,
    UserMemoryJob,
    UserMemoryOperation,
    UserMemoryState,
    UserMemoryTurn,
)
from app.schemas.user_memory import (
    MAX_ACTIVE_ENTRIES,
    MAX_OPEN_CANDIDATES,
    MAX_QUOTE_CHARS,
    MemoryErrorCode,
    sanitize_memory_text,
)

logger = logging.getLogger(__name__)

OPEN_CANDIDATE_STATUS = "candidate"
ACTIVE_STATUS = "active"
TERMINAL_ENTRY_STATUSES = ("rejected", "superseded", "forgotten", "expired")
VALID_TURN_STATUSES_FOR_EXTRACTION = ("live", "closed")


# ---------------------------------------------------------------------------
# Errors. Each carries the code the API maps onto its HTTP status.
# ---------------------------------------------------------------------------


class MemoryRepositoryError(Exception):
    """Base class carrying a stable, non-leaking error code."""

    def __init__(self, code: MemoryErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: MemoryErrorCode = code


class MemoryNotFoundError(MemoryRepositoryError):
    """Missing, or owned by somebody else. Both answer 404 identically."""

    def __init__(self, message: str = "memory not found") -> None:
        super().__init__("memory_not_found", message)


class MemoryConflictError(MemoryRepositoryError):
    """A CAS check failed. ``current`` carries what the server holds now."""

    def __init__(
        self,
        code: MemoryErrorCode = "memory_version_conflict",
        message: str = "memory version conflict",
        *,
        current: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message)
        self.current = current or {}


class MemoryLimitError(MemoryRepositoryError):
    """A bound was reached. Nothing is silently evicted to make room."""

    def __init__(self, message: str = "memory limit reached") -> None:
        super().__init__("memory_limit_reached", message)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def source_hash(text: str) -> str:
    """SHA-256 over the raw employee input's UTF-8 bytes.

    Deliberately not over the assembled prompt: RAG snippets, vision output and
    system text must never change the identity of what the employee said.
    """

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def value_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def request_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{new_token(12)}"


def _bump_revision(db: Session, state: UserMemoryState, *, stamp: datetime) -> int:
    """Move the observable state version. Callers do this inside their own
    transaction so the revision a client reads back always matches the content
    that transaction wrote."""

    db.flush()
    db.execute(
        update(UserMemoryState)
        .where(UserMemoryState.owner_user_id == state.owner_user_id)
        .values(revision=UserMemoryState.revision + 1, updated_at=stamp)
    )
    db.refresh(state)
    return state.revision


def _bump_epoch(db: Session, state: UserMemoryState, *, stamp: datetime) -> int:
    """Move the revocation fence and cancel work that predates it."""

    db.flush()
    db.execute(
        update(UserMemoryState)
        .where(UserMemoryState.owner_user_id == state.owner_user_id)
        .values(
            epoch=UserMemoryState.epoch + 1,
            revision=UserMemoryState.revision + 1,
            updated_at=stamp,
        )
    )
    db.refresh(state)
    db.execute(
        update(UserMemoryJob)
        .where(
            UserMemoryJob.owner_user_id == state.owner_user_id,
            UserMemoryJob.status.in_(("pending", "running")),
            UserMemoryJob.epoch < state.epoch,
        )
        .values(
            status="cancelled",
            updated_at=stamp,
            completed_at=stamp,
            lease_owner=None,
            lease_expires_at=None,
        )
    )
    return state.epoch


def _begin_sqlite_write(db: Session) -> None:
    """Make SAVEPOINTs subordinate to a real transaction in sqlite3 legacy mode."""

    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        driver = connection.connection.driver_connection
        if not getattr(driver, "in_transaction", False):
            connection.exec_driver_sql("BEGIN IMMEDIATE")


# ---------------------------------------------------------------------------
# State.
# ---------------------------------------------------------------------------


def get_or_create_state(db: Session, owner_user_id: str) -> UserMemoryState:
    """Return the employee's state row, creating the default row on first use.

    Concurrent first access is resolved by the primary key plus a conflict
    re-read, not by "select, find nothing, insert" - two requests racing on a
    brand new account must not both insert.
    """

    state = db.get(UserMemoryState, owner_user_id)
    if state is not None:
        return state
    _begin_sqlite_write(db)
    stamp = now()
    state = UserMemoryState(
        owner_user_id=owner_user_id,
        use_enabled=True,
        learning_mode="manual",
        revision=1,
        epoch=1,
        created_at=stamp,
        updated_at=stamp,
    )
    try:
        with db.begin_nested():
            db.add(state)
            db.flush()
    except IntegrityError:
        existing = db.get(UserMemoryState, owner_user_id)
        if existing is None:  # pragma: no cover - the FK to users failed instead
            raise
        return existing
    return state


def lock_owned_state(db: Session, owner_user_id: str) -> UserMemoryState:
    """Serialize memory writes without committing the caller's transaction.

    A no-op UPDATE takes a real write lock on SQLite as well as PostgreSQL.
    Refresh after acquiring it so an earlier identity-map read cannot bypass
    revision/epoch checks. Read-only snapshot callers do not take this lock.
    """

    _begin_sqlite_write(db)
    db.flush()
    get_or_create_state(db, owner_user_id)
    locked = db.execute(
        update(UserMemoryState)
        .where(UserMemoryState.owner_user_id == owner_user_id)
        .values(revision=UserMemoryState.revision)
    )
    if locked.rowcount != 1:
        raise MemoryNotFoundError()
    return db.scalars(
        select(UserMemoryState)
        .where(UserMemoryState.owner_user_id == owner_user_id)
        .execution_options(populate_existing=True)
    ).one()


def update_settings(
    db: Session,
    *,
    owner_user_id: str,
    expected_revision: int,
    use_enabled: bool | None = None,
    learning_mode: str | None = None,
) -> UserMemoryState:
    """Apply a CAS settings change.

    Turning learning off cancels queued extraction but never deletes anything
    already saved, and loosening the mode later does not activate old
    candidates or rescan old history: that is why only the cutoff moves here.
    """

    state = lock_owned_state(db, owner_user_id)
    if state.revision != expected_revision:
        raise MemoryConflictError(
            "memory_revision_conflict",
            "memory settings revision conflict",
            current={"revision": state.revision},
        )
    stamp = now()
    tightened = False
    if use_enabled is not None and use_enabled != state.use_enabled:
        state.use_enabled = use_enabled
        tightened = tightened or not use_enabled
    if learning_mode is not None and learning_mode != state.learning_mode:
        previous = state.learning_mode
        state.learning_mode = learning_mode
        if {"manual": 0, "suggest": 1, "auto_low_risk": 2}[learning_mode] < {
            "manual": 0,
            "suggest": 1,
            "auto_low_risk": 2,
        }[previous]:
            tightened = True
        if previous == "manual" and learning_mode != "manual":
            # Automatic learning starts now; older input stays out of the queue.
            state.automatic_source_cutoff_at = stamp
    if tightened:
        _bump_epoch(db, state, stamp=stamp)
    else:
        _bump_revision(db, state, stamp=stamp)
    return state


# ---------------------------------------------------------------------------
# Turns: the backend-issued identity of one authenticated raw input.
# ---------------------------------------------------------------------------


def register_turn(
    db: Session,
    *,
    owner_user_id: str,
    channel: str,
    request_key: str,
    raw_text: str,
    runtime_session_id: str | None = None,
    chat_session_id: str | None = None,
    chat_message_id: str | None = None,
    source_text_safe: str | None = None,
    source_text_retention: timedelta | None = None,
) -> UserMemoryTurn:
    """Register one authenticated input, or read back the existing turn.

    A transport retry carrying the same ``request_key`` and the same content
    reads back the same turn. The same key with different content is a
    conflict: new input needs a new key, otherwise a retry could relabel what
    the employee actually said.

    ``source_text_safe`` is only for channels with no ``ChatMessage`` to point
    at. Web turns keep a pointer and copy nothing.
    """

    state = lock_owned_state(db, owner_user_id)
    digest = source_hash(raw_text)
    existing = db.scalar(
        select(UserMemoryTurn).where(
            UserMemoryTurn.owner_user_id == owner_user_id,
            UserMemoryTurn.channel == channel,
            UserMemoryTurn.request_key == request_key,
        )
    )
    if existing is not None:
        if existing.source_hash != digest:
            raise MemoryConflictError(
                "memory_request_conflict",
                "request key already used for different content",
                current={"turn_id": existing.id},
            )
        return existing

    if source_text_safe is not None and source_hash(source_text_safe) != digest:
        raise MemoryRepositoryError(
            "memory_source_invalid", "safe source must match the authenticated input"
        )
    stamp = now()
    turn = UserMemoryTurn(
        id=_new_id("umt"),
        owner_user_id=owner_user_id,
        channel=channel,
        request_key=request_key,
        runtime_session_id=runtime_session_id,
        chat_session_id=chat_session_id,
        chat_message_id=chat_message_id,
        source_hash=digest,
        # Entry adapters reject unsafe input before passing a snapshot. Do not
        # strip it here: offsets and the source hash refer to these exact bytes.
        source_text_safe=source_text_safe,
        source_text_expires_at=(
            stamp + source_text_retention if source_text_retention else None
        ),
        epoch=state.epoch,
        status="live",
        created_at=stamp,
    )
    try:
        with db.begin_nested():
            db.add(turn)
            db.flush()
    except IntegrityError:
        conflict = db.scalar(
            select(UserMemoryTurn).where(
                UserMemoryTurn.owner_user_id == owner_user_id,
                UserMemoryTurn.channel == channel,
                UserMemoryTurn.request_key == request_key,
            )
        )
        if conflict is None:  # pragma: no cover - the owner FK failed instead
            raise
        if conflict.source_hash != digest:
            raise MemoryConflictError(
                "memory_request_conflict",
                "request key already used for different content",
                current={"turn_id": conflict.id},
            ) from None
        return conflict
    return turn


def close_turn(db: Session, *, owner_user_id: str, turn_id: str) -> None:
    """End online proposal authority for a turn without invalidating it.

    ``closed`` still allows an already-queued background extraction to run; only
    ``invalidated`` says the source itself is gone.
    """

    # Match extraction/cleanup lock order before touching a turn row.
    lock_owned_state(db, owner_user_id)
    stamp = now()
    db.execute(
        update(UserMemoryTurn)
        .where(
            UserMemoryTurn.owner_user_id == owner_user_id,
            UserMemoryTurn.id == turn_id,
            UserMemoryTurn.status == "live",
        )
        .values(status="closed", closed_at=stamp)
    )


def get_owned_turn(
    db: Session, *, owner_user_id: str, turn_id: str
) -> UserMemoryTurn | None:
    return db.scalar(
        select(UserMemoryTurn)
        .where(
            UserMemoryTurn.owner_user_id == owner_user_id,
            UserMemoryTurn.id == turn_id,
        )
        .execution_options(populate_existing=True)
    )


def invalidate_message_sources(
    db: Session,
    *,
    message_ids: Sequence[str],
    owner_user_id: str,
    surviving_hashes: Mapping[str, str] | None = None,
) -> int:
    """Invalidate turns whose source chat message changed or disappeared.

    Called from inside the same transaction that edits or removes the messages.
    ``surviving_hashes`` maps a message id to the hash of its current text: a
    message still present with the same content keeps its turn, so a physical
    delete/reinsert during ``replace`` does not manufacture new evidence or
    cancel unchanged memory. Anything absent from the map is treated as gone.
    """

    if not message_ids:
        return 0
    lock_owned_state(db, owner_user_id)
    surviving = dict(surviving_hashes or {})
    turns = list(
        db.scalars(
            select(UserMemoryTurn)
            .where(
                UserMemoryTurn.owner_user_id == owner_user_id,
                UserMemoryTurn.chat_message_id.in_(list(message_ids)),
                UserMemoryTurn.status != "invalidated",
            )
            .execution_options(populate_existing=True)
        )
    )
    return invalidate_turn_sources(
        db,
        owner_user_id=owner_user_id,
        turn_ids=[
            turn.id
            for turn in turns
            if surviving.get(turn.chat_message_id or "") != turn.source_hash
        ],
    )


def invalidate_turn_sources(
    db: Session, *, owner_user_id: str, turn_ids: Sequence[str]
) -> int:
    """Revoke authenticated sources without removing their replay tombstones."""

    if not turn_ids:
        return 0
    state = lock_owned_state(db, owner_user_id)
    turns = list(
        db.scalars(
            select(UserMemoryTurn)
            .where(
                UserMemoryTurn.owner_user_id == owner_user_id,
                UserMemoryTurn.id.in_(turn_ids),
                UserMemoryTurn.status != "invalidated",
            )
            .execution_options(populate_existing=True)
        )
    )
    stamp = now()
    invalidated = 0
    for turn in turns:
        turn.status = "invalidated"
        turn.closed_at = stamp
        turn.source_text_safe = None
        turn.source_text_expires_at = None
        invalidated += 1
        db.execute(
            update(UserMemoryJob)
            .where(
                UserMemoryJob.owner_user_id == turn.owner_user_id,
                UserMemoryJob.turn_id == turn.id,
                UserMemoryJob.status.in_(("pending", "running")),
            )
            .values(
                status="cancelled",
                updated_at=stamp,
                completed_at=stamp,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        db.execute(
            update(UserMemoryEvidence)
            .where(
                UserMemoryEvidence.owner_user_id == owner_user_id,
                UserMemoryEvidence.turn_id == turn.id,
            )
            .values(quote=None, quote_start=None, quote_end=None)
        )
    if invalidated:
        db.flush()
        expire_unsourced_automatic_entries(db, owner_user_id=owner_user_id)
        _bump_revision(db, state, stamp=stamp)
    return invalidated


def expire_unsourced_automatic_entries(db: Session, *, owner_user_id: str) -> int:
    """Stop injecting derived facts and candidates that lost every valid source.

    A fact with an independent manual confirmation survives - the employee's own
    confirmation happened, and deleting the original chat does not pretend it
    did not. Such a fact stays individually forgettable.
    """

    state = lock_owned_state(db, owner_user_id)
    entries = list(
        db.scalars(
            select(UserMemoryEntry)
            .where(
                UserMemoryEntry.owner_user_id == owner_user_id,
                UserMemoryEntry.status.in_((ACTIVE_STATUS, OPEN_CANDIDATE_STATUS)),
                UserMemoryEntry.origin != "manual",
            )
            .execution_options(populate_existing=True)
        )
    )
    if not entries:
        return 0
    stamp = now()
    expired = 0
    for entry in entries:
        valid = db.scalar(
            select(func.count())
            .select_from(UserMemoryEvidence)
            .join(
                UserMemoryTurn,
                (UserMemoryTurn.owner_user_id == UserMemoryEvidence.owner_user_id)
                & (UserMemoryTurn.id == UserMemoryEvidence.turn_id),
            )
            .where(
                UserMemoryEvidence.owner_user_id == owner_user_id,
                UserMemoryEvidence.memory_id == entry.id,
                UserMemoryTurn.status.in_(VALID_TURN_STATUSES_FOR_EXTRACTION),
                UserMemoryEvidence.source_hash == UserMemoryTurn.source_hash,
            )
        )
        if valid:
            continue
        entry.status = "expired"
        entry.version += 1
        entry.updated_at = stamp
        entry.value_json = None
        entry.value_hash = None
        _wipe_evidence_quotes(db, owner_user_id=owner_user_id, memory_ids=[entry.id])
        expired += 1
    if expired:
        _bump_revision(db, state, stamp=stamp)
    return expired


# ---------------------------------------------------------------------------
# Entries.
# ---------------------------------------------------------------------------


def list_owned_entries(
    db: Session,
    *,
    owner_user_id: str,
    statuses: Sequence[str] | None = None,
    key_prefix: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> list[UserMemoryEntry]:
    """Bounded, owner-filtered listing. ``cursor`` is the last seen entry id."""

    limit = max(1, min(limit, 50))
    stmt = select(UserMemoryEntry).where(UserMemoryEntry.owner_user_id == owner_user_id)
    if statuses:
        stmt = stmt.where(UserMemoryEntry.status.in_(list(statuses)))
    if key_prefix:
        stmt = stmt.where(UserMemoryEntry.key.startswith(key_prefix, autoescape=True))
    if cursor:
        stmt = stmt.where(UserMemoryEntry.id > cursor)
    stmt = stmt.order_by(UserMemoryEntry.id).limit(limit)
    return list(db.scalars(stmt))


def get_owned_entry(
    db: Session, *, owner_user_id: str, memory_id: str
) -> UserMemoryEntry:
    entry = db.scalar(
        select(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.id == memory_id,
        )
        .execution_options(populate_existing=True)
    )
    if entry is None:
        raise MemoryNotFoundError()
    return entry


def get_active_entry(
    db: Session, *, owner_user_id: str, key: str
) -> UserMemoryEntry | None:
    return db.scalar(
        select(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.key == key,
            UserMemoryEntry.status == ACTIVE_STATUS,
        )
        .execution_options(populate_existing=True)
    )


def list_active_entries(
    db: Session, *, owner_user_id: str, at: datetime | None = None
) -> list[UserMemoryEntry]:
    """Active facts that have not expired on their own.

    Expiry is checked here rather than left to a sweep, so a snapshot never
    serves a fact whose ``expires_at`` has already passed.
    """

    moment = at or now()
    stmt = (
        select(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.status == ACTIVE_STATUS,
            (UserMemoryEntry.expires_at.is_(None))
            | (UserMemoryEntry.expires_at > moment),
        )
        .order_by(UserMemoryEntry.key)
    )
    return list(db.scalars(stmt))


def automatic_cutoff_for_key(
    db: Session, *, owner_user_id: str, key: str
) -> datetime | None:
    """The newest forget barrier recorded for one key.

    Automatic extraction from any source registered before this instant may not
    re-create the key; re-saving needs a fresh authenticated input or a new
    manual confirmation.
    """

    cutoff = db.scalar(
        select(func.max(UserMemoryEntry.forgotten_source_cutoff_at)).where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.key == key,
        )
    )
    # SQLite hands timestamps back without a timezone, so a value read from the
    # database is normalized here rather than at each comparison. Getting this
    # wrong is a crash on SQLite and silently correct on PostgreSQL, which is
    # the worst way for it to be wrong.
    return as_utc(cutoff) if cutoff is not None else None


def _assert_capacity(db: Session, *, owner_user_id: str, status: str) -> None:
    if status == ACTIVE_STATUS:
        limit, target = MAX_ACTIVE_ENTRIES, ACTIVE_STATUS
    else:
        limit, target = MAX_OPEN_CANDIDATES, OPEN_CANDIDATE_STATUS
    used = db.scalar(
        select(func.count())
        .select_from(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.status == target,
        )
    )
    if (used or 0) >= limit:
        raise MemoryLimitError(f"{target} memory limit reached")


def _same_memory_scope(left: UserMemoryEntry, right: UserMemoryEntry) -> bool:
    # 不跨人工来源边界，也不延长短期记忆的有效期。
    return (
        left.key == right.key
        and left.value_hash == right.value_hash
        and left.value_json == right.value_json
        and (left.origin == "manual") == (right.origin == "manual")
        and (as_utc(left.expires_at) if left.expires_at else None)
        == (as_utc(right.expires_at) if right.expires_at else None)
    )


def reconcile_equivalent_candidates(db: Session, *, owner_user_id: str) -> None:
    """将历史等值候选归并，保留真实保存状态、来源和成功回执。"""
    state = lock_owned_state(db, owner_user_id)
    expire_due_entries(db, owner_user_id=owner_user_id)
    expire_unsourced_automatic_entries(db, owner_user_id=owner_user_id)
    entries = list(
        db.scalars(
            select(UserMemoryEntry)
            .where(
                UserMemoryEntry.owner_user_id == owner_user_id,
                UserMemoryEntry.status.in_((ACTIVE_STATUS, OPEN_CANDIDATE_STATUS)),
            )
            .order_by(UserMemoryEntry.created_at, UserMemoryEntry.id)
        )
    )
    active_by_key = {
        entry.key: entry for entry in entries if entry.status == ACTIVE_STATUS
    }
    canonical_candidates: list[UserMemoryEntry] = []
    changed = False
    for candidate in entries:
        if candidate.status != OPEN_CANDIDATE_STATUS:
            continue
        active = active_by_key.get(candidate.key)
        if active is None or not _same_memory_scope(candidate, active):
            active = next(
                (
                    item
                    for item in canonical_candidates
                    if _same_memory_scope(candidate, item)
                    and candidate.expected_active_version
                    == item.expected_active_version
                ),
                None,
            )
        if active is None:
            canonical_candidates.append(candidate)
            continue
        evidence = list(
            db.scalars(
                select(UserMemoryEvidence).where(
                    UserMemoryEvidence.owner_user_id == owner_user_id,
                    UserMemoryEvidence.memory_id == candidate.id,
                )
            )
        )
        for source in evidence:
            turn = get_owned_turn(
                db, owner_user_id=owner_user_id, turn_id=source.turn_id
            )
            if (
                turn is None
                or turn.status not in VALID_TURN_STATUSES_FOR_EXTRACTION
                or source.source_hash != turn.source_hash
            ):
                continue
            cutoff = automatic_cutoff_for_key(
                db, owner_user_id=owner_user_id, key=candidate.key
            )
            if cutoff is not None and as_utc(turn.created_at) <= cutoff:
                continue
            # 归并的是已授权的持久来源，不是旧 epoch 发起的新写入。
            # 遗忘其他字段会推进全局 epoch，但不能撤销本字段的既有来源。
            linked = db.scalar(
                select(UserMemoryEvidence.id).where(
                    UserMemoryEvidence.owner_user_id == owner_user_id,
                    UserMemoryEvidence.memory_id == active.id,
                    UserMemoryEvidence.turn_id == source.turn_id,
                )
            )
            if linked is None:
                source.memory_id = active.id
                db.flush()
            # 只重定向确有同一来源证据的成功提案，不触碰遗忘/失败回执。
            db.execute(
                update(UserMemoryOperation)
                .where(
                    UserMemoryOperation.owner_user_id == owner_user_id,
                    UserMemoryOperation.result_memory_id == candidate.id,
                    UserMemoryOperation.turn_id == source.turn_id,
                    UserMemoryOperation.status == "succeeded",
                    UserMemoryOperation.kind.in_(("propose", "extract_attempt")),
                )
                .values(result_memory_id=active.id)
            )
        candidate.status = "superseded"
        candidate.version += 1
        candidate.updated_at = now()
        changed = True
    if changed:
        _bump_revision(db, state, stamp=now())


def propose_entry(
    db: Session,
    *,
    owner_user_id: str,
    key: str,
    value: str,
    origin: str,
    turn_id: str | None = None,
    quote: str | None = None,
    quote_start: int | None = None,
    quote_end: int | None = None,
    evidence_kind: str = "user_statement",
    expected_active_version: int | None = None,
    expires_at: datetime | None = None,
    auto_confirm: bool = False,
) -> UserMemoryEntry:
    """Create a candidate row plus its evidence.

    A conflicting candidate never overwrites the active fact while it waits:
    they are separate rows, so the employee keeps seeing the old value until
    they confirm the new one.
    """

    state = lock_owned_state(db, owner_user_id)
    reconcile_equivalent_candidates(db, owner_user_id=owner_user_id)
    stamp = now()
    active = get_active_entry(db, owner_user_id=owner_user_id, key=key)
    entry = UserMemoryEntry(
        id=_new_id("ume"),
        owner_user_id=owner_user_id,
        key=key,
        value_json=json.dumps(value, ensure_ascii=False),
        value_hash=value_hash(value),
        status=OPEN_CANDIDATE_STATUS,
        origin=origin,
        version=1,
        expected_active_version=(
            expected_active_version
            if expected_active_version is not None
            else (active.version if active else None)
        ),
        created_at=stamp,
        updated_at=stamp,
        expires_at=expires_at,
    )
    existing = next(
        (
            item
            for item in db.scalars(
                select(UserMemoryEntry)
                .where(
                    UserMemoryEntry.owner_user_id == owner_user_id,
                    UserMemoryEntry.key == key,
                    UserMemoryEntry.value_hash == entry.value_hash,
                    UserMemoryEntry.status.in_((ACTIVE_STATUS, OPEN_CANDIDATE_STATUS)),
                )
                .order_by(UserMemoryEntry.status, UserMemoryEntry.created_at)
            )
            if _same_memory_scope(item, entry)
            and (
                item.status == ACTIVE_STATUS
                or item.expected_active_version == entry.expected_active_version
            )
        ),
        None,
    )
    if existing is not None:
        if turn_id:
            add_evidence(
                db,
                owner_user_id=owner_user_id,
                memory_id=existing.id,
                turn_id=turn_id,
                quote=quote,
                quote_start=quote_start,
                quote_end=quote_end,
                evidence_kind=evidence_kind,
            )
            _bump_revision(db, state, stamp=stamp)
        # 复用人工待确认候选绝不触发后来请求的自动确认政策。
        return existing
    _assert_capacity(db, owner_user_id=owner_user_id, status=OPEN_CANDIDATE_STATUS)
    db.add(entry)
    db.flush()
    if turn_id:
        add_evidence(
            db,
            owner_user_id=owner_user_id,
            memory_id=entry.id,
            turn_id=turn_id,
            quote=quote,
            quote_start=quote_start,
            quote_end=quote_end,
            evidence_kind=evidence_kind,
        )
    _bump_revision(db, state, stamp=stamp)
    if auto_confirm:
        return confirm_entry(
            db,
            owner_user_id=owner_user_id,
            memory_id=entry.id,
            expected_version=entry.version,
            expected_active_version=expected_active_version,
        )
    return entry


def add_evidence(
    db: Session,
    *,
    owner_user_id: str,
    memory_id: str,
    turn_id: str,
    quote: str | None,
    quote_start: int | None,
    quote_end: int | None,
    evidence_kind: str,
) -> UserMemoryEvidence | None:
    """Attach one source. Re-extracting the same turn adds nothing.

    The composite foreign keys mean the database also rejects an owner mismatch
    between the fact and the turn, so a forged identifier cannot cross owners
    even if a caller forgets to check.
    """

    state = lock_owned_state(db, owner_user_id)
    turn = db.scalar(
        select(UserMemoryTurn)
        .where(
            UserMemoryTurn.owner_user_id == owner_user_id,
            UserMemoryTurn.id == turn_id,
        )
        .execution_options(populate_existing=True)
    )
    if turn is None or turn.status == "invalidated" or turn.epoch != state.epoch:
        raise MemoryRepositoryError("memory_source_invalid", "source turn is revoked")
    entry = get_owned_entry(db, owner_user_id=owner_user_id, memory_id=memory_id)
    if entry.status not in (OPEN_CANDIDATE_STATUS, ACTIVE_STATUS):
        raise MemoryRepositoryError(
            "memory_source_invalid", "memory is no longer writable"
        )
    cutoff = automatic_cutoff_for_key(db, owner_user_id=owner_user_id, key=entry.key)
    if cutoff is not None and as_utc(turn.created_at) <= cutoff:
        raise MemoryRepositoryError(
            "memory_source_invalid", "source predates forgetting"
        )
    if evidence_kind == "manual_confirmation" and turn.channel != "manual":
        raise MemoryRepositoryError(
            "memory_source_invalid", "confirmation needs manual input"
        )
    existing = db.scalar(
        select(UserMemoryEvidence).where(
            UserMemoryEvidence.owner_user_id == owner_user_id,
            UserMemoryEvidence.memory_id == memory_id,
            UserMemoryEvidence.turn_id == turn_id,
        )
    )
    if existing is not None:
        return existing
    safe_quote = sanitize_memory_text(quote)[:MAX_QUOTE_CHARS] if quote else None
    evidence = UserMemoryEvidence(
        id=_new_id("umv"),
        owner_user_id=owner_user_id,
        memory_id=memory_id,
        turn_id=turn_id,
        source_hash=turn.source_hash,
        quote=safe_quote,
        quote_start=quote_start,
        quote_end=quote_end,
        evidence_kind=evidence_kind,
        created_at=now(),
    )
    db.add(evidence)
    db.flush()
    return evidence


def list_entry_evidence(
    db: Session,
    *,
    owner_user_id: str,
    memory_id: str,
    cursor: str | None = None,
    limit: int = 10,
) -> list[tuple[UserMemoryEvidence, UserMemoryTurn]]:
    limit = max(1, min(limit, 10))
    stmt = (
        select(UserMemoryEvidence, UserMemoryTurn)
        .join(
            UserMemoryTurn,
            (UserMemoryTurn.owner_user_id == UserMemoryEvidence.owner_user_id)
            & (UserMemoryTurn.id == UserMemoryEvidence.turn_id),
        )
        .where(
            UserMemoryEvidence.owner_user_id == owner_user_id,
            UserMemoryEvidence.memory_id == memory_id,
        )
    )
    if cursor:
        stmt = stmt.where(UserMemoryEvidence.id > cursor)
    stmt = stmt.order_by(UserMemoryEvidence.id).limit(limit)
    return [(row[0], row[1]) for row in db.execute(stmt)]


def confirm_entry(
    db: Session,
    *,
    owner_user_id: str,
    memory_id: str,
    expected_version: int,
    expected_active_version: int | None = None,
) -> UserMemoryEntry:
    """Activate a candidate: supersede, activate, bump, all in one transaction.

    Every step is a conditional ``UPDATE`` whose affected row count is checked.
    A stale candidate - one raised against an active version that has since
    moved - is refused rather than allowed to overwrite the newer correction.
    """

    state = lock_owned_state(db, owner_user_id)
    expire_due_entries(db, owner_user_id=owner_user_id)
    expire_unsourced_automatic_entries(db, owner_user_id=owner_user_id)
    entry = get_owned_entry(db, owner_user_id=owner_user_id, memory_id=memory_id)
    if (
        entry.status == "superseded"
        and entry.confirmed_at is None
        and entry.version == expected_version + 1
        and (entry.expires_at is None or as_utc(entry.expires_at) > now())
    ):
        active = get_active_entry(db, owner_user_id=owner_user_id, key=entry.key)
        if active is not None and _same_memory_scope(entry, active):
            # 旧页面仍可能点击已归并候选；只确认当前相同结果，不覆盖新值。
            return active
    if entry.status != OPEN_CANDIDATE_STATUS or (
        entry.expires_at is not None and as_utc(entry.expires_at) <= now()
    ):
        raise MemoryConflictError(
            "memory_version_conflict",
            "entry is not an open candidate",
            current={"status": entry.status, "version": entry.version},
        )
    if entry.version != expected_version:
        raise MemoryConflictError(current={"version": entry.version})

    active = get_active_entry(db, owner_user_id=owner_user_id, key=entry.key)
    if active is not None and _same_memory_scope(entry, active):
        reconcile_equivalent_candidates(db, owner_user_id=owner_user_id)
        return active

    stamp = now()
    active = get_active_entry(db, owner_user_id=owner_user_id, key=entry.key)
    active_version = active.version if active else None
    if (
        expected_active_version is not None
        and expected_active_version != active_version
    ):
        raise MemoryConflictError(
            "memory_version_conflict",
            "active memory changed since it was shown",
            current={"active_version": active_version},
        )
    if (
        expected_active_version is None
        and entry.expected_active_version != active_version
    ):
        raise MemoryConflictError(
            "memory_version_conflict",
            "candidate is stale and needs re-confirmation",
            current={"active_version": active_version},
        )

    if active is None:
        _assert_capacity(db, owner_user_id=owner_user_id, status=ACTIVE_STATUS)

    # The active version for a key must be strictly monotonic, otherwise it is
    # useless as a staleness fence: version numbers live per row, so two
    # unrelated candidates can both sit at version 1 and a stale one would
    # match the newly activated row by coincidence.
    new_version = max(expected_version, active.version if active else 0) + 1

    if active is not None:
        superseded = db.execute(
            update(UserMemoryEntry)
            .where(
                UserMemoryEntry.owner_user_id == owner_user_id,
                UserMemoryEntry.id == active.id,
                UserMemoryEntry.status == ACTIVE_STATUS,
                UserMemoryEntry.version == active.version,
            )
            .values(status="superseded", version=active.version + 1, updated_at=stamp)
        )
        if superseded.rowcount != 1:
            raise MemoryConflictError(
                "memory_version_conflict",
                "active memory changed during activation",
                current={"active_version": active_version},
            )

    activated = db.execute(
        update(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.id == memory_id,
            UserMemoryEntry.status == OPEN_CANDIDATE_STATUS,
            UserMemoryEntry.version == expected_version,
        )
        .values(
            status=ACTIVE_STATUS,
            version=new_version,
            expected_active_version=None,
            confirmed_at=stamp,
            updated_at=stamp,
        )
    )
    if activated.rowcount != 1:
        raise MemoryConflictError(current={"version": entry.version})

    db.expire(entry)
    reconcile_equivalent_candidates(db, owner_user_id=owner_user_id)
    _bump_revision(db, state, stamp=stamp)
    return get_owned_entry(db, owner_user_id=owner_user_id, memory_id=memory_id)


def reject_entry(
    db: Session, *, owner_user_id: str, memory_id: str, expected_version: int
) -> UserMemoryEntry:
    """Reject a candidate so a later extraction does not re-raise the same one."""

    state = lock_owned_state(db, owner_user_id)
    entry = get_owned_entry(db, owner_user_id=owner_user_id, memory_id=memory_id)
    if entry.status != OPEN_CANDIDATE_STATUS or (
        entry.expires_at is not None and as_utc(entry.expires_at) <= now()
    ):
        raise MemoryConflictError(
            "memory_version_conflict",
            "entry is not an open candidate",
            current={"status": entry.status, "version": entry.version},
        )
    stamp = now()
    changed = db.execute(
        update(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.id == memory_id,
            UserMemoryEntry.status == OPEN_CANDIDATE_STATUS,
            UserMemoryEntry.version == expected_version,
        )
        .values(
            status="rejected",
            version=expected_version + 1,
            value_json=None,
            value_hash=None,
            updated_at=stamp,
        )
    )
    if changed.rowcount != 1:
        raise MemoryConflictError(current={"version": entry.version})
    _wipe_evidence_quotes(db, owner_user_id=owner_user_id, memory_ids=[memory_id])
    _bump_revision(db, state, stamp=stamp)
    db.expire(entry)
    return get_owned_entry(db, owner_user_id=owner_user_id, memory_id=memory_id)


def update_entry(
    db: Session,
    *,
    owner_user_id: str,
    memory_id: str,
    value: str,
    expected_version: int,
    turn_id: str | None = None,
) -> UserMemoryEntry:
    """Correct an owned entry in place. Account profile fields are untouched."""

    state = lock_owned_state(db, owner_user_id)
    entry = get_owned_entry(db, owner_user_id=owner_user_id, memory_id=memory_id)
    if entry.status not in (ACTIVE_STATUS, OPEN_CANDIDATE_STATUS) or (
        entry.expires_at is not None and as_utc(entry.expires_at) <= now()
    ):
        raise MemoryConflictError(
            "memory_version_conflict",
            "entry is no longer editable",
            current={"status": entry.status, "version": entry.version},
        )
    stamp = now()
    changed = db.execute(
        update(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.id == memory_id,
            UserMemoryEntry.version == expected_version,
            UserMemoryEntry.status == entry.status,
        )
        .values(
            value_json=json.dumps(value, ensure_ascii=False),
            value_hash=value_hash(value),
            origin="manual",
            version=expected_version + 1,
            updated_at=stamp,
        )
    )
    if changed.rowcount != 1:
        raise MemoryConflictError(current={"version": entry.version})
    if turn_id:
        add_evidence(
            db,
            owner_user_id=owner_user_id,
            memory_id=memory_id,
            turn_id=turn_id,
            quote=None,
            quote_start=None,
            quote_end=None,
            evidence_kind="manual_confirmation",
        )
    _bump_revision(db, state, stamp=stamp)
    db.expire(entry)
    return get_owned_entry(db, owner_user_id=owner_user_id, memory_id=memory_id)


def _wipe_evidence_quotes(
    db: Session, *, owner_user_id: str, memory_ids: Iterable[str]
) -> None:
    """Remove stored excerpts while keeping the bare provenance link.

    What survives is the identifier, the kind and the timestamp - nothing from
    which the forgotten value could be reconstructed.
    """

    ids = list(memory_ids)
    if not ids:
        return
    db.execute(
        update(UserMemoryEvidence)
        .where(
            UserMemoryEvidence.owner_user_id == owner_user_id,
            UserMemoryEvidence.memory_id.in_(ids),
        )
        .values(quote=None, quote_start=None, quote_end=None, source_hash="")
    )


def _wipe_linked_source_text(
    db: Session, *, owner_user_id: str, memory_ids: Sequence[str]
) -> None:
    db.execute(
        update(UserMemoryTurn)
        .where(
            UserMemoryTurn.owner_user_id == owner_user_id,
            UserMemoryTurn.id.in_(
                select(UserMemoryEvidence.turn_id).where(
                    UserMemoryEvidence.owner_user_id == owner_user_id,
                    UserMemoryEvidence.memory_id.in_(memory_ids),
                )
            ),
        )
        .values(source_text_safe=None, source_text_expires_at=None)
    )


def forget_entry(
    db: Session, *, owner_user_id: str, memory_id: str, expected_version: int
) -> UserMemoryEntry:
    """Forget one key: wipe values and excerpts, then raise the barrier.

    This revokes the memory and its automatic re-derivation. It does not delete
    the original chat, the Pi transcript or anything a provider already
    received - the UI and the API say so in those words.
    """

    state = lock_owned_state(db, owner_user_id)
    entry = get_owned_entry(db, owner_user_id=owner_user_id, memory_id=memory_id)
    if entry.version != expected_version:
        raise MemoryConflictError(current={"version": entry.version})
    stamp = now()
    key = entry.key

    claimed = db.execute(
        update(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.id == memory_id,
            UserMemoryEntry.version == expected_version,
        )
        .values(version=UserMemoryEntry.version + 1)
    )
    if claimed.rowcount != 1:
        raise MemoryConflictError(current={"version": entry.version})
    siblings = list(
        db.scalars(
            select(UserMemoryEntry.id).where(
                UserMemoryEntry.owner_user_id == owner_user_id,
                UserMemoryEntry.key == key,
            )
        )
    )
    # Wipe every value and hash for the key: a hash of a short name is itself
    # reversible, so nothing that could recover the value may survive.
    db.execute(
        update(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.key == key,
        )
        .values(
            value_json=None,
            value_hash=None,
            forgotten_source_cutoff_at=stamp,
            updated_at=stamp,
        )
    )
    # Open candidates for the key are withdrawn rather than left to resurrect it.
    db.execute(
        update(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.key == key,
            UserMemoryEntry.status == OPEN_CANDIDATE_STATUS,
        )
        .values(
            status="rejected", version=UserMemoryEntry.version + 1, updated_at=stamp
        )
    )
    db.execute(
        update(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.key == key,
            UserMemoryEntry.status == ACTIVE_STATUS,
        )
        .values(
            status="forgotten", version=UserMemoryEntry.version + 1, updated_at=stamp
        )
    )
    db.execute(
        update(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.id == memory_id,
        )
        .values(status="forgotten", version=expected_version + 1, updated_at=stamp)
    )
    _wipe_evidence_quotes(db, owner_user_id=owner_user_id, memory_ids=siblings)
    _wipe_linked_source_text(db, owner_user_id=owner_user_id, memory_ids=siblings)
    _bump_epoch(db, state, stamp=stamp)
    db.expire(entry)
    return get_owned_entry(db, owner_user_id=owner_user_id, memory_id=memory_id)


def clear_entries(db: Session, *, owner_user_id: str, expected_revision: int) -> int:
    """Forget everything and stop old sources from re-deriving any of it."""

    state = lock_owned_state(db, owner_user_id)
    if state.revision != expected_revision:
        raise MemoryConflictError(
            "memory_revision_conflict",
            "memory revision conflict",
            current={"revision": state.revision},
        )
    stamp = now()
    memory_ids = list(
        db.scalars(
            select(UserMemoryEntry.id).where(
                UserMemoryEntry.owner_user_id == owner_user_id
            )
        )
    )
    cleared = db.execute(
        update(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.status.in_((ACTIVE_STATUS, OPEN_CANDIDATE_STATUS)),
        )
        .values(status="forgotten", updated_at=stamp)
    ).rowcount
    db.execute(
        update(UserMemoryEntry)
        .where(UserMemoryEntry.owner_user_id == owner_user_id)
        .values(
            value_json=None,
            value_hash=None,
            version=UserMemoryEntry.version + 1,
            forgotten_source_cutoff_at=stamp,
            updated_at=stamp,
        )
    )
    _wipe_evidence_quotes(db, owner_user_id=owner_user_id, memory_ids=memory_ids)
    db.execute(
        update(UserMemoryTurn)
        .where(UserMemoryTurn.owner_user_id == owner_user_id)
        .values(source_text_safe=None, source_text_expires_at=None)
    )
    state.automatic_source_cutoff_at = stamp
    _bump_epoch(db, state, stamp=stamp)
    return cleared


# ---------------------------------------------------------------------------
# Operation receipts: idempotency for every write path.
# ---------------------------------------------------------------------------


def claim_operation(
    db: Session,
    *,
    owner_user_id: str,
    kind: str,
    request_id: str,
    payload_hash: str,
    turn_id: str | None = None,
    job_id: str | None = None,
    memory_id: str | None = None,
) -> tuple[UserMemoryOperation, bool]:
    """Insert a receipt, or read back the one an earlier attempt already wrote.

    Returns ``(operation, is_replay)``. A replay must never re-activate a
    candidate, bump a version or delete anything a second time; the caller
    reports the stored result instead.

    The same ``request_id`` with a different payload is a conflict: it is a new
    request wearing an old key, and answering it from the old receipt would
    silently drop the change.
    """

    lock_owned_state(db, owner_user_id)
    stamp = now()
    operation = UserMemoryOperation(
        id=_new_id("umo"),
        owner_user_id=owner_user_id,
        kind=kind,
        request_id=request_id,
        request_hash=payload_hash,
        turn_id=turn_id,
        job_id=job_id,
        memory_id=memory_id,
        status="pending",
        usage_json="{}",
        created_at=stamp,
    )
    try:
        with db.begin_nested():
            db.add(operation)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(UserMemoryOperation).where(
                UserMemoryOperation.owner_user_id == owner_user_id,
                UserMemoryOperation.kind == kind,
                UserMemoryOperation.request_id == request_id,
            )
        )
        if existing is None:  # pragma: no cover - the owner FK failed instead
            raise
        if existing.request_hash != payload_hash:
            raise MemoryConflictError(
                "memory_request_conflict",
                "request id already used with a different payload",
                current={"request_id": request_id},
            ) from None
        return existing, True
    return operation, False


def complete_operation(
    db: Session,
    *,
    operation: UserMemoryOperation,
    status: str,
    result_memory_id: str | None = None,
    result_revision: int | None = None,
    usage: Mapping[str, Any] | None = None,
    error_code: str | None = None,
) -> UserMemoryOperation:
    """Finish a receipt in the same transaction as the change it describes.

    ``usage`` is only ever a real provider/model/token/cost/calls record. Plain
    CRUD leaves ``{}`` here so nothing fabricates a model card downstream.
    """

    operation.status = status
    operation.result_memory_id = result_memory_id
    operation.result_revision = result_revision
    operation.error_code = error_code
    if usage:
        operation.usage_json = json.dumps(
            dict(usage), ensure_ascii=False, separators=(",", ":")
        )
    operation.completed_at = now()
    db.flush()
    return operation


def receipt_is_revoked(db: Session, operation: UserMemoryOperation) -> bool:
    """True when a once-successful receipt now points at a revoked or expired fact.

    Replaying such a request reports the current revoked state; it never
    returns the deleted value and never brings it back.
    """

    if operation.status != "succeeded" or not operation.result_memory_id:
        return False
    entry = db.scalar(
        select(UserMemoryEntry).where(
            UserMemoryEntry.owner_user_id == operation.owner_user_id,
            UserMemoryEntry.id == operation.result_memory_id,
        )
    )
    if entry is None:
        return True
    return (
        entry.status in ("forgotten", "expired")
        or entry.value_json is None
        or (entry.expires_at is not None and as_utc(entry.expires_at) <= now())
    )


def list_turn_operations(
    db: Session, *, owner_user_id: str, turn_id: str
) -> list[UserMemoryOperation]:
    return list(
        db.scalars(
            select(UserMemoryOperation)
            .where(
                UserMemoryOperation.owner_user_id == owner_user_id,
                UserMemoryOperation.turn_id == turn_id,
            )
            .order_by(UserMemoryOperation.created_at)
        )
    )


def get_turn_status(
    db: Session, *, owner_user_id: str, turn_id: str
) -> tuple[UserMemoryTurn, list[UserMemoryEntry], list[UserMemoryOperation]]:
    """Everything the chat UI needs to render one turn's memory state.

    Read back from the server on reconnect and on refresh: a lost SSE ``done``
    never revokes a memory that was already committed, and client storage is
    never treated as authoritative for "saved".
    """

    turn = get_owned_turn(db, owner_user_id=owner_user_id, turn_id=turn_id)
    if turn is None:
        raise MemoryNotFoundError()
    candidates = list(
        db.scalars(
            select(UserMemoryEntry)
            .join(
                UserMemoryEvidence,
                (UserMemoryEvidence.owner_user_id == UserMemoryEntry.owner_user_id)
                & (UserMemoryEvidence.memory_id == UserMemoryEntry.id),
            )
            .where(
                UserMemoryEntry.owner_user_id == owner_user_id,
                UserMemoryEvidence.turn_id == turn_id,
            )
            .order_by(UserMemoryEntry.created_at)
        )
    )
    return (
        turn,
        candidates,
        list_turn_operations(db, owner_user_id=owner_user_id, turn_id=turn_id),
    )


# ---------------------------------------------------------------------------
# Extraction jobs.
# ---------------------------------------------------------------------------


def enqueue_extraction_job(
    db: Session,
    *,
    owner_user_id: str,
    turn_id: str,
    source_hash_value: str,
    extractor_version: str,
    epoch: int,
) -> UserMemoryJob | None:
    """Enqueue extraction in the caller's transaction, or reuse the same job.

    The idempotency key is ``(owner, turn, source hash, extractor version)``, so
    a retried registration cannot produce a second paid extraction of one input.
    """

    lock_owned_state(db, owner_user_id)
    stamp = now()
    job = UserMemoryJob(
        id=_new_id("umj"),
        owner_user_id=owner_user_id,
        turn_id=turn_id,
        source_hash=source_hash_value,
        extractor_version=extractor_version,
        epoch=epoch,
        status="pending",
        attempts=0,
        claim_generation=0,
        next_attempt_at=stamp,
        created_at=stamp,
        updated_at=stamp,
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(UserMemoryJob).where(
                UserMemoryJob.owner_user_id == owner_user_id,
                UserMemoryJob.turn_id == turn_id,
                UserMemoryJob.source_hash == source_hash_value,
                UserMemoryJob.extractor_version == extractor_version,
            )
        )
        return existing
    return job


def claim_extraction_job(
    db: Session,
    *,
    lease_owner: str,
    lease_seconds: int,
    max_attempts: int = 3,
) -> UserMemoryJob | None:
    """Claim one due job, in its own transaction.

    Two dialects, two mechanisms, one guarantee. On PostgreSQL the candidate
    rows are taken with ``FOR UPDATE SKIP LOCKED``, so several backend
    instances step over each other's rows instead of serialising on them.
    SQLite has no row locks, so the conditional
    ``UPDATE ... WHERE status/lease/generation`` is what makes the claim
    exclusive there. The ``UPDATE`` guard runs on both: it is the actual
    correctness boundary, and ``SKIP LOCKED`` is only there to keep
    PostgreSQL workers from contending. ``with_for_update()`` alone would not
    be a cross-database guarantee.

    ``claim_generation`` moves on every claim, so a worker whose lease expired
    cannot commit even if it renewed its own timer locally.

    This commits deliberately: the lease has to be durable before the
    extraction HTTP call leaves the process, otherwise a crash would strand the
    job with no record that anyone had started it.
    """

    stamp = now()
    statement = (
        select(UserMemoryJob.id)
        .where(
            UserMemoryJob.status.in_(("pending", "running")),
            UserMemoryJob.attempts < max_attempts,
            (UserMemoryJob.next_attempt_at.is_(None))
            | (UserMemoryJob.next_attempt_at <= stamp),
            (UserMemoryJob.lease_expires_at.is_(None))
            | (UserMemoryJob.lease_expires_at <= stamp),
        )
        .order_by(UserMemoryJob.created_at)
        .limit(20)
    )
    if db.get_bind().dialect.name != "sqlite":
        statement = statement.with_for_update(of=UserMemoryJob, skip_locked=True)
    candidates = list(db.scalars(statement))
    for job_id in candidates:
        job = db.get(UserMemoryJob, job_id)
        if job is None:
            continue
        generation = job.claim_generation
        claimed = db.execute(
            update(UserMemoryJob)
            .where(
                UserMemoryJob.id == job_id,
                UserMemoryJob.claim_generation == generation,
                UserMemoryJob.status.in_(("pending", "running")),
                UserMemoryJob.attempts < max_attempts,
                (UserMemoryJob.next_attempt_at.is_(None))
                | (UserMemoryJob.next_attempt_at <= stamp),
                (UserMemoryJob.lease_expires_at.is_(None))
                | (UserMemoryJob.lease_expires_at <= stamp),
            )
            .values(
                status="running",
                attempts=UserMemoryJob.attempts + 1,
                claim_generation=generation + 1,
                lease_owner=lease_owner,
                lease_expires_at=stamp + timedelta(seconds=lease_seconds),
                started_at=stamp,
                updated_at=stamp,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount == 1:
            db.refresh(job)
            db.expunge(job)
            db.commit()
            return job
        # A zero-row UPDATE is not an error on either dialect: somebody took
        # the row between the select and the guard. Rolling back here would
        # release the PostgreSQL row locks the select is holding, so the
        # transaction stays open and the next candidate is tried instead.
    # A polling worker calls this on every tick. Without this rollback the
    # select above would leave a transaction open on an idle poll - a read
    # transaction on SQLite, which blocks the next writer, and idle-in-
    # transaction row locks on PostgreSQL.
    db.rollback()
    return None


def finish_extraction_job(
    db: Session,
    *,
    job_id: str,
    lease_owner: str,
    claim_generation: int,
    status: str,
    error_code: str | None = None,
    retry_after_seconds: int | None = None,
) -> bool:
    """Commit a job outcome only if this worker still holds the claim.

    Re-checking ``lease_owner`` and ``claim_generation`` here is what stops a
    slow worker whose lease expired - and whose job was re-claimed - from
    writing a result derived from stale authority.
    """

    if status not in ("succeeded", "failed", "cancelled"):
        raise ValueError("invalid extraction outcome")
    stamp = now()
    values: dict[str, Any] = {"updated_at": stamp, "last_error_safe": error_code}
    if status == "failed" and retry_after_seconds is not None:
        values.update(
            status="pending",
            lease_owner=None,
            lease_expires_at=None,
            next_attempt_at=stamp + timedelta(seconds=retry_after_seconds),
        )
    else:
        values.update(
            status=status,
            lease_owner=None,
            lease_expires_at=None,
            completed_at=stamp,
        )
    changed = db.execute(
        update(UserMemoryJob)
        .where(
            UserMemoryJob.id == job_id,
            UserMemoryJob.lease_owner == lease_owner,
            UserMemoryJob.claim_generation == claim_generation,
            UserMemoryJob.status == "running",
            UserMemoryJob.lease_expires_at > stamp,
        )
        .values(**values)
        .execution_options(synchronize_session="fetch")
    )
    return changed.rowcount == 1


def job_is_still_authorized(db: Session, job: UserMemoryJob) -> bool:
    """Fence a detached claim inside the transaction committing its result.

    Chat writers lock the session before memory state; follow the same order.
    The owner state write serializes SQLite too. All loaded authority is fresh,
    and no lock survives the caller's transaction or the provider HTTP call.
    """

    claim = (
        job.id,
        job.owner_user_id,
        job.turn_id,
        job.source_hash,
        job.epoch,
        job.lease_owner,
        job.claim_generation,
    )
    job_id, owner_id, turn_id, digest, epoch, lease_owner, generation = claim
    if not lease_owner:
        return False
    if (
        db.scalar(
            select(User.id)
            .where(User.id == owner_id, User.status == "active")
            .with_for_update(of=User)
        )
        is None
    ):
        return False
    pointer = db.execute(
        select(UserMemoryTurn.chat_session_id, UserMemoryTurn.chat_message_id).where(
            UserMemoryTurn.owner_user_id == owner_id,
            UserMemoryTurn.id == turn_id,
        )
    ).first()
    if pointer is None:
        return False
    session_id, message_id = pointer
    if message_id and not session_id:
        return False
    if session_id:
        session = db.scalar(
            select(ChatSession)
            .where(ChatSession.id == session_id, ChatSession.owner_user_id == owner_id)
            .with_for_update(of=ChatSession)
            .execution_options(populate_existing=True)
        )
        if session is None or session.writing_project_id:
            return False
    if (
        db.scalar(
            select(UserMemoryState.owner_user_id).where(
                UserMemoryState.owner_user_id == owner_id
            )
        )
        is None
    ):
        return False
    state = lock_owned_state(db, owner_id)
    if state.epoch != epoch or not state.use_enabled or state.learning_mode == "manual":
        return False
    turn = db.scalar(
        select(UserMemoryTurn)
        .where(UserMemoryTurn.owner_user_id == owner_id, UserMemoryTurn.id == turn_id)
        .with_for_update(of=UserMemoryTurn)
        .execution_options(populate_existing=True)
    )
    if (
        turn is None
        or turn.status not in VALID_TURN_STATUSES_FOR_EXTRACTION
        or turn.epoch != epoch
        or turn.source_hash != digest
        or turn.channel not in ("web", "open", "wecom")
        or (
            state.automatic_source_cutoff_at is not None
            and as_utc(turn.created_at) <= as_utc(state.automatic_source_cutoff_at)
        )
    ):
        return False
    if turn.chat_message_id:
        current_text = db.scalar(
            select(ChatMessage.content)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .where(
                ChatMessage.id == turn.chat_message_id,
                ChatMessage.owner_user_id == owner_id,
                ChatMessage.role == "user",
                ChatMessage.session_id == session_id,
                ChatSession.owner_user_id == owner_id,
                ChatSession.writing_project_id.is_(None),
            )
        )
        if current_text is None or source_hash(current_text) != digest:
            return False
    elif (
        turn.channel == "web"
        or turn.source_text_safe is None
        or turn.source_text_expires_at is None
        or as_utc(turn.source_text_expires_at) <= now()
        or source_hash(turn.source_text_safe) != digest
    ):
        return False
    return (
        db.scalar(
            select(UserMemoryJob.id)
            .where(
                UserMemoryJob.id == job_id,
                UserMemoryJob.owner_user_id == owner_id,
                UserMemoryJob.turn_id == turn_id,
                UserMemoryJob.source_hash == digest,
                UserMemoryJob.epoch == epoch,
                UserMemoryJob.status == "running",
                UserMemoryJob.lease_owner == lease_owner,
                UserMemoryJob.claim_generation == generation,
                UserMemoryJob.lease_expires_at > now(),
            )
            .with_for_update(of=UserMemoryJob)
        )
        is not None
    )


def purge_expired_source_text(db: Session, *, at: datetime | None = None) -> int:
    """Clear bounded source snapshots once their retention window has passed.

    An already-verified active fact keeps its own minimal evidence, so this
    sweep never revokes memory that still has an independent valid source; it
    only removes the temporary copy of the original text.
    """

    moment = at or now()
    return _purge_source_text(db, at=moment, limit=100)


def expire_due_entries(db: Session, *, owner_user_id: str) -> int:
    """Expire due active facts and candidates before returning visible state."""

    state = lock_owned_state(db, owner_user_id)
    stamp = now()
    changed = db.execute(
        update(UserMemoryEntry)
        .where(
            UserMemoryEntry.owner_user_id == owner_user_id,
            UserMemoryEntry.status.in_([ACTIVE_STATUS, OPEN_CANDIDATE_STATUS]),
            UserMemoryEntry.expires_at.is_not(None),
            UserMemoryEntry.expires_at <= stamp,
        )
        .values(status="expired", version=UserMemoryEntry.version + 1, updated_at=stamp)
        .execution_options(synchronize_session="fetch")
    ).rowcount
    if changed:
        _bump_revision(db, state, stamp=stamp)
    return changed


def _lock_cleanup_state(db: Session, owner_user_id: str) -> UserMemoryState | None:
    # Concurrent sweepers may encounter owners in different phases. Never wait
    # on another owner's held lock while retaining earlier sweep locks.
    _begin_sqlite_write(db)
    owner = db.scalar(
        select(UserMemoryState.owner_user_id)
        .where(UserMemoryState.owner_user_id == owner_user_id)
        .with_for_update(of=UserMemoryState, skip_locked=True)
    )
    return lock_owned_state(db, owner) if owner is not None else None


def _purge_source_text(db: Session, *, at: datetime, limit: int) -> int:
    unfinished = (
        select(UserMemoryJob.id)
        .where(
            UserMemoryJob.owner_user_id == UserMemoryTurn.owner_user_id,
            UserMemoryJob.turn_id == UserMemoryTurn.id,
            UserMemoryJob.status.in_(("pending", "running")),
        )
        .exists()
    )
    completed = (
        select(UserMemoryJob.id)
        .where(
            UserMemoryJob.owner_user_id == UserMemoryTurn.owner_user_id,
            UserMemoryJob.turn_id == UserMemoryTurn.id,
            UserMemoryJob.status.in_(("succeeded", "failed", "cancelled")),
        )
        .exists()
    )
    rows = list(
        db.execute(
            select(UserMemoryTurn.owner_user_id, UserMemoryTurn.id)
            .where(
                UserMemoryTurn.source_text_safe.is_not(None),
                (UserMemoryTurn.source_text_expires_at <= at)
                | (UserMemoryTurn.status == "invalidated")
                | (
                    (UserMemoryTurn.channel.in_(("open", "wecom")))
                    & completed
                    & ~unfinished
                ),
            )
            .order_by(UserMemoryTurn.owner_user_id, UserMemoryTurn.created_at)
            .limit(limit)
        )
    )
    purged = 0
    for owner_id, turn_id in rows:
        if _lock_cleanup_state(db, owner_id) is None:
            continue
        turn = db.scalar(
            select(UserMemoryTurn)
            .where(
                UserMemoryTurn.owner_user_id == owner_id,
                UserMemoryTurn.id == turn_id,
            )
            .with_for_update(of=UserMemoryTurn)
            .execution_options(populate_existing=True)
        )
        if turn is None or turn.source_text_safe is None:
            continue
        expired = turn.source_text_expires_at is not None and as_utc(
            turn.source_text_expires_at
        ) <= as_utc(at)
        if (
            not expired
            and turn.status != "invalidated"
            and db.scalar(
                select(UserMemoryJob.id)
                .where(
                    UserMemoryJob.owner_user_id == owner_id,
                    UserMemoryJob.turn_id == turn_id,
                    UserMemoryJob.status.in_(("pending", "running")),
                )
                .limit(1)
            )
            is not None
        ):
            continue
        if expired:
            db.execute(
                update(UserMemoryJob)
                .where(
                    UserMemoryJob.owner_user_id == owner_id,
                    UserMemoryJob.turn_id == turn_id,
                    UserMemoryJob.status.in_(("pending", "running")),
                )
                .values(
                    status="cancelled",
                    last_error_safe="memory_source_invalid",
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=at,
                    completed_at=at,
                )
            )
        turn.source_text_safe = None
        turn.source_text_expires_at = None
        purged += 1
    db.flush()
    return purged


def cleanup_memory_data(
    db: Session,
    *,
    limit: int = 100,
    candidate_retention_days: int = 30,
    terminal_retention_days: int = 30,
    max_attempts: int = 3,
) -> dict[str, int]:
    """Bound each privacy sweep; retain identifiers and all forget barriers.

    Natural snapshot expiry does not invalidate an already verified fact.
    Terminal values and excerpts are erased, never the receipt/turn tombstones
    needed to reject a replay. The dispatcher owns commit/rollback.
    """

    limit = max(1, min(limit, 1000))
    stamp = now()
    candidate_cutoff = stamp - timedelta(days=max(1, candidate_retention_days))
    terminal_cutoff = stamp - timedelta(days=max(1, terminal_retention_days))
    entries = list(
        db.execute(
            select(UserMemoryEntry.owner_user_id, UserMemoryEntry.id)
            .where(
                (
                    (UserMemoryEntry.status == OPEN_CANDIDATE_STATUS)
                    & (
                        (UserMemoryEntry.created_at <= candidate_cutoff)
                        | (UserMemoryEntry.expires_at <= stamp)
                    )
                )
                | (
                    (UserMemoryEntry.status == ACTIVE_STATUS)
                    & (UserMemoryEntry.expires_at <= stamp)
                )
                | (
                    UserMemoryEntry.status.in_(TERMINAL_ENTRY_STATUSES)
                    & (UserMemoryEntry.updated_at <= terminal_cutoff)
                    & UserMemoryEntry.value_json.is_not(None)
                )
            )
            .order_by(UserMemoryEntry.owner_user_id, UserMemoryEntry.updated_at)
            .limit(limit)
        )
    )
    cleaned = 0
    for owner_id, memory_id in entries:
        state = _lock_cleanup_state(db, owner_id)
        if state is None:
            continue
        entry = get_owned_entry(db, owner_user_id=owner_id, memory_id=memory_id)
        due = entry.expires_at is not None and as_utc(entry.expires_at) <= stamp
        candidate_due = entry.status == OPEN_CANDIDATE_STATUS and (
            as_utc(entry.created_at) <= candidate_cutoff or due
        )
        terminal_due = (
            entry.status in TERMINAL_ENTRY_STATUSES
            and as_utc(entry.updated_at) <= terminal_cutoff
            and entry.value_json is not None
        )
        if not (
            candidate_due or (entry.status == ACTIVE_STATUS and due) or terminal_due
        ):
            continue
        if entry.status in (ACTIVE_STATUS, OPEN_CANDIDATE_STATUS):
            entry.status = "expired"
        entry.value_json = None
        entry.value_hash = None
        entry.version += 1
        entry.updated_at = stamp
        _wipe_evidence_quotes(db, owner_user_id=owner_id, memory_ids=[memory_id])
        _bump_revision(db, state, stamp=stamp)
        cleaned += 1
    invalid_evidence = list(
        db.execute(
            select(UserMemoryEvidence.owner_user_id, UserMemoryEvidence.id)
            .join(
                UserMemoryTurn,
                (UserMemoryTurn.owner_user_id == UserMemoryEvidence.owner_user_id)
                & (UserMemoryTurn.id == UserMemoryEvidence.turn_id),
            )
            .where(
                UserMemoryTurn.status == "invalidated",
                UserMemoryEvidence.quote.is_not(None),
            )
            .order_by(UserMemoryEvidence.owner_user_id, UserMemoryEvidence.id)
            .limit(limit)
        )
    )
    cleaned_evidence = 0
    for owner_id, evidence_id in invalid_evidence:
        if _lock_cleanup_state(db, owner_id) is None:
            continue
        db.execute(
            update(UserMemoryEvidence)
            .where(
                UserMemoryEvidence.owner_user_id == owner_id,
                UserMemoryEvidence.id == evidence_id,
            )
            .values(quote=None, quote_start=None, quote_end=None)
        )
        cleaned_evidence += 1
    exhausted = list(
        db.execute(
            select(
                UserMemoryJob.owner_user_id,
                UserMemoryJob.id,
                UserMemoryJob.claim_generation,
            )
            .where(
                UserMemoryJob.status.in_(("pending", "running")),
                UserMemoryJob.attempts >= max(1, max_attempts),
                UserMemoryJob.lease_expires_at <= stamp,
            )
            .order_by(UserMemoryJob.owner_user_id, UserMemoryJob.created_at)
            .limit(limit)
        )
    )
    failed_jobs = 0
    for owner_id, job_id, generation in exhausted:
        state = _lock_cleanup_state(db, owner_id)
        if state is None:
            continue
        changed = db.execute(
            update(UserMemoryJob)
            .where(
                UserMemoryJob.owner_user_id == owner_id,
                UserMemoryJob.id == job_id,
                UserMemoryJob.claim_generation == generation,
                UserMemoryJob.status.in_(("pending", "running")),
                UserMemoryJob.lease_expires_at <= stamp,
            )
            .values(
                status="failed",
                last_error_safe="memory_attempts_exhausted",
                lease_owner=None,
                lease_expires_at=None,
                completed_at=stamp,
                updated_at=stamp,
            )
            .execution_options(synchronize_session="fetch")
        ).rowcount
        if changed:
            _bump_revision(db, state, stamp=stamp)
            failed_jobs += changed
    abandoned = list(
        db.execute(
            select(UserMemoryOperation.owner_user_id, UserMemoryOperation.id)
            .outerjoin(
                UserMemoryJob,
                (UserMemoryJob.owner_user_id == UserMemoryOperation.owner_user_id)
                & (UserMemoryJob.id == UserMemoryOperation.job_id),
            )
            .where(
                UserMemoryOperation.kind == "extract_attempt",
                UserMemoryOperation.status.in_(("pending", "running")),
                UserMemoryJob.id.is_(None)
                | (UserMemoryJob.status != "running")
                | (UserMemoryJob.lease_expires_at <= stamp)
                | (UserMemoryOperation.created_at < UserMemoryJob.started_at),
            )
            .order_by(UserMemoryOperation.owner_user_id, UserMemoryOperation.created_at)
            .limit(limit)
        )
    )
    failed_operations = 0
    for owner_id, operation_id in abandoned:
        if _lock_cleanup_state(db, owner_id) is None:
            continue
        operation = db.scalar(
            select(UserMemoryOperation)
            .where(
                UserMemoryOperation.owner_user_id == owner_id,
                UserMemoryOperation.id == operation_id,
            )
            .execution_options(populate_existing=True)
        )
        if operation is None or operation.status not in ("pending", "running"):
            continue
        job = (
            db.scalar(
                select(UserMemoryJob)
                .where(
                    UserMemoryJob.owner_user_id == owner_id,
                    UserMemoryJob.id == operation.job_id,
                )
                .execution_options(populate_existing=True)
            )
            if operation.job_id
            else None
        )
        if (
            job is not None
            and job.status == "running"
            and job.lease_expires_at is not None
            and as_utc(job.lease_expires_at) > stamp
            and operation.request_id == f"{job.id}:{job.claim_generation}"
        ):
            continue
        operation.status = "failed"
        operation.error_code = "memory_outcome_unknown"
        operation.completed_at = stamp
        failed_operations += 1
    db.flush()
    return {
        "sources": _purge_source_text(db, at=stamp, limit=limit),
        "entries": cleaned,
        "evidence": cleaned_evidence,
        "jobs": failed_jobs,
        "operations": failed_operations,
    }
