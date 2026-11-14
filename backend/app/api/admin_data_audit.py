from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import admin_user
from app.core.database import transaction
from app.core.security import iso
from app.models.auth import User
from app.models.chat import ChatMessage, ChatSession
from app.models.documents import Document, DocumentVersion
from app.models.writing import WritingOutline, WritingProject
from app.repositories import audit

router = APIRouter(prefix="/admin/data-audit", tags=["admin-data-audit"])


def _list_value(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _object_value(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _bounded_text(value: str | None, *, limit: int) -> tuple[str, bool]:
    text = value or ""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _owner_payload(owner: User) -> dict[str, str]:
    return {
        "id": owner.id,
        "username": owner.username,
        "display_name": owner.display_name,
    }


def _require_owner(db, owner_user_id: str) -> User:
    owner = db.get(User, owner_user_id)
    if not owner:
        raise HTTPException(status_code=404, detail="用户不存在")
    return owner


def _page(items: list[dict], *, total: int, limit: int, offset: int) -> dict:
    next_offset = offset + len(items) if offset + len(items) < total else None
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
    }


def _writing_list_item(project: WritingProject, owner: User) -> dict:
    chapters = project.outline.chapters if project.outline else []
    return {
        "id": project.id,
        "owner": _owner_payload(owner),
        "title": project.title,
        "space_count": len(_list_value(project.selected_space_ids)),
        "document_count": len(_list_value(project.selected_document_ids)),
        "chapter_count": len(chapters),
        "content_length": sum(len(chapter.content or "") for chapter in chapters),
        "outline_status": project.outline.status if project.outline else "not_created",
        "created_at": iso(project.created_at),
        "updated_at": iso(project.updated_at),
    }


@router.get("/writing-projects")
def list_writing_projects(
    owner_user_id: str = Query(min_length=1, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(admin_user),
) -> dict:
    with transaction() as db:
        owner = _require_owner(db, owner_user_id)
        filters = [WritingProject.owner_user_id == owner.id]
        term = (q or "").strip()
        if term:
            filters.append(WritingProject.title.contains(term, autoescape=True))
        total = int(
            db.scalar(select(func.count()).select_from(WritingProject).where(*filters))
            or 0
        )
        projects = list(
            db.scalars(
                select(WritingProject)
                .where(*filters)
                .options(
                    selectinload(WritingProject.outline).selectinload(
                        WritingOutline.chapters
                    )
                )
                .order_by(WritingProject.updated_at.desc(), WritingProject.id)
                .offset(offset)
                .limit(limit)
            )
        )
        items = [_writing_list_item(project, owner) for project in projects]
        audit.record(
            db,
            admin.id,
            "admin_writing_audit_list",
            owner.id,
            "success",
        )
        return _page(items, total=total, limit=limit, offset=offset)


@router.get("/writing-projects/{project_id}")
def get_writing_project(
    project_id: str,
    admin: User = Depends(admin_user),
) -> dict:
    with transaction() as db:
        project = db.scalar(
            select(WritingProject)
            .where(WritingProject.id == project_id)
            .options(
                selectinload(WritingProject.outline).selectinload(
                    WritingOutline.chapters
                )
            )
        )
        if not project:
            raise HTTPException(status_code=404, detail="文稿不存在")
        owner = _require_owner(db, project.owner_user_id)
        chapters = sorted(
            project.outline.chapters if project.outline else [],
            key=lambda chapter: (chapter.position, chapter.id),
        )
        audit.record(
            db,
            admin.id,
            "admin_writing_audit_detail",
            project.id,
            "success",
        )
        return {
            **_writing_list_item(project, owner),
            "space_ids": _list_value(project.selected_space_ids),
            "document_ids": _list_value(project.selected_document_ids),
            "brief": _object_value(project.brief_json),
            "chapters": [
                {
                    "id": chapter.id,
                    "position": chapter.position,
                    "title": chapter.title,
                    "content": chapter.content,
                    "updated_at": iso(chapter.updated_at),
                }
                for chapter in chapters
            ],
        }


def _chat_list_item(session: ChatSession, owner: User) -> dict:
    return {
        "id": session.id,
        "owner": _owner_payload(owner),
        "title": session.title,
        "message_count": len(session.messages),
        "archived": session.archived,
        "pinned": session.pinned,
        "created_at": iso(session.created_at),
        "updated_at": iso(session.updated_at),
    }


@router.get("/chat-sessions")
def list_chat_sessions(
    owner_user_id: str = Query(min_length=1, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(admin_user),
) -> dict:
    with transaction() as db:
        owner = _require_owner(db, owner_user_id)
        filters = [ChatSession.owner_user_id == owner.id]
        term = (q or "").strip()
        if term:
            filters.append(ChatSession.title.contains(term, autoescape=True))
        total = int(
            db.scalar(select(func.count()).select_from(ChatSession).where(*filters))
            or 0
        )
        sessions = list(
            db.scalars(
                select(ChatSession)
                .where(*filters)
                .options(selectinload(ChatSession.messages))
                .order_by(ChatSession.updated_at.desc(), ChatSession.id)
                .offset(offset)
                .limit(limit)
            )
        )
        items = [_chat_list_item(session, owner) for session in sessions]
        audit.record(
            db,
            admin.id,
            "admin_chat_audit_list",
            owner.id,
            "success",
        )
        return _page(items, total=total, limit=limit, offset=offset)


def _chat_message_payload(message: ChatMessage) -> dict:
    content, truncated = _bounded_text(message.content, limit=20_000)
    return {
        "id": message.id,
        "role": message.role,
        "content": content,
        "content_truncated": truncated,
        "space_count": len(_list_value(message.space_ids_json)),
        "document_count": len(_list_value(message.document_ids_json)),
        "reference_count": len(_list_value(message.references_json)),
        "session_reference_count": len(_list_value(message.session_references_json)),
        "writing_reference_count": len(_list_value(message.writing_references_json)),
        "image_count": len(_list_value(message.images_json)),
        "has_thinking": bool(message.thinking),
        "created_at": iso(message.created_at),
    }


@router.get("/chat-sessions/{session_id}")
def get_chat_session(
    session_id: str,
    admin: User = Depends(admin_user),
) -> dict:
    with transaction() as db:
        session = db.scalar(
            select(ChatSession)
            .where(ChatSession.id == session_id)
            .options(selectinload(ChatSession.messages))
        )
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        owner = _require_owner(db, session.owner_user_id)
        audit.record(
            db,
            admin.id,
            "admin_chat_audit_detail",
            session.id,
            "success",
        )
        return {
            **_chat_list_item(session, owner),
            "messages": [
                _chat_message_payload(message)
                for message in sorted(
                    session.messages,
                    key=lambda item: (item.created_at, item.id),
                )
            ],
        }


def _upload_item(document: Document, owner: User) -> dict:
    latest: DocumentVersion | None = max(
        document.versions,
        key=lambda version: (version.version_number, version.created_at),
        default=None,
    )
    failure_message, failure_truncated = _bounded_text(
        latest.failure_message if latest else None,
        limit=2_000,
    )
    return {
        "id": document.id,
        "owner": _owner_payload(owner),
        "name": document.name,
        "space_id": document.space_id,
        "status": document.status,
        "is_temporary": document.is_temporary,
        "chat_session_id": document.chat_session_id,
        "version_count": len(document.versions),
        "latest_version": (
            {
                "id": latest.id,
                "version_number": latest.version_number,
                "processing_stage": latest.processing_stage,
                "queryable": latest.queryable,
                "retryable": latest.retryable,
                "failure_code": latest.failure_code,
                "failure_message": failure_message,
                "failure_message_truncated": failure_truncated,
                "chunk_count": latest.chunk_count,
                "page_count": latest.page_count,
                "ocr_page_count": latest.ocr_page_count,
                "vision_page_count": latest.vision_page_count,
                "extraction_method": latest.extraction_method,
                "created_at": iso(latest.created_at),
                "updated_at": iso(latest.updated_at),
            }
            if latest
            else None
        ),
        "created_at": iso(document.created_at),
        "updated_at": iso(document.updated_at),
    }


@router.get("/uploads")
def list_uploads(
    owner_user_id: str = Query(min_length=1, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(admin_user),
) -> dict:
    with transaction() as db:
        owner = _require_owner(db, owner_user_id)
        filters = [Document.created_by_user_id == owner.id]
        term = (q or "").strip()
        if term:
            filters.append(Document.name.contains(term, autoescape=True))
        total = int(
            db.scalar(select(func.count()).select_from(Document).where(*filters)) or 0
        )
        documents = list(
            db.scalars(
                select(Document)
                .where(*filters)
                .options(selectinload(Document.versions))
                .order_by(Document.updated_at.desc(), Document.id)
                .offset(offset)
                .limit(limit)
            )
        )
        items = [_upload_item(document, owner) for document in documents]
        audit.record(
            db,
            admin.id,
            "admin_upload_audit_list",
            owner.id,
            "success",
        )
        return _page(items, total=total, limit=limit, offset=offset)
