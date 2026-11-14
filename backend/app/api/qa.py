from __future__ import annotations

import json
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import current_user
from app.core.database import transaction
from app.models.auth import User
from app.models.documents import Document, DocumentVersion
from app.models.qa import Citation, QaSession, QaTurn
from app.repositories import qa
from app.schemas.qa import (
    CitationResponse,
    CitationSourceResponse,
    QaEventResponse,
    QaSessionCreateRequest,
    QaSessionResponse,
    QaSessionSummaryResponse,
    QaTurnCreateRequest,
    QaTurnResponse,
)
from app.services.authorization import PermissionService
from app.services.qa_service import QaServiceError, generate_answer

router = APIRouter(prefix="/qa", tags=["qa"])

_ZERO_USAGE = {
    "provider": "deterministic_document_version_metadata",
    "input_tokens": 0,
    "input_uncached_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "cost": "0",
    "latency_ms": 0,
}


class QaBudgetExceeded(Exception):
    """Raised by the budget boundary without exposing provider or document details."""


def preflight_budget(*_args: object, **_kwargs: object) -> None:
    """The deterministic zero-usage provider has no billable budget to consume yet."""


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="资源不存在")


def session_response(session: QaSession) -> QaSessionResponse:
    return QaSessionResponse(
        id=session.id,
        space_ids=qa.load_snapshot(session.selected_space_ids),
        scope_version=qa.load_snapshot(session.scope_version),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def session_summary_response(db, session: QaSession) -> QaSessionSummaryResponse:
    turns = qa.list_turns_for_session(db, session.id)
    last_turn = turns[-1] if turns else None
    return QaSessionSummaryResponse(
        id=session.id,
        space_ids=qa.load_snapshot(session.selected_space_ids),
        turn_count=len(turns),
        last_question=last_turn.question if last_turn else None,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def citation_response(db, citation: Citation) -> CitationResponse:
    document = db.get(Document, citation.document_id)
    version = db.get(DocumentVersion, citation.document_version_id)
    if not document or not version or version.document_id != document.id:
        raise RuntimeError("citation references a missing document version")
    bbox = None
    if citation.bbox_json:
        try:
            parsed = json.loads(citation.bbox_json)
            if isinstance(parsed, (list, tuple)) and len(parsed) == 4:
                values = tuple(float(item) for item in parsed)
                if all(0 <= item <= 1 for item in values) and values[2] > values[0] and values[3] > values[1]:
                    bbox = values
        except (TypeError, ValueError, json.JSONDecodeError):
            bbox = None
    page_number = citation.page_number if citation.page_number and citation.page_number > 0 else None
    location_label = (
        f"第 {page_number} 页" if page_number is not None else None
    )
    return CitationResponse(
        id=citation.id,
        turn_id=citation.turn_id,
        document_id=document.id,
        document_version_id=version.id,
        document_name=document.name,
        version_number=version.version_number,
        page_or_section=citation.page_or_section,
        excerpt=citation.excerpt,
        support_state=cast(Any, citation.support_state),
        location_label=location_label,
        page_number=page_number,
        page_block_id=citation.page_block_id,
        bbox=bbox,
    )


def _terminal_events(
    status: str, verification_summary: dict[str, Any], usage: dict[str, Any]
) -> list[dict[str, Any]]:
    retrieval_summary = {"provider": "permission_safe_snapshot", "candidate_count": 0}
    return [
        {"type": "status", "status": "retrieving"},
        {"type": "retrieval_summary", "retrieval_summary": retrieval_summary},
        {"type": "verification_summary", "verification_summary": verification_summary},
        {"type": "usage", "usage": usage},
        {"type": "status", "status": status},
    ]


def _terminal_turn(
    db,
    session: QaSession,
    question: str,
    *,
    status: str,
    reason: str,
) -> QaTurn:
    usage = dict(_ZERO_USAGE)
    verification_summary = {"state": status, "reason": reason}
    return qa.create_turn(
        db,
        session=session,
        question=question,
        status=status,
        retrieval_summary={
            "provider": "permission_safe_snapshot",
            "candidate_count": 0,
        },
        answer=None,
        verification_summary=verification_summary,
        usage=usage,
        events=_terminal_events(status, verification_summary, usage),
        evidence=[],
    )


def _persist_safe_terminal(db, turn: QaTurn, *, reason: str) -> QaTurn:
    usage = dict(_ZERO_USAGE)
    verification_summary = {"state": "source_permission_denied", "reason": reason}
    return qa.transition_turn_to_terminal(
        db,
        turn=turn,
        status="source_permission_denied",
        retrieval_summary={
            "provider": "permission_safe_snapshot",
            "candidate_count": 0,
        },
        verification_summary=verification_summary,
        usage=usage,
        events=_terminal_events(
            "source_permission_denied", verification_summary, usage
        ),
    )


def _citation_is_currently_visible(
    db, user: User, turn: QaTurn, citation: Citation
) -> bool:
    document = db.get(Document, citation.document_id)
    version = db.get(DocumentVersion, citation.document_version_id)
    if (
        not document
        or document.status != "active"
        or not version
        or version.document_id != document.id
        or version.processing_stage != "ready"
        or not version.queryable
        or document.space_id not in qa.load_snapshot(turn.selected_space_ids)
    ):
        return False
    try:
        PermissionService.require_space_access(
            db, user, document.space_id, minimum_role="reader"
        )
    except HTTPException:
        return False
    return True


def turn_response(db, turn: QaTurn, user: User) -> QaTurnResponse:
    stored_citations = qa.citations_for_turn(db, turn.id)
    if any(
        not _citation_is_currently_visible(db, user, turn, item)
        for item in stored_citations
    ):
        turn = _persist_safe_terminal(db, turn, reason="citation_no_longer_available")
        stored_citations = []
    citations = [citation_response(db, item) for item in stored_citations]
    citation_by_id = {item.id: item for item in citations}
    events: list[QaEventResponse] = []
    for event in qa.load_snapshot(turn.events):
        event = {**event}
        citation_id = event.pop("citation_id", None)
        if citation_id:
            event["citation"] = citation_by_id[citation_id]
        events.append(QaEventResponse(**event))
    return QaTurnResponse(
        id=turn.id,
        session_id=turn.session_id,
        question=turn.question,
        space_ids=qa.load_snapshot(turn.selected_space_ids),
        scope_version=qa.load_snapshot(turn.scope_version),
        status=cast(Any, turn.status),
        retrieval_summary=qa.load_snapshot(turn.retrieval_summary),
        retrieval_trace=(
            qa.load_snapshot(turn.retrieval_summary).get("retrieval_trace")
            if isinstance(qa.load_snapshot(turn.retrieval_summary), dict)
            else None
        ),
        answer=turn.answer,
        citations=citations,
        verification_summary=qa.load_snapshot(turn.verification_summary),
        usage=qa.load_snapshot(turn.usage),
        events=events,
        created_at=turn.created_at,
        completed_at=turn.completed_at,
    )


def _current_scope(db, user: User, session: QaSession) -> list[str]:
    space_ids = qa.load_snapshot(session.selected_space_ids)
    for space_id in space_ids:
        PermissionService.require_space_access(
            db, user, space_id, minimum_role="reader"
        )
    return space_ids


def _deterministic_turn(
    db,
    session: QaSession,
    question: str,
    space_ids: list[str],
    *,
    user_id: str,
) -> QaTurn:
    """Generate a QA turn using LLM-based RAG pipeline.

    Delegates to QaService.generate_answer for evidence retrieval, LLM generation,
    citation parsing, and quality verification. Falls back to metadata-only responses
    when no LLM endpoint is configured or the LLM call fails.
    """
    try:
        (
            answer,
            status,
            retrieval_summary,
            verification_summary,
            usage,
            events,
            evidence,
        ) = generate_answer(db, question, space_ids, user_id=user_id)
    except QaServiceError:
        # Last-resort fallback: if even the service layer itself errors,
        # return a knowledge_gap terminal turn so the user sees a clean state.
        usage = dict(_ZERO_USAGE)
        verification_summary = {"state": "knowledge_gap", "reason": "qa_service_error"}
        return qa.create_turn(
            db,
            session=session,
            question=question,
            status="knowledge_gap",
            retrieval_summary={"provider": "qa_service_error", "candidate_count": 0},
            answer=None,
            verification_summary=verification_summary,
            usage=usage,
            events=_terminal_events("knowledge_gap", verification_summary, usage),
            evidence=[],
        )

    return qa.create_turn(
        db,
        session=session,
        question=question,
        status=status,
        retrieval_summary=retrieval_summary,
        answer=answer,
        verification_summary=verification_summary,
        usage=usage,
        events=events,
        evidence=evidence,
    )


@router.get("/sessions", response_model=list[QaSessionSummaryResponse])
def list_sessions(user: User = Depends(current_user)) -> list[QaSessionSummaryResponse]:
    with transaction() as db:
        return [session_summary_response(db, s) for s in qa.list_sessions(db, user.id)]


@router.delete("/sessions/{session_id}", status_code=204, response_model=None)
def delete_session(session_id: str, user: User = Depends(current_user)) -> None:
    with transaction() as db:
        if not qa.delete_owned_session(db, session_id, user.id):
            raise _not_found()


@router.get("/sessions/{session_id}/turns", response_model=list[QaTurnResponse])
def list_session_turns(
    session_id: str, user: User = Depends(current_user)
) -> list[QaTurnResponse]:
    with transaction() as db:
        session = qa.get_owned_session(db, session_id, user.id)
        if not session:
            raise _not_found()
        return [
            turn_response(db, t, user)
            for t in qa.list_turns_for_session(db, session_id)
        ]


@router.post("/sessions", response_model=QaSessionResponse, status_code=201)
def create_session(
    payload: QaSessionCreateRequest, user: User = Depends(current_user)
) -> QaSessionResponse:
    space_ids = list(dict.fromkeys(payload.space_ids))
    if len(space_ids) != len(payload.space_ids):
        raise HTTPException(status_code=422, detail="知识范围不能重复")
    with transaction() as db:
        spaces = [
            PermissionService.require_space_access(
                db, user, space_id, minimum_role="reader"
            )
            for space_id in space_ids
        ]
        session = qa.create_session(
            db,
            owner_user_id=user.id,
            space_ids=space_ids,
            scope_version={space.id: space.scope_version for space in spaces},
        )
        return session_response(session)


@router.post(
    "/sessions/{session_id}/turns", response_model=QaTurnResponse, status_code=201
)
def create_turn(
    session_id: str,
    payload: QaTurnCreateRequest,
    user: User = Depends(current_user),
) -> QaTurnResponse:
    with transaction() as db:
        session = qa.get_owned_session(db, session_id, user.id)
        if not session:
            raise _not_found()
        question = payload.question.strip()
        try:
            space_ids = _current_scope(db, user, session)
        except HTTPException as exc:
            if exc.status_code not in (403, 404):
                raise
            turn = _terminal_turn(
                db,
                session,
                question,
                status="source_permission_denied",
                reason="scope_no_longer_available",
            )
            return turn_response(db, turn, user)
        try:
            preflight_budget(db, user, session, question)
        except QaBudgetExceeded:
            turn = _terminal_turn(
                db,
                session,
                question,
                status="budget_exceeded",
                reason="budget_preflight_denied",
            )
            return turn_response(db, turn, user)
        turn = _deterministic_turn(db, session, question, space_ids, user_id=user.id)
        return turn_response(db, turn, user)


@router.get("/turns/{turn_id}", response_model=QaTurnResponse)
def get_turn(turn_id: str, user: User = Depends(current_user)) -> QaTurnResponse:
    with transaction() as db:
        turn = qa.get_owned_turn(db, turn_id, user.id)
        if not turn:
            raise _not_found()
        return turn_response(db, turn, user)


@router.get("/citations/{citation_id}/source", response_model=CitationSourceResponse)
def open_citation_source(
    citation_id: str, user: User = Depends(current_user)
) -> CitationSourceResponse:
    with transaction() as db:
        citation = qa.get_citation(db, citation_id)
        if not citation or citation.turn.owner_user_id != user.id:
            raise _not_found()
        if not _citation_is_currently_visible(db, user, citation.turn, citation):
            raise _not_found()
        document = db.get(Document, citation.document_id)
        version = db.get(DocumentVersion, citation.document_version_id)
        if not document or not version:
            raise _not_found()
        response = citation_response(db, citation)
        return CitationSourceResponse(
            citation=response,
            document_id=document.id,
            document_version_id=version.id,
            page_or_section=citation.page_or_section,
            excerpt=citation.excerpt,
            page_image_path=(
                f"/documents/{document.id}/versions/{version.id}/pages/{citation.page_number}/image"
                if citation.page_number and citation.page_number > 0
                else None
            ),
        )
