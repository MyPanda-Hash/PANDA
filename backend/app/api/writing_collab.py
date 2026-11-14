"""Collaboration endpoints for writing projects.

Kept out of ``app/api/writing.py`` (already ~1.5k lines) so the collaboration
surface stays reviewable on its own. Same ``/writing`` prefix, same auth
dependency, same 404-not-403 policy for unauthorised project access.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.core.database import SessionLocal, transaction
from app.models.auth import User
from app.models.writing import WritingProject, WritingProjectMember
from app.api.deps import current_user
from app.repositories import (
    audit,
    users,
    writing,
    writing_members,
)
from app.repositories.writing_members import ASSIGNABLE_ROLES
from app.schemas.writing import (
    CandidateResponse,
    ChapterEditLockRequest,
    ChapterEditLockResponse,
    ProjectSummaryListResponse,
    WritingSuggestionCreateRequest,
    WritingMemberCreateRequest,
    WritingMemberDirectoryResponse,
    WritingMemberDirectoryUserResponse,
    WritingMemberListResponse,
    WritingMemberResponse,
    WritingMemberUpdateRequest,
)
from app.services.writing_membership import project_not_found, require_project_role

router = APIRouter(prefix="/writing", tags=["writing-collab"])


def _display_name(user: User | None) -> str:
    if user is None:
        return "未知用户"
    return (user.display_name or user.username or user.id).strip()


def _owner_entry(db, project: WritingProject) -> WritingMemberResponse:
    owner = db.get(User, project.owner_user_id)
    return WritingMemberResponse(
        id=f"owner:{project.id}",
        project_id=project.id,
        user_id=project.owner_user_id,
        user_display_name=_display_name(owner),
        username=owner.username if owner else None,
        role="owner",
        invited_by_user_id=project.owner_user_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _lock_response(db, chapter, user: User) -> ChapterEditLockResponse:
    holder = writing.active_lock_holder(chapter)
    holder_user = db.get(User, holder) if holder else None
    return ChapterEditLockResponse(
        chapter_id=chapter.id,
        edit_lock_user_id=holder,
        edit_lock_user_name=_display_name(holder_user) if holder else None,
        edit_lock_expires_at=chapter.edit_lock_expires_at if holder else None,
        held_by_me=holder == user.id,
    )


def _member_entry(db, member: WritingProjectMember) -> WritingMemberResponse:
    user = db.get(User, member.user_id)
    return WritingMemberResponse(
        id=member.id,
        project_id=member.project_id,
        user_id=member.user_id,
        user_display_name=_display_name(user),
        username=user.username if user else None,
        role=member.role,
        invited_by_user_id=member.invited_by_user_id,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


@router.get("/projects/{project_id}/members", response_model=WritingMemberListResponse)
def list_project_members(
    project_id: str, user: User = Depends(current_user)
) -> WritingMemberListResponse:
    with SessionLocal() as db:
        project = require_project_role(db, project_id, user, minimum_role="viewer")
        role = writing_members.resolve_member_role(db, project, user.id)
        assert role is not None
        return WritingMemberListResponse(
            owner=_owner_entry(db, project),
            items=[
                _member_entry(db, item)
                for item in writing_members.list_members(db, project.id)
            ],
            my_role=role,
        )


@router.get(
    "/projects/{project_id}/member-directory",
    response_model=WritingMemberDirectoryResponse,
)
def list_project_member_directory(
    project_id: str,
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=100, ge=1, le=200),
    user: User = Depends(current_user),
) -> WritingMemberDirectoryResponse:
    with SessionLocal() as db:
        require_project_role(db, project_id, user, minimum_role="owner")
        return WritingMemberDirectoryResponse(
            items=[
                WritingMemberDirectoryUserResponse(
                    id=item.id,
                    username=item.username,
                    display_name=_display_name(item),
                )
                for item in users.list_directory(db, q, limit)
            ]
        )


@router.post(
    "/projects/{project_id}/members",
    response_model=WritingMemberResponse,
    status_code=201,
)
def add_project_member(
    project_id: str,
    payload: WritingMemberCreateRequest,
    user: User = Depends(current_user),
) -> WritingMemberResponse:
    with transaction() as db:
        project = require_project_role(db, project_id, user, minimum_role="owner")
        if payload.role not in ASSIGNABLE_ROLES:
            raise HTTPException(status_code=422, detail="不支持的成员角色")
        if payload.user_id == project.owner_user_id:
            raise HTTPException(status_code=409, detail="文稿所有者无需再次添加")
        target = users.get_by_id(db, payload.user_id)
        if target is None or target.status != "active":
            raise HTTPException(status_code=404, detail="用户不存在或已停用")
        member = writing_members.add_member(
            db,
            project,
            user_id=target.id,
            role=payload.role,
            invited_by_user_id=user.id,
        )
        audit.record(
            db,
            user.id,
            "writing_member_added",
            f"{project.id}:{target.id}",
            "success",
        )
        return _member_entry(db, member)


@router.patch(
    "/projects/{project_id}/members/{member_id}",
    response_model=WritingMemberResponse,
)
def update_project_member(
    project_id: str,
    member_id: str,
    payload: WritingMemberUpdateRequest,
    user: User = Depends(current_user),
) -> WritingMemberResponse:
    with transaction() as db:
        project = require_project_role(db, project_id, user, minimum_role="owner")
        if payload.role not in ASSIGNABLE_ROLES:
            raise HTTPException(status_code=422, detail="不支持的成员角色")
        member = writing_members.member_by_id(db, project.id, member_id)
        if member is None or member.status != "active":
            raise HTTPException(status_code=404, detail="成员不存在")
        writing_members.update_member_role(db, member, payload.role)
        audit.record(
            db,
            user.id,
            "writing_member_role_changed",
            f"{project.id}:{member.user_id}",
            "success",
        )
        return _member_entry(db, member)


@router.delete(
    "/projects/{project_id}/members/{member_id}",
    status_code=204,
    response_model=None,
)
def remove_project_member(
    project_id: str, member_id: str, user: User = Depends(current_user)
) -> None:
    with transaction() as db:
        project = require_project_role(db, project_id, user, minimum_role="owner")
        member = writing_members.member_by_id(db, project.id, member_id)
        if member is None or member.status != "active":
            raise HTTPException(status_code=404, detail="成员不存在")
        writing_members.remove_member(db, member)
        audit.record(
            db,
            user.id,
            "writing_member_removed",
            f"{project.id}:{member.user_id}",
            "success",
        )


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/edit-lock",
    response_model=ChapterEditLockResponse,
)
def acquire_chapter_edit_lock(
    project_id: str,
    chapter_id: str,
    payload: ChapterEditLockRequest | None = Body(default=None),
    user: User = Depends(current_user),
) -> ChapterEditLockResponse:
    """Take or renew the advisory edit lock on a chapter.

    Advisory only — it never blocks another editor's save. Losing the race
    returns 200 with the current holder so the client can show "X is editing"
    instead of failing.
    """
    ttl = payload.ttl_seconds if payload is not None else 120
    with transaction() as db:
        project = require_project_role(db, project_id, user, minimum_role="editor")
        chapter = writing.chapter_for_project(db, project.id, chapter_id)
        if chapter is None:
            raise HTTPException(status_code=404, detail="章节不存在")
        writing.acquire_chapter_edit_lock(db, chapter, user.id, ttl_seconds=ttl)
        return _lock_response(db, chapter, user)


@router.delete(
    "/projects/{project_id}/chapters/{chapter_id}/edit-lock",
    response_model=ChapterEditLockResponse,
)
def release_chapter_edit_lock(
    project_id: str, chapter_id: str, user: User = Depends(current_user)
) -> ChapterEditLockResponse:
    with transaction() as db:
        project = require_project_role(db, project_id, user, minimum_role="editor")
        chapter = writing.chapter_for_project(db, project.id, chapter_id)
        if chapter is None:
            raise HTTPException(status_code=404, detail="章节不存在")
        writing.release_chapter_edit_lock(db, chapter, user.id)
        return _lock_response(db, chapter, user)


def _parse_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    try:
        return writing.decode_project_cursor(cursor)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid cursor")


@router.get("/projects:shared-with-me", response_model=ProjectSummaryListResponse)
def list_projects_shared_with_me(
    user: User = Depends(current_user),
    limit: int = Query(
        writing.PROJECT_PAGE_SIZE, ge=1, le=writing.PROJECT_MAX_PAGE_SIZE
    ),
    cursor: str | None = Query(None),
    q: str = Query("", max_length=200),
    delivery: Literal["all", "active", "delivered"] = Query("all"),
) -> ProjectSummaryListResponse:
    """用户参与协作但不是 owner 的文稿（列表只回计数与状态）。"""
    from app.api.writing import _project_summary_response

    with SessionLocal() as db:
        rows, next_cursor = writing_members.list_projects_for_member(
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


@router.get("/projects/{project_id}/my-role")
def get_my_project_role(
    project_id: str, user: User = Depends(current_user)
) -> dict[str, str]:
    with SessionLocal() as db:
        project = require_project_role(db, project_id, user, minimum_role="viewer")
        role = writing_members.resolve_member_role(db, project, user.id)
        if role is None:
            raise project_not_found()
        return {"project_id": project.id, "role": role}


# --------------------------------------------------------------------------
# Suggestions (human-authored candidates)
# --------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/suggestions",
    response_model=CandidateResponse,
    status_code=201,
)
def create_project_suggestion(
    project_id: str,
    payload: WritingSuggestionCreateRequest,
    user: User = Depends(current_user),
) -> CandidateResponse:
    """Propose a rewrite without writing the body.

    Lands as a pending ``WritingCandidate`` with ``origin="human"``, so it is
    accepted or rejected through exactly the same endpoints as an agent
    candidate — including the staleness guard.
    """
    from app.api.writing import _candidate_response

    with transaction() as db:
        project = require_project_role(db, project_id, user, minimum_role="commenter")
        chapter = writing.chapter_for_project(db, project.id, payload.chapter_id)
        if chapter is None:
            raise HTTPException(status_code=404, detail="章节不存在")
        _, candidate = writing.save_human_suggestion(
            db,
            project,
            chapter,
            author_user_id=user.id,
            markdown=payload.proposed_content,
        )
        return _candidate_response(
            candidate,
            stale=writing.is_section_candidate_stale(chapter, candidate),
            author_name=_display_name(db.get(User, user.id)),
        )
