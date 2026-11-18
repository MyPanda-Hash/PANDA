from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Literal, cast

from datetime import datetime

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.api.deps import current_user
from app.core.database import SessionLocal, transaction
from app.core.security import as_utc, now
from app.core.user_messages import public_error_message
from app.knowledge_engine.graph_retriever import GraphRetriever
from app.models.auth import User
from app.models.writing import (
    WritingCandidate,
    WritingClaim,
    WritingOperation,
    WritingProject,
    WritingProjectPublication,
)
from app.repositories import audit, usage, writing
from app.schemas.writing import (
    CandidateResponse,
    ChapterResponse,
    ChapterRestoreRequest,
    ChapterSaveRequest,
    CitationBindingResponse,
    DocumentRunRequest,
    ClaimCreateRequest,
    ClaimResponse,
    EvidenceExpandRequest,
    EvidenceExpandResponse,
    ExportRequest,
    ExportPreflightResponse,
    KnowledgeCommunityResponse,
    KnowledgeEntityResponse,
    KnowledgeGapResponse,
    KnowledgeMatchRequest,
    KnowledgeMatchResponse,
    KnowledgeSurpriseResponse,
    OperationEventListResponse,
    OperationResponse,
    OperationEventResponse,
    OutlineResponse,
    OutlineUpdateRequest,
    ProjectCreateRequest,
    ProjectFromChatRequest,
    ProjectResponse,
    ProjectSummaryListResponse,
    ProjectSummaryResponse,
    ProjectUpdateRequest,
    RecommendDocumentItem,
    RecommendDocumentsRequest,
    RecommendDocumentsResponse,
    RewriteRunRequest,
    VersionResponse,
    WritingBriefResponse,
    WritingBriefInput,
    WritingChapterPreview,
    WritingChapterPreviewListResponse,
    WritingContextResponse,
    WritingProjectPublicationDetailResponse,
    WritingProjectPublicationResponse,
)
from app.services.authorization import PermissionService
from app.services.chat_knowledge import (
    normalize_document_ids,
    require_documents_in_scope,
    require_reader_spaces,
)
from app.services.publication_access import can_view_publication
from app.services.model_routing import resolve_model_endpoint
from app.services.writing_membership import (
    require_project_role,
    resolve_project_role,
)
from app.services.writing_preview import (
    PREVIEW_MARKDOWN_LIMIT,
    TERMINAL_PREVIEW_STATUSES,
    PreviewStatus,
    decode_snapshot,
)
from app.services.writing_scope import (
    WritingScopeUnavailableError,
    resolve_current_writing_scope,
)
from app.services.hybrid_retrieval import retrieve_for_qa, select_recommended_documents
from app.services.retrieval_router import RetrievalMode

router = APIRouter(prefix="/writing", tags=["writing"])

logger = logging.getLogger(__name__)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="写作项目不存在")


def _json_object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            return int(value)
        except (OverflowError, ValueError):
            return 0
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _active_lock_user_id(chapter) -> str | None:
    """Holder of a still-valid advisory edit lock, else ``None``.

    Expired locks simply read as absent — no sweeper job is needed.
    """
    holder = getattr(chapter, "edit_lock_user_id", None)
    expires_at = getattr(chapter, "edit_lock_expires_at", None)
    if not holder or expires_at is None:
        return None
    return holder if as_utc(expires_at) > now() else None


def _brief_input(db, brief: WritingBriefInput | None, project: WritingProject | None = None) -> dict | None:
    if brief is None:
        return None
    value = brief.model_dump()
    selected = brief.preferred_model.strip()
    if selected:
        endpoint = resolve_model_endpoint(db, "writing", requested_model=selected)
        if endpoint is None or endpoint.model != selected:
            raise HTTPException(status_code=422, detail="所选生成模型已不可用，请重新选择模型。")
    if project is not None and "preferred_model" not in brief.model_fields_set:
        from app.services.writing_brief import decode_brief

        value["preferred_model"] = decode_brief(project.brief_json)["preferred_model"]
    return value


def _brief_response(project: WritingProject) -> WritingBriefResponse:
    from app.services.writing_brief import decode_brief

    brief = decode_brief(getattr(project, "brief_json", None))
    return WritingBriefResponse(
        revision=project.brief_revision or 1,
        audience=str(brief.get("audience") or ""),
        document_type=str(brief.get("document_type") or ""),
        purpose=str(brief.get("purpose") or ""),
        length_hint=str(brief.get("length_hint") or ""),
        tone=str(brief.get("tone") or ""),
        must_cover=list(brief.get("must_cover") or []),
        forbidden=list(brief.get("forbidden") or []),
        fact_scope=str(brief.get("fact_scope") or ""),
        citation_style=str(brief.get("citation_style") or ""),
        notes=str(brief.get("notes") or ""),
        preferred_model=str(brief.get("preferred_model") or ""),
    )


def _project_response(
    project: WritingProject, *, include_quality: bool = False
) -> ProjectResponse:
    from app.services.writing_skills_registry import writing_skill_payload

    outline = project.outline
    assert outline is not None
    brief = _brief_response(project)
    return ProjectResponse(
        id=project.id,
        title=project.title,
        doc_status=cast(
            Literal["draft", "in_review", "approved", "issued"],
            getattr(project, "doc_status", None) or "draft",
        ),
        doc_number=getattr(project, "doc_number", None),
        issued_at=getattr(project, "issued_at", None),
        valid_until=getattr(project, "valid_until", None),
        space_ids=writing.load_space_ids(project.selected_space_ids),
        document_ids=writing.load_document_ids(
            getattr(project, "selected_document_ids", None)
        ),
        budget_limit=project.budget_limit,
        supported_export_formats=writing_skill_payload(brief.document_type)[
            "supported_formats"
        ],
        pptx_template_revision_id=getattr(project, "pptx_template_revision_id", None),
        brief=brief,
        outline=OutlineResponse(
            id=outline.id,
            status=cast(Literal["draft", "confirmed"], outline.status),
            confirmed_at=outline.confirmed_at,
            chapters=[
                ChapterResponse(
                    id=chapter.id,
                    position=chapter.position,
                    title=chapter.title,
                    content=chapter.content,
                    revision=writing.chapter_revision(chapter),
                    last_editor_user_id=getattr(chapter, "last_editor_user_id", None),
                    edit_lock_user_id=_active_lock_user_id(chapter),
                    edit_lock_expires_at=(
                        chapter.edit_lock_expires_at
                        if _active_lock_user_id(chapter)
                        else None
                    ),
                    updated_at=chapter.updated_at,
                )
                for chapter in sorted(outline.chapters, key=lambda item: item.position)
            ],
        ),
        claims=[_claim_response(claim) for claim in project.claims],
        versions=[
            VersionResponse(
                id=version.id,
                chapter_id=chapter.id,
                chapter_title=chapter.title,
                content=version.content,
                kind=version.kind,
                created_at=version.created_at,
            )
            for chapter in outline.chapters
            for version in chapter.versions
        ],
        operations=[
            _operation_response(
                item, include_quality=include_quality, quality_project=project
            )
            for item in project.operations
        ],
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _project_summary_response(row: Any) -> ProjectSummaryResponse:
    """列表项来自计数版查询（不加载正文与历史版本），这里只做字段整形。"""
    from app.services.writing_brief import decode_brief
    from app.services.writing_skills_registry import writing_skill_payload

    brief = decode_brief(getattr(row, "brief_json", None))
    return ProjectSummaryResponse(
        id=row.id,
        title=row.title,
        space_ids=writing.load_space_ids(row.selected_space_ids),
        supported_export_formats=writing_skill_payload(
            str(brief.get("document_type") or "")
        )["supported_formats"],
        outline_status=cast(Literal["draft", "confirmed"], row.outline_status),
        chapter_count=row.chapter_count,
        version_count=row.version_count,
        unsupported_claim_count=row.unsupported_claim_count,
        completed_export_count=row.completed_export_count,
        updated_at=row.updated_at,
    )


def _user_display_name(user: User | None) -> str:
    if not user:
        return "未知用户"
    return (user.display_name or user.username or user.id).strip()


def _publication_payload(
    db,
    publication: WritingProjectPublication,
    viewer: User | None = None,
) -> dict:
    owner = db.get(User, publication.source_owner_user_id)
    shared_by = db.get(User, publication.shared_by_user_id)
    snapshot = writing.publication_snapshot(publication)
    chapters = snapshot.get("chapters")
    chapters = chapters if isinstance(chapters, list) else []
    content_length = sum(
        len(str(item.get("content") or ""))
        for item in chapters
        if isinstance(item, dict)
    )
    mine = bool(
        viewer
        and viewer.id
        in {publication.source_owner_user_id, publication.shared_by_user_id}
    )
    can_revoke = bool(
        viewer
        and (viewer.role in {"admin", "system_admin"} or mine)
    )
    return {
        "id": publication.id,
        "source_project_id": publication.source_project_id,
        "source_owner_user_id": publication.source_owner_user_id,
        "source_owner_name": _user_display_name(owner),
        "shared_by_user_id": publication.shared_by_user_id,
        "shared_by_name": _user_display_name(shared_by),
        "title": publication.title,
        "summary": publication.summary,
        "version": publication.version,
        "share_scope": publication.share_scope,
        "allow_cite": publication.allow_cite,
        "allow_remix": publication.allow_remix,
        "status": publication.status,
        "chapter_count": len(chapters),
        "content_length": content_length,
        "can_revoke": can_revoke,
        "relation": "published_by_me" if mine else "shared_with_me",
        "published_at": publication.published_at.isoformat(),
        "revoked_at": publication.revoked_at.isoformat()
        if publication.revoked_at
        else None,
    }


def _claim_response(claim: WritingClaim) -> ClaimResponse:
    return ClaimResponse(
        id=claim.id,
        text=claim.text,
        support_state=cast(
            Literal["supported", "uncertain", "unsupported"], claim.support_state
        ),
        created_at=claim.created_at,
    )


def _operation_response(
    operation: WritingOperation,
    *,
    include_quality: bool = False,
    quality_project: WritingProject | None = None,
) -> OperationResponse:
    result = _json_object(operation.result)
    # 问题原句属于来源派生内容；只有重新校验当前资料权限的入口才能返回。
    quality_scope = result.pop("quality_scope", None)
    if (
        not include_quality
        or quality_project is None
        or not isinstance(quality_scope, dict)
        or quality_scope.get("space_ids")
        != sorted(writing.load_space_ids(quality_project.selected_space_ids))
        or quality_scope.get("document_ids")
        != sorted(writing.load_document_ids(quality_project.selected_document_ids))
        or quality_scope.get("brief_revision") != (quality_project.brief_revision or 1)
    ):
        result.pop("quality_by_chapter", None)
    return OperationResponse(
        id=operation.id,
        kind=operation.kind,
        status=cast(
            Literal[
                "queued",
                "dispatching",
                "running",
                "completed",
                "failed",
                "cancelling",
                "cancelled",
                "stopped",
            ],
            operation.status,
        ),
        usage=_json_object(operation.usage),
        result=result,
        attempt=operation.attempt,
        last_event_cursor=operation.last_event_cursor,
        error_code=operation.error_code,
        error_message=public_error_message(
            operation.error_message_safe,
            fallback="写作任务失败，请稍后重试。",
        )
        if operation.error_message_safe
        else None,
        created_at=operation.created_at,
        started_at=operation.started_at,
        completed_at=operation.completed_at,
    )


def _candidate_response(
    candidate: WritingCandidate,
    *,
    stale: bool = False,
    author_name: str | None = None,
) -> CandidateResponse:
    return CandidateResponse(
        id=candidate.id,
        writing_operation_id=candidate.writing_operation_id,
        kind=cast(
            Literal["outline", "section", "verification", "document"],
            candidate.kind,
        ),
        chapter_id=candidate.chapter_id,
        candidate_revision=candidate.candidate_revision,
        payload=_json_object(candidate.payload_json),
        status=cast(Literal["pending", "published", "rejected"], candidate.status),
        origin=cast(
            Literal["agent", "human"], getattr(candidate, "origin", None) or "agent"
        ),
        author_user_id=getattr(candidate, "author_user_id", None),
        author_name=author_name,
        created_at=candidate.created_at,
        published_at=candidate.published_at,
        stale=stale,
    )


def _section_candidate_response(
    db, project: WritingProject, candidate: WritingCandidate
) -> CandidateResponse:
    stale = False
    if candidate.chapter_id:
        chapter = writing.chapter_for_project(db, project.id, candidate.chapter_id)
        if chapter is not None:
            stale = writing.is_section_candidate_stale(chapter, candidate)
    author = (
        db.get(User, candidate.author_user_id)
        if getattr(candidate, "author_user_id", None)
        else None
    )
    return _candidate_response(
        candidate,
        stale=stale,
        author_name=_user_display_name(author) if author is not None else None,
    )


def _project(
    db, project_id: str, user: User, *, minimum_role: str = "editor"
) -> WritingProject:
    """Load a project the acting user may touch at ``minimum_role`` or above.

    The default is deliberately ``editor``: most endpoints below mutate the
    document, so an unannotated call site fails closed rather than open.
    Read-only endpoints pass ``minimum_role="viewer"`` explicitly.

    Being a project member never grants knowledge-space access — endpoints that
    surface source material must still check the acting user's space ACL (see
    ``_scope_is_current`` / ``require_reader_spaces``).
    """
    return require_project_role(db, project_id, user, minimum_role=minimum_role)


def _chapter_conflict_detail(db, chapter, submitted_content: str) -> dict:
    """409 payload for a chapter save that raced another editor."""
    editor_id = getattr(chapter, "last_editor_user_id", None)
    editor = db.get(User, editor_id) if editor_id else None
    return {
        "error_code": "chapter_revision_conflict",
        "chapter_id": chapter.id,
        "current_revision": writing.chapter_revision(chapter),
        "current_content": chapter.content,
        "submitted_content": submitted_content,
        "last_editor_user_id": editor_id,
        "last_editor_name": _user_display_name(editor),
        "updated_at": chapter.updated_at.isoformat() if chapter.updated_at else None,
    }


def _operation_visible(
    db, project_id: str, user: User, *, minimum_role: str = "viewer"
) -> bool:
    """Whether ``user`` may see (or act on) operations of ``project_id``."""
    return (
        resolve_project_role(db, project_id, user.id, minimum_role=minimum_role)
        is not None
    )


def _project_with_role(
    db, project_id: str, user: User, *, minimum_role: str = "editor"
) -> tuple[WritingProject, str]:
    resolved = resolve_project_role(db, project_id, user.id, minimum_role=minimum_role)
    if resolved is None:
        raise _not_found()
    return resolved


def _scope_is_current(db, project: WritingProject, user: User) -> bool:
    try:
        resolve_current_writing_scope(db, user=user, project=project)
    except WritingScopeUnavailableError:
        return False
    return True


def _create_agent_operation(
    *,
    project_id: str,
    workflow: str,
    idempotency_key: str | None,
    user: User,
    input_payload: dict[str, object] | None = None,
) -> OperationResponse:
    key = (idempotency_key or "").strip() or None
    input_payload = {**(input_payload or {}), "actor_user_id": user.id}
    with transaction() as db:
        project = _project(db, project_id, user)
        if workflow == "document" and (
            not project.outline or project.outline.status != "confirmed"
        ):
            raise HTTPException(status_code=409, detail="请先确认大纲后再生成正文")
        requested_chapter_ids = (
            input_payload.get("chapter_ids") if input_payload else None
        )
        if workflow == "document" and requested_chapter_ids:
            known_chapter_ids = {chapter.id for chapter in project.outline.chapters}
            if any(
                chapter_id not in known_chapter_ids
                for chapter_id in requested_chapter_ids
            ):
                raise HTTPException(status_code=404, detail="指定章节不存在")
        if not _scope_is_current(db, project, user):
            operation = writing.create_operation(
                db,
                project,
                workflow,
                idempotency_key=key,
                input_payload=input_payload,
                input_revision=1,
            )
            return _operation_response(
                writing.stop_operation(db, operation, "scope_no_longer_available")
            )
        try:
            operation = writing.create_operation(
                db,
                project,
                workflow,
                idempotency_key=key,
                input_payload=input_payload,
                input_revision=1,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if workflow == "document":
            state = _json_object(operation.result)
            if "target_chapter_ids" not in state:
                state["target_chapter_ids"] = _target_chapter_ids(db, operation)
                operation.result = json.dumps(state, ensure_ascii=False)
        existing_binding = writing.binding_for_operation(db, operation.id)
        if existing_binding:
            return _operation_response(operation)
        writing.enqueue_dispatch(db, operation)
        return _operation_response(operation)


def _parse_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    try:
        return writing.decode_project_cursor(cursor)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid cursor")


@router.get("/projects", response_model=ProjectSummaryListResponse)
def list_projects(
    user: User = Depends(current_user),
    limit: int = Query(
        writing.PROJECT_PAGE_SIZE, ge=1, le=writing.PROJECT_MAX_PAGE_SIZE
    ),
    cursor: str | None = Query(None),
    q: str = Query("", max_length=200),
    delivery: Literal["all", "active", "delivered"] = Query("all"),
) -> ProjectSummaryListResponse:
    with transaction() as db:
        rows, next_cursor = writing.list_project_summaries(
            db,
            user.id,
            query=q,
            delivery=delivery,
            limit=limit,
            cursor=_parse_cursor(cursor),
        )
        return ProjectSummaryListResponse(
            items=[_project_summary_response(row) for row in rows],
            next_cursor=next_cursor,
        )


@router.get(
    "/shared-projects/{publication_id}",
    response_model=WritingProjectPublicationDetailResponse,
)
def get_shared_project(
    publication_id: str,
    user: User = Depends(current_user),
) -> WritingProjectPublicationDetailResponse:
    with SessionLocal() as db:
        publication = writing.get_publication(db, publication_id)
        if not publication or not can_view_publication(db, user, publication):
            raise HTTPException(status_code=404, detail="共享文稿不存在或已撤销")
        payload = _publication_payload(db, publication, user)
        snapshot = writing.publication_snapshot(publication)
        chapters = snapshot.get("chapters")
        payload["chapters"] = chapters if isinstance(chapters, list) else []
        payload["evidence_sources"] = snapshot.get("evidence_sources") or []
        return WritingProjectPublicationDetailResponse(**payload)


@router.delete(
    "/shared-projects/{publication_id}", status_code=204, response_model=None
)
def revoke_shared_project(
    publication_id: str, user: User = Depends(current_user)
) -> None:
    with transaction() as db:
        publication = writing.get_publication(db, publication_id)
        is_admin = user.role in {"admin", "system_admin"}
        can_revoke = bool(
            publication
            and (
                is_admin
                or user.id
                in {publication.source_owner_user_id, publication.shared_by_user_id}
            )
        )
        if not publication or not can_revoke:
            raise HTTPException(status_code=404, detail="共享文稿不存在或无权撤销")
        writing.revoke_publication(db, publication)


@router.post("/projects", response_model=ProjectResponse, status_code=201)
def create_project(
    payload: ProjectCreateRequest, user: User = Depends(current_user)
) -> ProjectResponse:
    with transaction() as db:
        for space_id in payload.space_ids:
            PermissionService.require_space_access(
                db, user, space_id, minimum_role="reader"
            )
        document_ids = normalize_document_ids(payload.document_ids)
        if document_ids and not payload.space_ids:
            raise HTTPException(
                status_code=422, detail="指定文档时必须同时选择知识空间"
            )
        if document_ids:
            require_documents_in_scope(
                db, user, space_ids=payload.space_ids, document_ids=document_ids
            )
        brief = _brief_input(db, payload.brief)
        project = writing.create_project(
            db,
            owner_user_id=user.id,
            title=payload.title.strip(),
            space_ids=payload.space_ids,
            document_ids=document_ids,
            budget_limit=payload.budget_limit,
            brief=brief,
        )
        return _project_response(project)


# 证据类 blocker 能否原样作为 blocking_reason 的白名单；保存时提示与
# _preflight 共用同一套映射，避免“保存提示无阻断、正式检查却失败”的漂移。
_EVIDENCE_BLOCK_REASON_CODES = {
    "incomplete_document",
    "incomplete_evidence_coverage",
    "unsupported_content",
    "missing_inline_bindings",
    "scope_no_longer_available",
}


def _evidence_reason(code: str) -> str:
    return code if code in _EVIDENCE_BLOCK_REASON_CODES else "evidence_invalid"


def _save_citation_binding(
    *,
    has_scope: bool,
    validation: Any,
) -> CitationBindingResponse | None:
    """存为文稿后立即回报与正式预检同源的证据阻断；不阻止草稿保存。

    页面只做一行提示，不做人工核对；逐处位置由 exports:preflight 在用户
    主动检查时给出。没有资料范围也不涉及来源时返回 None（不适用）。
    """
    if not has_scope:
        return None
    return CitationBindingResponse(
        blocking_reasons=list(dict.fromkeys(
            _evidence_reason(str(blocker.get("code") or ""))
            for blocker in validation.blockers
        )),
        issue_count=sum(
            len(blocker.get("support_issues") or []) for blocker in validation.blockers
        ),
    )


@router.post("/projects/from-chat", response_model=ProjectResponse, status_code=201)
def create_project_from_chat(
    payload: ProjectFromChatRequest, user: User = Depends(current_user)
) -> ProjectResponse:
    """从自由对话沉淀为写作项目：写入大纲/正文种子，资料空间可空。"""
    space_ids = list(dict.fromkeys(payload.space_ids))
    document_ids = normalize_document_ids(payload.document_ids)
    if bool(payload.source_chat_session_id) != bool(
        payload.source_chat_assistant_message_id
    ):
        raise HTTPException(
            status_code=422, detail="聊天来源必须同时提供会话和回复标识"
        )
    title = payload.title.strip()
    seed = payload.seed_markdown.strip()
    if payload.seed_outline:
        chapters = [
            {"title": item.title.strip()}
            for item in payload.seed_outline
            if item.title.strip()
        ]
    else:
        chapters = [{"title": "正文"}]
    if not chapters:
        chapters = [{"title": "正文"}]

    with transaction() as db:
        source_evidence: list[dict] = []
        source_evidence_payload: dict = {}
        if payload.source_chat_session_id and payload.source_chat_assistant_message_id:
            from app.repositories import chat as chat_repo

            session = chat_repo.get_owned_session(
                db, payload.source_chat_session_id, user.id
            )
            source = (
                next(
                    (
                        message
                        for message in session.messages
                        if message.id == payload.source_chat_assistant_message_id
                        and message.owner_user_id == user.id
                        and message.role == "assistant"
                    ),
                    None,
                )
                if session is not None
                else None
            )
            if source is None:
                raise HTTPException(status_code=404, detail="聊天来源不存在或无权访问")
            prior_user = next(
                (
                    message
                    for message in reversed(
                        session.messages[: session.messages.index(source)]
                    )
                    if message.role == "user"
                ),
                None,
            )
            source_message = chat_repo.message_to_dict(source)
            scope_message = prior_user or source
            scope_payload = chat_repo.message_to_dict(scope_message)
            if (
                not scope_payload.get("space_ids")
                and not scope_payload.get("document_ids")
                and scope_payload.get("writing_context") is None
            ):
                # Accessible-mode chat stores its actual server-derived source
                # scope on the assistant. Explicit user/project scope wins.
                scope_payload = source_message
            space_ids = list(dict.fromkeys(scope_payload.get("space_ids") or []))
            document_ids = normalize_document_ids(
                scope_payload.get("document_ids") or []
            )
            source_evidence = source_message.get("references") or []
            trace = source_message.get("retrieval_trace") or {}
            source_evidence_payload = {
                "evidence": source_evidence,
                "coverage": trace.get("coverage") or {},
            }
        if document_ids and not space_ids and not payload.source_chat_session_id:
            raise HTTPException(
                status_code=422, detail="指定文档时必须同时选择知识空间"
            )
        for space_id in space_ids:
            PermissionService.require_space_access(
                db, user, space_id, minimum_role="reader"
            )
        if document_ids:
            require_documents_in_scope(
                db,
                user,
                space_ids=space_ids,
                document_ids=document_ids,
                chat_session_id=payload.source_chat_session_id,
            )
        brief = _brief_input(db, payload.brief)
        if brief is None and payload.source_chat_hint:
            brief = {"purpose": title, "notes": payload.source_chat_hint}
        project = writing.create_project(
            db,
            owner_user_id=user.id,
            title=title,
            space_ids=space_ids,
            document_ids=document_ids,
            budget_limit=payload.budget_limit,
            brief=brief,
            source_chat_session_id=payload.source_chat_session_id,
            source_chat_assistant_message_id=payload.source_chat_assistant_message_id,
        )
        project = writing.replace_outline(db, project, chapters)
        if seed:
            outline = project.outline
            assert outline is not None
            first = sorted(outline.chapters, key=lambda item: item.position)[0]
            writing.seed_chapter(db, project, first.id, seed)
            validation = None
            if payload.source_chat_session_id:
                from app.services.evidence_export import validate_evidence_sources

                validation = validate_evidence_sources(
                    db,
                    user=user,
                    texts=[seed],
                    evidence=source_evidence,
                    space_ids=space_ids,
                    document_ids=document_ids or None,
                    chat_session_id=payload.source_chat_session_id,
                )
            if validation is not None:
                operation = writing.start_operation(
                    db,
                    writing.create_operation(
                        db,
                        project,
                        "manual_save",
                        input_payload={"actor_user_id": user.id},
                    ),
                )
                writing.complete_operation(
                    db,
                    operation,
                    {
                        "evidence_payload": source_evidence_payload,
                        "evidence_sources": validation.sources,
                        "evidence_blockers": validation.blockers,
                        "source_chat_session_id": payload.source_chat_session_id,
                        "source_chat_assistant_message_id": payload.source_chat_assistant_message_id,
                    },
                )
            project = writing.owned_project(db, project.id, user.id)
            assert project is not None
        response = _project_response(project)
        if space_ids or document_ids or source_evidence:
            from app.services.evidence_export import validate_project_evidence

            try:
                validation = validate_project_evidence(db, user=user, project=project)
            except WritingScopeUnavailableError:
                response.citation_binding = CitationBindingResponse(
                    blocking_reasons=["scope_no_longer_available"]
                )
            else:
                response.citation_binding = _save_citation_binding(
                    has_scope=True, validation=validation
                )
        return response


@router.post(
    "/projects/recommend-documents",
    response_model=RecommendDocumentsResponse,
    status_code=200,
)
def recommend_documents_for_writing(
    payload: RecommendDocumentsRequest, user: User = Depends(current_user)
) -> RecommendDocumentsResponse:
    """根据写作任务智能推荐最相关的引用资料。

    当检索证据低于阈值时宁可不返回，避免自动选错资料。
    """
    query = payload.goal.strip() or payload.skill.strip()
    if not query:
        raise HTTPException(status_code=422, detail="请提供 skill 或 goal 用于匹配资料")

    with transaction() as db:
        for space_id in payload.space_ids:
            PermissionService.require_space_access(
                db, user, space_id, minimum_role="reader"
            )
        document_ids = normalize_document_ids(payload.document_ids) or None
        bundle = retrieve_for_qa(
            db,
            question=query,
            space_ids=payload.space_ids,
            document_ids=document_ids,
            top_k=payload.top_k,
            purpose="writing",
            mode=RetrievalMode.AUTO,
        )
        score_rows = list(bundle.scores or bundle.meta.get("scores") or [])
        threshold = max(0.0, min(1.0, payload.threshold))
        selected = [
            RecommendDocumentItem(
                document_id=row["document_id"],
                score=row["score"],
                lex_score=row.get("lex_score", 0.0),
                graph_score=row.get("graph_score", 0.0),
                vector_score=row.get("vector_score", 0.0),
            )
            for row in select_recommended_documents(score_rows, threshold=threshold)
        ]
        return RecommendDocumentsResponse(
            skill=payload.skill,
            query=query,
            recommendations=selected,
            selected_count=len(selected),
            total=len(score_rows),
            threshold=threshold,
        )


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, user: User = Depends(current_user)) -> ProjectResponse:
    with transaction() as db:
        project = _project(db, project_id, user, minimum_role="viewer")
        return _project_response(
            project, include_quality=_scope_is_current(db, project, user)
        )


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdateRequest,
    user: User = Depends(current_user),
) -> ProjectResponse:
    with transaction() as db:
        project, role = _project_with_role(db, project_id, user)
        if "budget_limit" in payload.model_fields_set and role != "owner":
            raise HTTPException(
                status_code=403, detail="只有文稿所有者可以调整预算上限"
            )
        if "valid_until" in payload.model_fields_set:
            project.valid_until = payload.valid_until
        space_ids = (
            payload.space_ids
            if payload.space_ids is not None
            else writing.load_space_ids(project.selected_space_ids)
        )
        if payload.space_ids is not None:
            for space_id in payload.space_ids:
                PermissionService.require_space_access(
                    db, user, space_id, minimum_role="reader"
                )
        document_ids = None
        if payload.document_ids is not None:
            document_ids = normalize_document_ids(payload.document_ids)
            if document_ids and not space_ids:
                raise HTTPException(
                    status_code=422, detail="指定文档时必须同时选择知识空间"
                )
            if document_ids:
                require_documents_in_scope(
                    db, user, space_ids=space_ids, document_ids=document_ids
                )
        brief = _brief_input(db, payload.brief, project)
        return _project_response(
            writing.update_project(
                db,
                project,
                title=payload.title,
                space_ids=payload.space_ids,
                document_ids=document_ids,
                budget_limit=payload.budget_limit,
                brief=brief,
                update_budget="budget_limit" in payload.model_fields_set,
            )
        )


@router.delete(
    "/projects/{project_id}",
    status_code=204,
    response_class=Response,
    response_model=None,
)
def delete_project(project_id: str, user: User = Depends(current_user)) -> None:
    with transaction() as db:
        writing.delete_project(db, _project(db, project_id, user, minimum_role="owner"))


@router.put("/projects/{project_id}/outline", response_model=ProjectResponse)
def update_outline(
    project_id: str, payload: OutlineUpdateRequest, user: User = Depends(current_user)
) -> ProjectResponse:
    with transaction() as db:
        try:
            return _project_response(
                writing.replace_outline(
                    db,
                    _project(db, project_id, user),
                    [item.model_dump() for item in payload.chapters],
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/outline:confirm", response_model=ProjectResponse)
def confirm_outline(
    project_id: str, user: User = Depends(current_user)
) -> ProjectResponse:
    with transaction() as db:
        return _project_response(
            writing.confirm_outline(db, _project(db, project_id, user))
        )


@router.post(
    "/projects/{project_id}/chapters:generate",
    response_model=OperationResponse,
    status_code=201,
)
def generate_chapters(
    project_id: str, user: User = Depends(current_user)
) -> OperationResponse:
    with transaction() as db:
        project = _project(db, project_id, user)
        if not project.outline or project.outline.status != "confirmed":
            raise HTTPException(status_code=409, detail="请先确认大纲后再批量生成")
        operation = writing.start_operation(
            db, writing.create_operation(db, project, "batch_generation")
        )
        if not _scope_is_current(db, project, user):
            return _operation_response(
                writing.stop_operation(db, operation, "scope_no_longer_available")
            )
        versions = writing.generate_chapters(db, project)
        return _operation_response(
            writing.complete_operation(
                db, operation, {"version_ids": [version.id for version in versions]}
            )
        )


@router.post(
    "/projects/{project_id}/outline-runs",
    response_model=OperationResponse,
    status_code=201,
)
def create_outline_run(
    project_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(current_user),
) -> OperationResponse:
    return _create_agent_operation(
        project_id=project_id,
        workflow="outline",
        idempotency_key=idempotency_key,
        user=user,
    )


@router.post(
    "/projects/{project_id}/document-runs",
    response_model=OperationResponse,
    status_code=201,
)
def create_document_run(
    project_id: str,
    payload: DocumentRunRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(current_user),
) -> OperationResponse:
    chapter_ids = (
        list(dict.fromkeys(chapter_id.strip() for chapter_id in payload.chapter_ids))
        if payload
        else []
    )
    if any(not chapter_id or len(chapter_id) > 64 for chapter_id in chapter_ids):
        raise HTTPException(status_code=422, detail="章节 ID 格式无效")
    return _create_agent_operation(
        project_id=project_id,
        workflow="document",
        idempotency_key=idempotency_key,
        user=user,
        input_payload={"chapter_ids": chapter_ids} if chapter_ids else None,
    )


@router.post(
    "/projects/{project_id}/rewrite-runs",
    response_model=OperationResponse,
    status_code=201,
)
def create_rewrite_run(
    project_id: str,
    payload: RewriteRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(current_user),
) -> OperationResponse:
    key = (idempotency_key or "").strip() or None
    with transaction() as db:
        project = _project(db, project_id, user)
        if not project.outline or project.outline.status != "confirmed":
            raise HTTPException(status_code=409, detail="请先确认大纲后再重写正文")
        chapter = writing.chapter_for_project(db, project.id, payload.chapter_id)
        if chapter is None:
            raise HTTPException(status_code=404, detail="章节不存在")
        if payload.selection_end <= payload.selection_start:
            raise HTTPException(status_code=422, detail="选区结束位置必须大于开始位置")
        if payload.selection_end > len(chapter.content):
            raise HTTPException(status_code=422, detail="选区超出当前章节范围")
        if not _scope_is_current(db, project, user):
            operation = writing.create_operation(
                db,
                project,
                "rewrite",
                idempotency_key=key,
                input_revision=1,
                input_payload={"actor_user_id": user.id},
            )
            return _operation_response(
                writing.stop_operation(db, operation, "scope_no_longer_available")
            )
        source_revision = len(chapter.versions)
        try:
            operation = writing.create_operation(
                db,
                project,
                "rewrite",
                idempotency_key=key,
                input_revision=source_revision,
                input_payload={
                    "actor_user_id": user.id,
                    "chapter_id": chapter.id,
                    "instruction": payload.instruction.strip(),
                    "selection_start": payload.selection_start,
                    "selection_end": payload.selection_end,
                    "selected_text": chapter.content[
                        payload.selection_start : payload.selection_end
                    ],
                    "source_content": chapter.content,
                    "fact_locks": [
                        item.model_dump(mode="json") for item in payload.fact_locks
                    ],
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if writing.binding_for_operation(db, operation.id) is None:
            writing.enqueue_dispatch(db, operation)
        return _operation_response(operation)


@router.get(
    "/projects/{project_id}/outline-candidates",
    response_model=list[CandidateResponse],
)
def list_outline_candidates(
    project_id: str, user: User = Depends(current_user)
) -> list[CandidateResponse]:
    with transaction() as db:
        project = _project(db, project_id, user, minimum_role="viewer")
        source_visible = _scope_is_current(db, project, user)
        return [
            _candidate_response(item)
            for item in writing.candidates_for_project(db, project.id, kind="outline")
            if item.origin == "human" or source_visible
        ]


@router.post(
    "/projects/{project_id}/outline-candidates/{candidate_id}:publish",
    response_model=ProjectResponse,
)
def publish_outline_candidate(
    project_id: str,
    candidate_id: str,
    user: User = Depends(current_user),
) -> ProjectResponse:
    with transaction() as db:
        project = _project(db, project_id, user)
        candidate = writing.candidate_for_project(db, project.id, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="大纲候选不存在")
        if candidate.origin != "human" and not _scope_is_current(db, project, user):
            raise HTTPException(
                status_code=403, detail="知识范围已失效，无法采用生成候选"
            )
        try:
            updated = writing.publish_outline_candidate(db, project, candidate)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _project_response(updated)


@router.get(
    "/projects/{project_id}/section-candidates",
    response_model=list[CandidateResponse],
)
def list_section_candidates(
    project_id: str, user: User = Depends(current_user)
) -> list[CandidateResponse]:
    with transaction() as db:
        project = _project(db, project_id, user, minimum_role="viewer")
        source_visible = _scope_is_current(db, project, user)
        return [
            _section_candidate_response(db, project, item)
            for item in writing.candidates_for_project(db, project.id, kind="section")
            if item.origin == "human" or source_visible
        ]


@router.post(
    "/projects/{project_id}/section-candidates/{candidate_id}:publish",
    response_model=VersionResponse,
)
def publish_section_candidate(
    project_id: str,
    candidate_id: str,
    force: bool = False,
    user: User = Depends(current_user),
) -> VersionResponse:
    with transaction() as db:
        project = _project(db, project_id, user)
        candidate = writing.candidate_for_project(db, project.id, candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="章节候选不存在")
        if candidate.origin != "human" and not _scope_is_current(db, project, user):
            raise HTTPException(
                status_code=403, detail="知识范围已失效，无法采用生成候选"
            )
        try:
            version = writing.publish_section_candidate(
                db, project, candidate, force=force
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if getattr(candidate, "origin", "agent") == "human":
            audit.record(
                db,
                user.id,
                "writing_suggestion_published",
                f"{project.id}:{candidate.id}",
                "success",
            )
        chapter = writing.chapter_for_project(db, project.id, version.chapter_id)
        assert chapter is not None
        return VersionResponse(
            id=version.id,
            chapter_id=version.chapter_id,
            chapter_title=chapter.title,
            content=version.content,
            kind=version.kind,
            created_at=version.created_at,
        )


@router.post(
    "/projects/{project_id}/section-candidates/{candidate_id}:reject",
    response_model=CandidateResponse,
)
def reject_section_candidate(
    project_id: str,
    candidate_id: str,
    user: User = Depends(current_user),
) -> CandidateResponse:
    with transaction() as db:
        project = _project(db, project_id, user)
        candidate = writing.candidate_for_project(db, project.id, candidate_id)
        if candidate is None or candidate.kind != "section":
            raise HTTPException(status_code=404, detail="章节候选不存在")
        if candidate.origin != "human" and not _scope_is_current(db, project, user):
            raise HTTPException(
                status_code=403, detail="知识范围已失效，无法读取生成候选"
            )
        try:
            rejected = _section_candidate_response(
                db, project, writing.reject_candidate(candidate)
            )
            if getattr(candidate, "origin", "agent") == "human":
                audit.record(
                    db,
                    user.id,
                    "writing_suggestion_rejected",
                    f"{project.id}:{candidate.id}",
                    "success",
                )
            return rejected
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/operations/{operation_id}", response_model=OperationResponse)
def get_operation(
    operation_id: str, user: User = Depends(current_user)
) -> OperationResponse:
    with transaction() as db:
        operation = writing.operation_by_id(db, operation_id)
        if operation is None or not _operation_visible(
            db, operation.project_id, user, minimum_role="viewer"
        ):
            raise HTTPException(status_code=404, detail="写作任务不存在")
        return _operation_response(
            operation,
            include_quality=_operation_source_visible(db, operation, user),
            quality_project=db.get(WritingProject, operation.project_id),
        )


def _operation_event_response(
    receipt, *, include_source_text: bool = True
) -> OperationEventResponse:
    payload = _json_object(receipt.payload_json)
    if not include_source_text:
        # Text derived from source documents must not outlive the reader's
        # access to them: the envelope still streams (cursors stay monotonic)
        # but carries no chapter text.
        if receipt.event_type == writing.PREVIEW_EVENT_TYPE:
            payload.pop("preview", None)
        elif receipt.event_type == "message_update":
            payload.pop("delta", None)
    scope = str(payload.get("scope") or "")
    chapter_id = payload.get("chapter_id")
    if not isinstance(chapter_id, str) and scope.startswith("document:"):
        chapter_id = scope.removeprefix("document:") or None
    return OperationEventResponse(
        event_id=receipt.event_id,
        type=receipt.event_type,
        stage=payload.get("stage"),
        agent_name=payload.get("agent_name"),
        tool_name=payload.get("tool_name"),
        chapter_id=chapter_id if isinstance(chapter_id, str) else None,
        payload=payload,
        created_at=receipt.created_at.isoformat(),
    )


_OPERATION_EVENT_PAGE_SIZE = 200


def _load_operation_events(
    db,
    operation: WritingOperation,
    *,
    after_cursor: int,
    include_source_text: bool = True,
) -> list[OperationEventResponse]:
    receipts = writing.list_agent_events(
        db,
        platform_run_id=f"pi-intent:{operation.id}",
        after_event_id=max(0, after_cursor),
        limit=_OPERATION_EVENT_PAGE_SIZE,
    )
    return [
        _operation_event_response(receipt, include_source_text=include_source_text)
        for receipt in receipts
    ]


def _operation_source_visible(db, operation: WritingOperation, user: User) -> bool:
    """Whether the acting user may still see text derived from this run's sources.

    Project membership is not source access: the stored scope of the run is
    re-checked against the acting user's current ACL, so a revoked space or
    document stops leaking generated text to that person.
    """
    project = db.get(WritingProject, operation.project_id)
    if project is None:
        return False
    return _scope_is_current(db, project, user)


@router.get(
    "/operations/{operation_id}/previews",
    response_model=WritingChapterPreviewListResponse,
)
def get_operation_chapter_previews(
    operation_id: str,
    user: User = Depends(current_user),
) -> WritingChapterPreviewListResponse:
    """Latest cumulative Markdown preview per chapter of a writing operation.

    Answers from the snapshots the provider persists while a chapter streams, so
    a reload — or a dispatcher restart — recovers the same partial text without
    replaying the model or depending on best-effort raw telemetry.
    """
    with transaction() as db:
        operation = writing.operation_by_id(db, operation_id)
        if operation is None or not _operation_visible(
            db, operation.project_id, user, minimum_role="viewer"
        ):
            raise HTTPException(status_code=404, detail="写作任务不存在")
        if not _operation_source_visible(db, operation, user):
            raise HTTPException(
                status_code=403, detail="知识范围已失效，无法查看章节预览"
            )
        items = [
            item
            for item in (
                _chapter_preview_response(db, receipt, operation=operation)
                for receipt in writing.chapter_preview_events(
                    db, platform_run_id=f"pi-intent:{operation_id}"
                )
            )
            if item is not None
        ]
        target_chapter_ids = _target_chapter_ids(db, operation)
    return WritingChapterPreviewListResponse(
        items=items, target_chapter_ids=target_chapter_ids
    )


def _target_chapter_ids(db, operation: WritingOperation) -> list[str]:
    """Ordered chapters this operation generates — its run's actual targets.

    A subset run narrows the outline to the chapters it asked for, in outline
    order, so a reloaded client knows the first target and what follows without
    guessing from previews (a chapter that has not started has none).
    """
    recorded = _json_object(operation.result).get("target_chapter_ids")
    if isinstance(recorded, list):
        return [str(chapter_id) for chapter_id in recorded]
    project = db.get(WritingProject, operation.project_id)
    outline = project.outline if project is not None else None
    if outline is None:
        return []
    ordered = [
        chapter.id
        for chapter in sorted(outline.chapters, key=lambda item: item.position)
    ]
    requested = _json_object(operation.input_payload).get("chapter_ids")
    if not isinstance(requested, list) or not requested:
        empty = [
            chapter.id
            for chapter in sorted(outline.chapters, key=lambda item: item.position)
            if not chapter.content.strip()
        ]
        return empty if 0 < len(empty) < len(ordered) else ordered
    wanted = {str(item) for item in requested}
    return [chapter_id for chapter_id in ordered if chapter_id in wanted]


def _settled_chapter_preview(
    db, operation: WritingOperation, chapter_id: str
) -> tuple[PreviewStatus, str, str | None, int | None] | None:
    """Rebuild a finished chapter's outcome from the committed result.

    The terminal snapshot write is best-effort, so a lost one must not leave a
    chapter stuck on an active stage: the operation and its candidates are the
    durable truth about what was actually saved or offered for adoption.
    """
    candidate = writing.latest_section_candidate(
        db, operation_id=operation.id, chapter_id=chapter_id
    )
    if candidate is None:
        return None
    payload = _json_object(candidate.payload_json)
    markdown = payload.get("markdown")
    text = markdown if isinstance(markdown, str) else ""
    if candidate.status != "published":
        return ("candidate", text, candidate.id, None)
    chapter = writing.chapter_for_project(db, operation.project_id, chapter_id)
    return (
        "saved",
        text,
        candidate.id,
        writing.chapter_revision(chapter) if chapter is not None else None,
    )


#: A settled operation that never reached a chapter's own terminal snapshot.
_OPERATION_PREVIEW_STATUS: dict[str, PreviewStatus] = {
    "failed": "failed",
    "cancelled": "cancelled",
    "stopped": "cancelled",
}


def _chapter_preview_response(
    db, receipt, *, operation: WritingOperation
) -> WritingChapterPreview | None:
    snapshot = decode_snapshot(receipt.payload_json)
    if snapshot is None:
        return None
    status: PreviewStatus = snapshot.status
    markdown = snapshot.markdown
    candidate_id = snapshot.candidate_id
    revision = snapshot.revision
    error_message = snapshot.error_message
    if status not in TERMINAL_PREVIEW_STATUSES:
        # A worker killed mid-chapter never writes its own terminal snapshot;
        # for those the settled operation is the durable truth.
        derived: PreviewStatus | None = _OPERATION_PREVIEW_STATUS.get(operation.status)
        if derived is not None:
            status = derived
            error_message = error_message or operation.error_message_safe
        elif operation.status == "completed":
            settled = _settled_chapter_preview(db, operation, snapshot.chapter_id)
            if settled is not None:
                status, settled_markdown, candidate_id, revision = settled
                markdown = settled_markdown or markdown
                error_message = None
    return WritingChapterPreview(
        operation_id=snapshot.operation_id,
        chapter_id=snapshot.chapter_id,
        preview_id=snapshot.preview_id,
        attempt=snapshot.attempt,
        base_revision=snapshot.base_revision,
        event_id=snapshot.event_id,
        status=status,
        markdown=markdown[:PREVIEW_MARKDOWN_LIMIT],
        candidate_id=candidate_id,
        revision=revision,
        error_message=error_message,
        updated_at=snapshot.updated_at,
    )


@router.get(
    "/operations/{operation_id}/events", response_model=OperationEventListResponse
)
def get_operation_events(
    operation_id: str,
    after_cursor: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
) -> OperationEventListResponse:
    with transaction() as db:
        operation = writing.operation_by_id(db, operation_id)
        if operation is None or not _operation_visible(
            db, operation.project_id, user, minimum_role="viewer"
        ):
            raise HTTPException(status_code=404, detail="写作任务不存在")
        items = _load_operation_events(
            db,
            operation,
            after_cursor=after_cursor,
            include_source_text=_operation_source_visible(db, operation, user),
        )
        return OperationEventListResponse(
            items=items,
            next_cursor=items[-1].event_id if items else after_cursor,
        )


@router.get("/operations/{operation_id}/events/stream")
async def stream_operation_events(
    operation_id: str,
    after_cursor: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
) -> StreamingResponse:
    with transaction() as db:
        operation = writing.operation_by_id(db, operation_id)
        if operation is None or not _operation_visible(
            db, operation.project_id, user, minimum_role="viewer"
        ):
            raise HTTPException(status_code=404, detail="写作任务不存在")

    terminal_statuses = {"completed", "failed", "cancelled", "stopped"}

    async def event_stream() -> AsyncIterator[bytes]:
        cursor = max(0, after_cursor)
        started = asyncio.get_running_loop().time()
        last_heartbeat = started
        max_runtime = max(
            60.0,
            float(getattr(settings, "writing_event_stream_timeout_seconds", 3600)),
        )
        while True:
            with SessionLocal() as db:
                operation = writing.operation_by_id(db, operation_id)
                if operation is None or not _operation_visible(
                    db, operation.project_id, user, minimum_role="viewer"
                ):
                    return
                events = _load_operation_events(
                    db,
                    operation,
                    after_cursor=cursor,
                    include_source_text=_operation_source_visible(db, operation, user),
                )
                status = operation.status
            for event in events:
                cursor = max(cursor, event.event_id)
                yield (
                    "data: "
                    + json.dumps(event.model_dump(), ensure_ascii=False)
                    + "\n\n"
                ).encode()
            now_mono = asyncio.get_running_loop().time()
            if status in terminal_statuses and len(events) < _OPERATION_EVENT_PAGE_SIZE:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "operation_settled",
                            "status": status,
                            "next_cursor": cursor,
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                ).encode()
                return
            if len(events) == _OPERATION_EVENT_PAGE_SIZE:
                continue
            if now_mono - started >= max_runtime:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "operation_stream_timeout",
                            "status": status,
                            "next_cursor": cursor,
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                ).encode()
                return
            if now_mono - last_heartbeat >= 10:
                yield b": writing-operation-heartbeat\n\n"
                last_heartbeat = now_mono
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/operations/{operation_id}:cancel", response_model=OperationResponse)
def cancel_operation(
    operation_id: str, user: User = Depends(current_user)
) -> OperationResponse:
    with transaction() as db:
        operation = writing.operation_by_id(db, operation_id)
        if operation is None or not _operation_visible(
            db, operation.project_id, user, minimum_role="editor"
        ):
            raise HTTPException(status_code=404, detail="写作任务不存在")
        binding = writing.binding_for_operation(db, operation.id)
        if binding is None:
            outbox = writing.cancel_pending_dispatch(db, operation.id)
            if outbox is None:
                raise HTTPException(status_code=409, detail="写作任务缺少调度记录")
            if outbox.status == "cancelled":
                operation.status = "cancelled"
                operation.completed_at = now()
            elif outbox.status == "claimed":
                operation.status = "cancelling"
            else:
                raise HTTPException(
                    status_code=409, detail="写作任务正在执行，暂不能取消"
                )
            return _operation_response(operation)
        binding.status = "stopped"
        return _operation_response(writing.stop_operation(db, operation, "用户取消"))


@router.put(
    "/projects/{project_id}/chapters/{chapter_id}",
    response_model=OperationResponse,
    status_code=201,
)
def save_chapter(
    project_id: str,
    chapter_id: str,
    payload: ChapterSaveRequest,
    user: User = Depends(current_user),
) -> OperationResponse:
    with transaction() as db:
        project = _project(db, project_id, user)
        chapter = writing.chapter_for_project(db, project.id, chapter_id)
        if chapter is None:
            raise HTTPException(status_code=404, detail="章节不存在")
        operation = writing.start_operation(
            db, writing.create_operation(db, project, "manual_save")
        )
        if not _scope_is_current(db, project, user):
            return _operation_response(
                writing.stop_operation(db, operation, "scope_no_longer_available")
            )
        try:
            version = writing.save_chapter(
                db,
                project,
                chapter_id,
                payload.content,
                base_revision=payload.base_revision,
                editor_user_id=user.id,
            )
        except writing.ChapterRevisionConflict as conflict:
            # Never silently overwrite a co-editor: stop this operation and hand
            # both versions back so the client can present a merge choice.
            writing.stop_operation(db, operation, "chapter_revision_conflict")
            raise HTTPException(
                status_code=409,
                detail=_chapter_conflict_detail(db, conflict.chapter, payload.content),
            ) from conflict
        assert version is not None
        return _operation_response(
            writing.complete_operation(
                db,
                operation,
                {
                    "version_id": version.id,
                    "revision": writing.chapter_revision(chapter),
                },
            )
        )


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/versions/{version_id}:restore",
    response_model=OperationResponse,
    status_code=201,
)
def restore_chapter_version(
    project_id: str,
    chapter_id: str,
    version_id: str,
    payload: ChapterRestoreRequest,
    user: User = Depends(current_user),
) -> OperationResponse:
    with transaction() as db:
        project = _project(db, project_id, user)
        if not writing.chapter_for_project(db, project.id, chapter_id):
            raise HTTPException(status_code=404, detail="章节不存在")
        operation = writing.start_operation(
            db, writing.create_operation(db, project, "version_restore")
        )
        if not _scope_is_current(db, project, user):
            return _operation_response(
                writing.stop_operation(db, operation, "scope_no_longer_available")
            )
        result = writing.restore_chapter_version(
            db,
            project,
            chapter_id,
            version_id,
            payload.current_content,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="历史版本不存在")
        restored, backup = result
        return _operation_response(
            writing.complete_operation(
                db,
                operation,
                {
                    "version_id": restored.id,
                    "source_version_id": version_id,
                    "backup_version_id": backup.id if backup else None,
                },
            )
        )


@router.post(
    "/projects/{project_id}/claims", response_model=ClaimResponse, status_code=201
)
def add_claim(
    project_id: str, payload: ClaimCreateRequest, user: User = Depends(current_user)
) -> ClaimResponse:
    with transaction() as db:
        return _claim_response(
            writing.add_claim(
                db, _project(db, project_id, user), payload.text, payload.support_state
            )
        )


def _preflight(
    db,
    project: WritingProject,
    output_format: str,
    *,
    user: User | None = None,
) -> ExportPreflightResponse:
    """Report current evidence and claim support for ordinary document exports."""

    claim_blockers = writing.unsupported_claim_ids(db, project.id)
    from app.services.evidence_export import validate_project_evidence

    evidence_blockers: list[dict] = []
    if user is not None:
        try:
            evidence_validation = validate_project_evidence(
                db, user=user, project=project
            )
            evidence_blockers = evidence_validation.blockers
        except WritingScopeUnavailableError:
            evidence_blockers = [{"code": "scope_no_longer_available"}]

    reasons: list[str] = []
    if claim_blockers:
        reasons.append("unsupported_claim")
    for blocker in evidence_blockers:
        reason = _evidence_reason(str(blocker["code"]))
        if reason not in reasons:
            reasons.append(reason)


    return ExportPreflightResponse(
        blocking_claim_ids=claim_blockers,
        blocking_reasons=reasons,
        can_export=not reasons,
        format=output_format,
        evidence_blockers=evidence_blockers,
    )


def _export_blocked_message(preflight: ExportPreflightResponse) -> str:
    parts: list[str] = []
    for reason in preflight.blocking_reasons:
        if reason == "unsupported_claim":
            parts.append(
                "存在未获支持的 Claim：" + ", ".join(preflight.blocking_claim_ids)
            )
        elif reason == "evidence_invalid":
            parts.append("正文存在无效或历史来源不可用的引用，请重新核验来源")
        elif reason == "unsupported_content":
            parts.append(
                "正文数字、日期或登记名称/主体等字段缺少同段证据支持，请补充来源或修正文稿"
            )
        elif reason == "missing_inline_bindings":
            parts.append(
                "正文数字等事实尚未关联到具体来源；检索到资料不等于正文已引用。"
                "请核对相关段落和表格行，补充准确引用后重新检查"
            )
        elif reason == "incomplete_evidence_coverage":
            parts.append("本次要求核查的资料仍有缺失或未处理内容，请补齐后再导出")
        elif reason == "incomplete_document":
            parts.append("文稿仍有空白章节，可保存草稿，补全后再正式导出")
        elif reason == "scope_no_longer_available":
            parts.append("当前用户已无法访问文稿的知识范围")
    return "；".join(parts) or "文稿当前不满足导出条件"


def _validate_export_format(project: WritingProject, output_format: str) -> None:
    from app.services.writing_brief import decode_brief
    from app.services.writing_skills_registry import writing_skill_definition

    definition = writing_skill_definition(
        decode_brief(getattr(project, "brief_json", None)).get("document_type")
    )
    if output_format == "pptx" and (
        definition is None or output_format not in definition.supported_formats
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{definition.label}当前不支持PPTX导出"
                if definition is not None
                else "当前文稿类型不支持PPTX导出"
            ),
        )


@router.post(
    "/projects/{project_id}/exports:preflight", response_model=ExportPreflightResponse
)
def export_preflight(
    project_id: str,
    payload: ExportRequest | None = Body(default=None),
    user: User = Depends(current_user),
) -> ExportPreflightResponse:
    output_format = payload.format if payload is not None else "docx"
    with transaction() as db:
        project = _project(db, project_id, user)
        _validate_export_format(project, output_format)
        return _preflight(db, project, output_format, user=user)


@router.post(
    "/projects/{project_id}/evidence:expand",
    response_model=EvidenceExpandResponse,
)
def expand_project_evidence(
    project_id: str,
    payload: EvidenceExpandRequest,
    user: User = Depends(current_user),
) -> EvidenceExpandResponse:
    from app.services import writing_evidence

    with SessionLocal() as db:
        project = _project(db, project_id, user, minimum_role="viewer")
        try:
            scope = resolve_current_writing_scope(db, user=user, project=project)
        except WritingScopeUnavailableError as error:
            raise HTTPException(
                status_code=403,
                detail="知识范围已失效，无法查看证据",
            ) from error
        expanded = writing_evidence.expand_citation_for_project(
            db,
            space_ids=scope.space_ids,
            document_ids=scope.document_ids or None,
            citation_id=payload.citation_id,
        )
        if expanded is None:
            raise HTTPException(status_code=404, detail="找不到对应证据来源")
        return EvidenceExpandResponse(**expanded)


@router.post(
    "/projects/{project_id}/exports", response_model=OperationResponse, status_code=201
)
def export_project(
    project_id: str,
    payload: ExportRequest | None = Body(default=None),
    user: User = Depends(current_user),
) -> OperationResponse:
    output_format = payload.format if payload is not None else "docx"
    with transaction() as db:
        project = _project(db, project_id, user)
        _validate_export_format(project, output_format)
        operation = writing.start_operation(
            db, writing.create_operation(db, project, "export")
        )
        if not _scope_is_current(db, project, user):
            return _operation_response(
                writing.stop_operation(db, operation, "scope_no_longer_available")
            )
        preflight = _preflight(
            db,
            project,
            output_format,
            user=user,
        )
        # A submitted fingerprint must still describe the current export.
        submitted_fingerprint = (
            payload.preflight_fingerprint if payload is not None else None
        )
        if submitted_fingerprint is not None and (
            submitted_fingerprint != preflight.preflight_fingerprint
        ):
            raise HTTPException(
                status_code=409,
                detail="内容或导出设置已变化，请重新检查后再导出",
            )
        artifact = usage.create_export_artifact(
            db,
            user=user,
            project=project,
            operation=operation,
            format=output_format,
            # 未通过检查的导出是「未核验草稿」：状态跟随用户看到的那次检查。
            draft=not preflight.can_export,
        )
        manifest = usage.artifact_manifest(artifact)
        if manifest.get("document_status") == "issued":
            # 只有通过核验的导出才占号并让项目进入已定稿。
            writing.assign_document_number(db, project)
        return _operation_response(
            writing.complete_operation(
                db,
                operation,
                {
                    "artifact_id": artifact.id,
                    "format": output_format,
                    "manifest_available": output_format == "pptx",
                    # 文件自身的核验状态：未通过检查的导出是 draft，不随项目状态漂移。
                    "document_status": manifest.get("document_status")
                    or project.doc_status,
                    "evidence_sources": manifest.get("evidence_sources", []),
                },
            )
        )


@router.post(
    "/projects/{project_id}/knowledge:match", response_model=KnowledgeMatchResponse
)
def knowledge_match(
    project_id: str, payload: KnowledgeMatchRequest, user: User = Depends(current_user)
) -> KnowledgeMatchResponse:
    """Match writing needs against the knowledge graph.

    Returns matched entities, knowledge communities, surprise connections,
    knowledge gaps, and a budget-aware writing context — all structured
    to inform chapter generation.
    """
    with transaction() as db:
        project = _project(db, project_id, user, minimum_role="viewer")
        # Second layer: project membership never grants space access. Reuse the
        # same per-actor gate as evidence:expand so both endpoints answer the
        # question "may THIS user still reach the cited material?" identically.
        # (``require_space_access`` answers 404 to avoid space enumeration; the
        # writing API turns that into an explicit 403, because the caller can
        # already see the project's space ids and needs to know to ask for
        # access rather than think the project vanished.)
        if not _scope_is_current(db, project, user):
            raise HTTPException(
                status_code=403, detail="知识范围已失效，无法读取知识图谱"
            )
        space_ids = writing.load_space_ids(project.selected_space_ids)

        all_entities: list[dict] = []
        all_communities: list[dict] = []
        all_surprises: list[dict] = []
        all_gaps: list[dict] = []

        # Keep original objects from the first space for writing context assembly
        primary_matched: list = []
        primary_evidence = None
        primary_surprises: list = []
        primary_gaps: list = []
        first = True

        for space_id in space_ids:
            retriever = GraphRetriever(db)

            # Step 1: Match & expand
            matched = retriever.match_and_expand(
                space_id,
                payload.query,
                entity_types=payload.entity_types,
            )

            # Step 2: Build evidence package
            package = retriever.build_evidence_package(
                space_id,
                payload.query,
                matched,
            )

            # Step 3: Detect surprises
            surprises = retriever.detect_surprises(space_id, matched)

            # Step 4: Detect knowledge gaps
            concepts = payload.concepts or []
            if payload.chapter_title:
                concepts.append(payload.chapter_title)
            gaps = retriever.detect_gaps(space_id, concepts, matched)

            # Capture primary space's original objects for context assembly
            if first:
                primary_matched = matched
                primary_evidence = package
                primary_surprises = surprises
                primary_gaps = gaps
                first = False

            # Collect results as dicts
            for e in matched:
                all_entities.append(
                    {
                        "name": e.name,
                        "entity_type": e.entity_type,
                        "description": e.description or "",
                        "aliases": e.aliases,
                        "score": e.score,
                        "match_reason": e.match_reason,
                    }
                )

            for c in package.communities:
                all_communities.append(
                    {
                        "topic": c.topic,
                        "cohesion": c.cohesion,
                        "entities": c.entities,
                    }
                )

            for s in surprises:
                all_surprises.append(
                    {
                        "entity_a": s.entity_a.name,
                        "entity_b": s.entity_b.name,
                        "relevance_score": s.relevance_score,
                        "common_neighbors": s.common_neighbors,
                        "suggestion": s.suggestion,
                    }
                )

            for g in gaps:
                all_gaps.append(
                    {
                        "concept": g.concept,
                        "coverage_score": g.coverage_score,
                        "suggestion": g.suggestion,
                    }
                )

        # Deduplicate and sort
        all_entities.sort(key=lambda x: x["score"], reverse=True)
        all_surprises.sort(key=lambda x: x["relevance_score"], reverse=True)
        all_gaps.sort(key=lambda x: x["coverage_score"])

        # Step 5: Assemble writing context from primary space's results
        writing_ctx = None
        if primary_matched and primary_evidence:
            ctx = GraphRetriever(db).assemble_writing_context(
                section_objective=payload.query,
                matched_entities=primary_matched,
                evidence=primary_evidence,
                surprises=primary_surprises,
                gaps=primary_gaps,
            )
            writing_ctx = WritingContextResponse(
                system_prompt_enrichment=ctx.system_prompt_enrichment,
                key_entities=ctx.key_entities,
                community_briefs=ctx.community_briefs,
                suggested_claims=ctx.suggested_claims,
                warnings=ctx.warnings,
                token_estimate=ctx.token_estimate,
            )

        return KnowledgeMatchResponse(
            matched_entities=[KnowledgeEntityResponse(**e) for e in all_entities[:30]],
            communities=[KnowledgeCommunityResponse(**c) for c in all_communities[:10]],
            surprises=[KnowledgeSurpriseResponse(**s) for s in all_surprises[:5]],
            gaps=[KnowledgeGapResponse(**g) for g in all_gaps[:5]],
            writing_context=writing_ctx,
        )
