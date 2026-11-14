from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.documents import Document, DocumentVersion
from app.models.qa import Citation, QaSession, QaTurn


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def load_snapshot(value: str) -> Any:
    return json.loads(value)


def create_session(
    db: Session,
    *,
    owner_user_id: str,
    space_ids: list[str],
    scope_version: dict[str, int],
) -> QaSession:
    created_at = now()
    session = QaSession(
        id=f"qas_{new_token(10)}",
        owner_user_id=owner_user_id,
        selected_space_ids=_dump(space_ids),
        scope_version=_dump(scope_version),
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(session)
    db.flush()
    return session


def get_owned_session(
    db: Session, session_id: str, owner_user_id: str
) -> QaSession | None:
    return db.scalar(
        select(QaSession).where(
            QaSession.id == session_id, QaSession.owner_user_id == owner_user_id
        )
    )


def list_sessions(db: Session, owner_user_id: str) -> list[QaSession]:
    return list(
        db.scalars(
            select(QaSession)
            .where(QaSession.owner_user_id == owner_user_id)
            .order_by(QaSession.updated_at.desc())
        )
    )


def delete_owned_session(db: Session, session_id: str, owner_user_id: str) -> bool:
    session = get_owned_session(db, session_id, owner_user_id)
    if not session:
        return False
    db.delete(session)
    return True


def list_turns_for_session(db: Session, session_id: str) -> list[QaTurn]:
    return list(
        db.scalars(
            select(QaTurn)
            .where(QaTurn.session_id == session_id)
            .order_by(QaTurn.created_at)
        )
    )


def ready_documents_for_scope(
    db: Session,
    space_ids: list[str] | None = None,
    *,
    limit: int | None = None,
    document_ids: list[str] | None = None,
    attachment_document_ids: list[str] | None = None,
) -> list[tuple[Document, DocumentVersion]]:
    """Return published, queryable versions within an authorized scope.

    A scope may be one or more knowledge spaces, an explicit document list,
    or both. Explicit document authorization remains the caller's
    responsibility.
    """
    if not attachment_document_ids and (
        (not space_ids and document_ids is None) or document_ids == []
    ):
        return []

    filters = [
        Document.status == "active",
        DocumentVersion.processing_stage == "ready",
        DocumentVersion.queryable.is_(True),
        DocumentVersion.is_current_published.is_(True),
        or_(
            Document.is_temporary.is_(False),
            Document.expires_at.is_(None),
            Document.expires_at > datetime.now(timezone.utc),
        ),
    ]
    if attachment_document_ids is not None:
        filters.extend([
            Document.temporary_cleanup_at.is_(None),
            or_(
                Document.expires_at.is_(None),
                Document.expires_at > datetime.now(timezone.utc),
            ),
        ])
    knowledge_scope = (
        Document.space_id.in_(space_ids or [])
        if document_ids is None else Document.id.in_(document_ids)
    )
    if attachment_document_ids is not None and document_ids is not None:
        knowledge_scope = and_(knowledge_scope, Document.space_id.in_(space_ids or []))
    # 附件仅补充已授权范围，不能借白名单绕过知识空间权限。
    if attachment_document_ids:
        knowledge_scope = or_(
            knowledge_scope,
            and_(
                Document.id.in_(attachment_document_ids),
                Document.is_temporary.is_(True),
                Document.space_id.is_(None),
            ),
        )
    filters.append(knowledge_scope)

    query = (
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(*filters)
        .order_by(Document.updated_at.desc(), Document.id.desc())
    )
    if limit is not None and limit > 0:
        query = query.limit(limit)
    return [(document, version) for document, version in db.execute(query).all()]


def _postgres_text(value: str) -> str:
    return value.replace("\x00", "")


def create_turn(
    db: Session,
    *,
    session: QaSession,
    question: str,
    status: str,
    retrieval_summary: dict[str, Any],
    answer: str | None,
    verification_summary: dict[str, Any],
    usage: dict[str, Any],
    events: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> QaTurn:
    completed_at = now()
    turn = QaTurn(
        id=f"qat_{new_token(10)}",
        session_id=session.id,
        owner_user_id=session.owner_user_id,
        question=question,
        selected_space_ids=session.selected_space_ids,
        scope_version=session.scope_version,
        status=status,
        retrieval_summary=_dump(retrieval_summary),
        answer=answer,
        verification_summary=_dump(verification_summary),
        usage=_dump(usage),
        events=_dump(events),
        created_at=completed_at,
        completed_at=completed_at,
    )
    db.add(turn)
    for item in evidence:
        db.add(
            Citation(
                id=item["id"],
                turn_id=turn.id,
                document_id=item["document_id"],
                document_version_id=item["document_version_id"],
                page_or_section=item["page_or_section"],
                excerpt=_postgres_text(item["excerpt"]),
                support_state=item["support_state"],
                page_number=item.get("page_number"),
                page_block_id=item.get("page_block_id"),
                bbox_json=_dump(item["bbox"]) if item.get("bbox") else None,
                created_at=completed_at,
            )
        )
    db.flush()
    return turn


def transition_turn_to_terminal(
    db: Session,
    *,
    turn: QaTurn,
    status: str,
    retrieval_summary: dict[str, Any],
    verification_summary: dict[str, Any],
    usage: dict[str, Any],
    events: list[dict[str, Any]],
) -> QaTurn:
    """Replace a stored turn with a safe, evidence-free terminal snapshot."""
    turn.status = status
    turn.answer = None
    turn.retrieval_summary = _dump(retrieval_summary)
    turn.verification_summary = _dump(verification_summary)
    turn.usage = _dump(usage)
    turn.events = _dump(events)
    db.execute(delete(Citation).where(Citation.turn_id == turn.id))
    db.flush()
    return turn


def next_citation_id() -> str:
    return f"cit_{new_token(10)}"


def citations_for_turn(db: Session, turn_id: str) -> list[Citation]:
    return list(
        db.scalars(
            select(Citation).where(Citation.turn_id == turn_id).order_by(Citation.id)
        )
    )


def get_citation(db: Session, citation_id: str) -> Citation | None:
    return db.get(Citation, citation_id)


def get_owned_turn(db: Session, turn_id: str, owner_user_id: str) -> QaTurn | None:
    return db.scalar(
        select(QaTurn).where(
            QaTurn.id == turn_id, QaTurn.owner_user_id == owner_user_id
        )
    )
