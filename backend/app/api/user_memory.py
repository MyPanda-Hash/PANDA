"""Management API for the signed-in employee's own memory.

Every route resolves its owner from ``current_user``. There is no ``user_id``
parameter and no administrator bypass: an admin has no route from here into
somebody else's memory, and a missing row and another employee's row answer 404
identically so a probe cannot learn that a row exists.

Confirming, correcting and forgetting through these routes is the authorisation
boundary. A generic capability ``request_id`` from the model is idempotency, not
the employee's consent, and it never reaches these handlers as approval.
"""

from __future__ import annotations

import logging

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from jwt.exceptions import PyJWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.database import transaction
from app.core.security import as_utc, now
from app.models.auth import User
from app.models.chat import ChatSession
from app.models.user_memory import UserMemoryEntry, UserMemoryOperation, UserMemoryTurn
from app.repositories import user_memory as repo
from app.schemas.user_memory import (
    MemoryClearRequest,
    MemoryConfirmRequest,
    MemoryCreateRequest,
    MemoryEvidenceListResponse,
    MemoryEvidenceResponse,
    MemoryForgetRequest,
    MemoryListResponse,
    MemoryMutationResponse,
    MemoryRejectRequest,
    MemorySettingsResponse,
    MemorySettingsUpdateRequest,
    MemoryTurnListResponse,
    MemoryTurnStatusResponse,
    MemoryUpdateRequest,
    MemoryValueError,
    normalize_memory_value,
)
from app.schemas.user_memory import StrictModel
from app.services import user_memory as service
from app.services.user_memory import entry_response, operation_receipt
from app.services.agent_capability import (
    CapabilityGrantError,
    decode_capability_token,
    require_grant_user,
    validate_claims,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory"])


def _fail(code: str) -> HTTPException:
    status, message = service.describe_error(code)  # type: ignore[arg-type]
    return HTTPException(
        status_code=status, detail={"error_code": code, "message": message}
    )


def _raise(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return _fail(code)
    logger.warning(
        "Unexpected personal memory failure error_type=%s", type(exc).__name__
    )
    return _fail("memory_unavailable")


# ---------------------------------------------------------------------------
# Settings.
# ---------------------------------------------------------------------------


@router.get("/me/memories/settings", response_model=MemorySettingsResponse)
def get_memory_settings(user: User = Depends(current_user)) -> MemorySettingsResponse:
    with transaction() as db:
        repo.expire_due_entries(db, owner_user_id=user.id)
        state = repo.get_or_create_state(db, user.id)
        return MemorySettingsResponse(
            use_enabled=state.use_enabled,
            learning_mode=state.learning_mode,  # type: ignore[arg-type]
            revision=state.revision,
        )


@router.patch("/me/memories/settings", response_model=MemorySettingsResponse)
def update_memory_settings(
    payload: MemorySettingsUpdateRequest, user: User = Depends(current_user)
) -> MemorySettingsResponse:
    with transaction() as db:
        operation, replay = _claim(
            db,
            user.id,
            kind="settings",
            request_id=payload.request_id,
            body=payload.model_dump(exclude={"request_id"}),
        )
        if replay:
            state = repo.get_or_create_state(db, user.id)
            return MemorySettingsResponse(
                use_enabled=state.use_enabled,
                learning_mode=state.learning_mode,  # type: ignore[arg-type]
                revision=state.revision,
                receipt=operation_receipt(db, operation, revision=state.revision),
            )
        try:
            state = repo.update_settings(
                db,
                owner_user_id=user.id,
                expected_revision=payload.expected_revision,
                use_enabled=payload.use_enabled,
                learning_mode=payload.learning_mode,
            )
        except Exception as exc:
            raise _raise(exc) from exc
        repo.complete_operation(
            db,
            operation=operation,
            status="succeeded",
            result_revision=state.revision,
        )
        return MemorySettingsResponse(
            use_enabled=state.use_enabled,
            learning_mode=state.learning_mode,  # type: ignore[arg-type]
            revision=state.revision,
            receipt=operation_receipt(db, operation, revision=state.revision),
        )


# ---------------------------------------------------------------------------
# Listing and sources.
# ---------------------------------------------------------------------------


@router.get("/me/memories", response_model=MemoryListResponse)
def list_memories(
    status: str | None = Query(default=None, max_length=32),
    key_prefix: str | None = Query(default=None, max_length=64),
    cursor: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=50),
    user: User = Depends(current_user),
) -> MemoryListResponse:
    with transaction() as db:
        repo.reconcile_equivalent_candidates(db, owner_user_id=user.id)
        state = repo.get_or_create_state(db, user.id)
        entries = repo.list_owned_entries(
            db,
            owner_user_id=user.id,
            statuses=[status] if status else ["active", "candidate"],
            key_prefix=key_prefix,
            cursor=cursor,
            limit=limit,
        )
        items = [entry_response(db, entry) for entry in entries]
        return MemoryListResponse(
            items=items,
            revision=state.revision,
            next_cursor=items[-1].id if len(items) == limit else None,
        )


@router.get(
    "/me/memories/{memory_id}/sources", response_model=MemoryEvidenceListResponse
)
def list_memory_sources(
    memory_id: str,
    cursor: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=10, ge=1, le=10),
    user: User = Depends(current_user),
) -> MemoryEvidenceListResponse:
    with transaction() as db:
        try:
            repo.get_owned_entry(db, owner_user_id=user.id, memory_id=memory_id)
        except Exception as exc:
            raise _raise(exc) from exc
        rows = repo.list_entry_evidence(
            db, owner_user_id=user.id, memory_id=memory_id, cursor=cursor, limit=limit
        )
        items = [
            MemoryEvidenceResponse(
                id=evidence.id,
                turn_id=evidence.turn_id,
                channel=turn.channel,  # type: ignore[arg-type]
                evidence_kind=evidence.evidence_kind,  # type: ignore[arg-type]
                quote=evidence.quote,
                created_at=evidence.created_at,
                # The UI says plainly when a source is gone rather than showing
                # an excerpt it can no longer verify.
                source_available=service.resolve_turn_source_text(db, turn) is not None,
            )
            for evidence, turn in rows
        ]
        return MemoryEvidenceListResponse(
            items=items,
            next_cursor=items[-1].id if len(items) == limit else None,
        )


@router.get("/me/memories/turns", response_model=MemoryTurnListResponse)
def list_memory_turns(
    session_id: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(current_user),
) -> MemoryTurnListResponse:
    """Recover committed notices after reconnect or refresh, without local text."""
    with transaction() as db:
        session = db.scalar(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.owner_user_id == user.id,
            )
        )
        if session is None:
            raise _fail("memory_not_found")
        turn_ids = db.scalars(
            select(UserMemoryTurn.id)
            .where(
                UserMemoryTurn.owner_user_id == user.id,
                UserMemoryTurn.chat_session_id == session.id,
                UserMemoryTurn.channel == "web",
            )
            .order_by(UserMemoryTurn.created_at.desc(), UserMemoryTurn.id.desc())
            .limit(limit)
        ).all()
        return MemoryTurnListResponse(
            items=[
                service.read_turn_status(db, owner_user_id=user.id, turn_id=turn_id)
                for turn_id in turn_ids
            ],
            revision=repo.get_or_create_state(db, user.id).revision,
        )


@router.get("/me/memories/turns/{turn_id}", response_model=MemoryTurnStatusResponse)
def get_turn_status(
    turn_id: str, user: User = Depends(current_user)
) -> MemoryTurnStatusResponse:
    """What the chat UI reads back for one turn.

    This is the only authority for "saved". A lost SSE ``done`` never revokes a
    memory that was already committed, and assistant prose is never parsed to
    decide the state.
    """

    with transaction() as db:
        try:
            return service.read_turn_status(db, owner_user_id=user.id, turn_id=turn_id)
        except Exception as exc:
            raise _raise(exc) from exc


# ---------------------------------------------------------------------------
# Mutations. Each one is idempotent through its receipt.
# ---------------------------------------------------------------------------


def _claim(
    db: Session,
    owner_user_id: str,
    *,
    kind: str,
    request_id: str,
    body: dict,
    memory_id: str | None = None,
) -> tuple[UserMemoryOperation, bool]:
    try:
        return repo.claim_operation(
            db,
            owner_user_id=owner_user_id,
            kind=kind,
            request_id=request_id,
            payload_hash=repo.request_hash(body),
            memory_id=memory_id,
        )
    except Exception as exc:
        raise _raise(exc) from exc


def _mutation_response(
    db: Session,
    *,
    entry: UserMemoryEntry | None,
    operation: UserMemoryOperation,
    owner_user_id: str,
) -> MemoryMutationResponse:
    state = repo.get_or_create_state(db, owner_user_id)
    return MemoryMutationResponse(
        entry=entry_response(db, entry) if entry is not None else None,
        receipt=operation_receipt(db, operation, revision=state.revision),
        revision=state.revision,
    )


def _replayed(
    db: Session, operation: UserMemoryOperation, owner_user_id: str
) -> MemoryMutationResponse:
    """Answer a retry from the stored receipt without repeating the change.

    A receipt whose memory has since been forgotten reports the revoked state:
    it does not hand back the deleted value and does not bring it back.
    """

    entry = None
    if operation.result_memory_id and not repo.receipt_is_revoked(db, operation):
        try:
            entry = repo.get_owned_entry(
                db, owner_user_id=owner_user_id, memory_id=operation.result_memory_id
            )
        except repo.MemoryNotFoundError:
            entry = None
    return _mutation_response(
        db, entry=entry, operation=operation, owner_user_id=owner_user_id
    )


@router.post("/me/memories", response_model=MemoryMutationResponse)
def create_memory(
    payload: MemoryCreateRequest, user: User = Depends(current_user)
) -> MemoryMutationResponse:
    """An explicit save from the management UI, which becomes a manual source."""

    with transaction() as db:
        operation, replay = _claim(
            db,
            user.id,
            kind="create",
            request_id=payload.request_id,
            body=payload.model_dump(exclude={"request_id"}),
        )
        if replay:
            return _replayed(db, operation, user.id)
        try:
            value = normalize_memory_value(payload.key, payload.value)
        except MemoryValueError as exc:
            raise _fail(exc.code) from exc
        if service.contains_secret(value):
            raise _fail("memory_value_invalid")
        try:
            turn = repo.register_turn(
                db,
                owner_user_id=user.id,
                channel="manual",
                request_key=f"manual:create:{payload.request_id}",
                raw_text=f"{payload.key}={value}",
            )
            entry = repo.propose_entry(
                db,
                owner_user_id=user.id,
                key=payload.key,
                value=value,
                origin="manual",
                turn_id=turn.id,
                quote=None,
                quote_start=None,
                quote_end=None,
                evidence_kind="manual_confirmation",
                expires_at=service.default_expiry(payload.key),
            )
            if entry.status == "candidate":
                entry = repo.confirm_entry(
                    db,
                    owner_user_id=user.id,
                    memory_id=entry.id,
                    expected_version=entry.version,
                )
        except Exception as exc:
            raise _raise(exc) from exc
        state = repo.get_or_create_state(db, user.id)
        repo.complete_operation(
            db,
            operation=operation,
            status="succeeded",
            result_memory_id=entry.id,
            result_revision=state.revision,
        )
        return _mutation_response(
            db, entry=entry, operation=operation, owner_user_id=user.id
        )


@router.patch("/me/memories/{memory_id}", response_model=MemoryMutationResponse)
def update_memory(
    memory_id: str,
    payload: MemoryUpdateRequest,
    user: User = Depends(current_user),
) -> MemoryMutationResponse:
    with transaction() as db:
        operation, replay = _claim(
            db,
            user.id,
            kind="update",
            request_id=payload.request_id,
            body=payload.model_dump(exclude={"request_id"}) | {"memory_id": memory_id},
            memory_id=memory_id,
        )
        if replay:
            return _replayed(db, operation, user.id)
        try:
            existing = repo.get_owned_entry(
                db, owner_user_id=user.id, memory_id=memory_id
            )
            value = normalize_memory_value(existing.key, payload.value)
        except MemoryValueError as exc:
            raise _fail(exc.code) from exc
        except Exception as exc:
            raise _raise(exc) from exc
        if service.contains_secret(value):
            raise _fail("memory_value_invalid")
        try:
            turn = repo.register_turn(
                db,
                owner_user_id=user.id,
                channel="manual",
                request_key=f"manual:update:{payload.request_id}",
                raw_text=f"{existing.key}={value}",
            )
            entry = repo.update_entry(
                db,
                owner_user_id=user.id,
                memory_id=memory_id,
                value=value,
                expected_version=payload.expected_version,
                turn_id=turn.id,
            )
        except Exception as exc:
            raise _raise(exc) from exc
        state = repo.get_or_create_state(db, user.id)
        repo.complete_operation(
            db,
            operation=operation,
            status="succeeded",
            result_memory_id=entry.id,
            result_revision=state.revision,
        )
        return _mutation_response(
            db, entry=entry, operation=operation, owner_user_id=user.id
        )


@router.post("/me/memories/{memory_id}/confirm", response_model=MemoryMutationResponse)
def confirm_memory(
    memory_id: str,
    payload: MemoryConfirmRequest,
    user: User = Depends(current_user),
) -> MemoryMutationResponse:
    """The employee's own confirmation - the authorisation the model cannot give."""

    with transaction() as db:
        operation, replay = _claim(
            db,
            user.id,
            kind="confirm",
            request_id=payload.request_id,
            body=payload.model_dump(exclude={"request_id"}) | {"memory_id": memory_id},
            memory_id=memory_id,
        )
        if replay:
            return _replayed(db, operation, user.id)
        try:
            entry = repo.confirm_entry(
                db,
                owner_user_id=user.id,
                memory_id=memory_id,
                expected_version=payload.expected_version,
                expected_active_version=payload.expected_active_version,
            )
            # Confirming creates an independent manual source, so deleting the
            # original chat later does not pretend this never happened.
            turn = repo.register_turn(
                db,
                owner_user_id=user.id,
                channel="manual",
                request_key=f"manual:confirm:{payload.request_id}",
                raw_text=f"confirm:{memory_id}",
            )
            repo.add_evidence(
                db,
                owner_user_id=user.id,
                memory_id=entry.id,
                turn_id=turn.id,
                quote=None,
                quote_start=None,
                quote_end=None,
                evidence_kind="manual_confirmation",
            )
        except Exception as exc:
            raise _raise(exc) from exc
        state = repo.get_or_create_state(db, user.id)
        repo.complete_operation(
            db,
            operation=operation,
            status="succeeded",
            result_memory_id=entry.id,
            result_revision=state.revision,
        )
        return _mutation_response(
            db, entry=entry, operation=operation, owner_user_id=user.id
        )


@router.post("/me/memories/{memory_id}/reject", response_model=MemoryMutationResponse)
def reject_memory(
    memory_id: str,
    payload: MemoryRejectRequest,
    user: User = Depends(current_user),
) -> MemoryMutationResponse:
    with transaction() as db:
        operation, replay = _claim(
            db,
            user.id,
            kind="reject",
            request_id=payload.request_id,
            body=payload.model_dump(exclude={"request_id"}) | {"memory_id": memory_id},
            memory_id=memory_id,
        )
        if replay:
            return _replayed(db, operation, user.id)
        try:
            entry = repo.reject_entry(
                db,
                owner_user_id=user.id,
                memory_id=memory_id,
                expected_version=payload.expected_version,
            )
        except Exception as exc:
            raise _raise(exc) from exc
        state = repo.get_or_create_state(db, user.id)
        repo.complete_operation(
            db,
            operation=operation,
            status="succeeded",
            result_memory_id=entry.id,
            result_revision=state.revision,
        )
        return _mutation_response(
            db, entry=entry, operation=operation, owner_user_id=user.id
        )


@router.post("/me/memories/{memory_id}/forget", response_model=MemoryMutationResponse)
def forget_memory(
    memory_id: str,
    payload: MemoryForgetRequest,
    user: User = Depends(current_user),
) -> MemoryMutationResponse:
    """Revoke one key. This does not delete the original chat, and says so."""

    with transaction() as db:
        operation, replay = _claim(
            db,
            user.id,
            kind="forget",
            request_id=payload.request_id,
            body=payload.model_dump(exclude={"request_id"}) | {"memory_id": memory_id},
            memory_id=memory_id,
        )
        if replay:
            return _replayed(db, operation, user.id)
        try:
            entry = repo.forget_entry(
                db,
                owner_user_id=user.id,
                memory_id=memory_id,
                expected_version=payload.expected_version,
            )
        except Exception as exc:
            raise _raise(exc) from exc
        state = repo.get_or_create_state(db, user.id)
        repo.complete_operation(
            db,
            operation=operation,
            status="succeeded",
            result_memory_id=entry.id,
            result_revision=state.revision,
        )
        return _mutation_response(
            db, entry=entry, operation=operation, owner_user_id=user.id
        )


@router.post("/me/memories/clear", response_model=MemorySettingsResponse)
def clear_memories(
    payload: MemoryClearRequest, user: User = Depends(current_user)
) -> MemorySettingsResponse:
    with transaction() as db:
        operation, replay = _claim(
            db,
            user.id,
            kind="clear",
            request_id=payload.request_id,
            body=payload.model_dump(exclude={"request_id"}),
        )
        if not replay:
            try:
                repo.clear_entries(
                    db,
                    owner_user_id=user.id,
                    expected_revision=payload.expected_revision,
                )
            except Exception as exc:
                raise _raise(exc) from exc
            state = repo.get_or_create_state(db, user.id)
            repo.complete_operation(
                db,
                operation=operation,
                status="succeeded",
                result_revision=state.revision,
            )
        state = repo.get_or_create_state(db, user.id)
        return MemorySettingsResponse(
            use_enabled=state.use_enabled,
            learning_mode=state.learning_mode,  # type: ignore[arg-type]
            revision=state.revision,
            receipt=operation_receipt(db, operation, revision=state.revision),
        )


# ---------------------------------------------------------------------------
# Internal snapshot route for the Pi extension.
#
# This exists so the snapshot never travels through the generic capability
# tool-output path. Anything returned from a tool call is written into the Pi
# transcript, which would leave a second, uncontrolled copy of the employee's
# private file on disk. The extension fetches the snapshot here and injects it
# through the `context` hook, where it lives for exactly one model call.
#
# It is a separate router rather than another `/{tool_name}` handler so it can
# never be reached by guessing a tool name.
# ---------------------------------------------------------------------------

internal_router = APIRouter(prefix="/internal/v1/memory", tags=["internal-memory"])


class MemorySnapshotRequest(StrictModel):
    """What the extension already holds, so an unchanged state costs nothing."""

    known_revision: int | None = None
    known_epoch: int | None = None


@internal_router.post("/snapshot")
def read_memory_snapshot(
    body: MemorySnapshotRequest,
    authorization: Annotated[str | None, Header()] = None,
    x_nexus_agent_id: Annotated[str | None, Header()] = None,
) -> dict:
    """Serve the bounded snapshot for one model call.

    Re-validated on every request rather than trusted from the grant: the token
    was signed before the run started, and the employee may have corrected,
    forgotten or switched memory off since. A caller that gets ``use_enabled``
    false or a 403 must drop what it cached and continue with no memory - there
    is no stale fallback.
    """

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing capability grant")
    try:
        claims = decode_capability_token(authorization[7:].strip())
    except (PyJWTError, RuntimeError):
        raise HTTPException(
            status_code=401, detail="invalid capability grant"
        ) from None

    with transaction() as db:
        try:
            # Re-checks the owner is still active and that auth_version has not
            # moved, so a password change revokes this the same as any tool.
            user = require_grant_user(db, claims)
            validate_claims(
                claims,
                tool_name="get_my_memory",
                expected_agent_id=(x_nexus_agent_id or "").strip() or None,
            )
        except CapabilityGrantError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        snapshot = service.build_memory_snapshot(db, owner_user_id=user.id, user=user)
        unchanged = (
            body.known_revision == snapshot.revision
            and body.known_epoch == snapshot.epoch
            and snapshot.use_enabled
            # Revision equality is not enough on its own: an entry can lapse
            # between two calls without any write happening.
            and (snapshot.valid_until is None or as_utc(snapshot.valid_until) > now())
        )
        if unchanged:
            return {
                "ok": True,
                "unchanged": True,
                "revision": snapshot.revision,
                "epoch": snapshot.epoch,
                "use_enabled": True,
                "addressing_name": snapshot.addressing_name,
            }
        return {
            "ok": True,
            "unchanged": False,
            "revision": snapshot.revision,
            "epoch": snapshot.epoch,
            "use_enabled": snapshot.use_enabled,
            "addressing_name": snapshot.addressing_name,
            "valid_until": (
                snapshot.valid_until.isoformat() if snapshot.valid_until else None
            ),
            "entries": [
                {
                    "key": item.key,
                    "value": item.value,
                    "version": item.version,
                    "expires_at": (
                        item.expires_at.isoformat() if item.expires_at else None
                    ),
                }
                for item in snapshot.entries
            ],
        }
