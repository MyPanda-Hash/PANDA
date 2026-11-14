"""Per-project collaborator rows for writing projects.

The owner is implicit: ``WritingProject.owner_user_id`` always resolves to the
``owner`` role and is never stored here. Existing single-owner projects keep
working with zero rows in this table.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import new_token, now
from app.models.writing import WritingProject, WritingProjectMember

# Ordered from least to most capable. Used by
# ``app.services.writing_membership.require_project_role``.
# ``commenter`` is the persisted legacy role for proposing suggestions only.
ROLE_ORDER: dict[str, int] = {
    "viewer": 1,
    "commenter": 2,
    "editor": 3,
    "owner": 4,
}

ASSIGNABLE_ROLES: tuple[str, ...] = ("editor", "commenter", "viewer")


def _id() -> str:
    return f"wmb_{new_token(10)}"


def member_row(
    db: Session, project_id: str, user_id: str
) -> WritingProjectMember | None:
    """Return the active membership row, ignoring removed members."""
    return db.scalar(
        select(WritingProjectMember).where(
            WritingProjectMember.project_id == project_id,
            WritingProjectMember.user_id == user_id,
            WritingProjectMember.status == "active",
        )
    )


def member_by_id(
    db: Session, project_id: str, member_id: str
) -> WritingProjectMember | None:
    return db.scalar(
        select(WritingProjectMember).where(
            WritingProjectMember.id == member_id,
            WritingProjectMember.project_id == project_id,
        )
    )


def resolve_member_role(
    db: Session, project: WritingProject, user_id: str
) -> str | None:
    """Effective role of ``user_id`` on ``project``; ``None`` when not a member."""
    if project.owner_user_id == user_id:
        return "owner"
    row = member_row(db, project.id, user_id)
    return row.role if row is not None else None


def list_members(db: Session, project_id: str) -> list[WritingProjectMember]:
    return list(
        db.scalars(
            select(WritingProjectMember)
            .where(
                WritingProjectMember.project_id == project_id,
                WritingProjectMember.status == "active",
            )
            .order_by(WritingProjectMember.created_at.asc())
        )
    )


def add_member(
    db: Session,
    project: WritingProject,
    *,
    user_id: str,
    role: str,
    invited_by_user_id: str,
) -> WritingProjectMember:
    """Create or reactivate a membership row.

    Re-inviting a previously removed user reuses the row so the unique
    constraint on ``(project_id, user_id)`` keeps holding.
    """
    stamp = now()
    existing = db.scalar(
        select(WritingProjectMember).where(
            WritingProjectMember.project_id == project.id,
            WritingProjectMember.user_id == user_id,
        )
    )
    if existing is not None:
        existing.role = role
        existing.status = "active"
        existing.removed_at = None
        existing.invited_by_user_id = invited_by_user_id
        existing.updated_at = stamp
        db.flush()
        return existing
    member = WritingProjectMember(
        id=_id(),
        project_id=project.id,
        user_id=user_id,
        role=role,
        invited_by_user_id=invited_by_user_id,
        status="active",
        created_at=stamp,
        updated_at=stamp,
    )
    db.add(member)
    db.flush()
    return member


def update_member_role(
    db: Session, member: WritingProjectMember, role: str
) -> WritingProjectMember:
    member.role = role
    member.updated_at = now()
    db.flush()
    return member


def remove_member(db: Session, member: WritingProjectMember) -> None:
    stamp = now()
    member.status = "removed"
    member.removed_at = stamp
    member.updated_at = stamp
    db.flush()


def member_project_ids(db: Session, user_id: str) -> list[str]:
    return list(
        db.scalars(
            select(WritingProjectMember.project_id).where(
                WritingProjectMember.user_id == user_id,
                WritingProjectMember.status == "active",
            )
        )
    )


def list_projects_for_member(
    db: Session,
    user_id: str,
    *,
    query: str = "",
    delivery: str = "all",
    limit: int = 20,
    cursor: tuple[datetime, str] | None = None,
) -> tuple[list, str | None]:
    """用户参与但不是 owner 的文稿（与自有列表共用计数版查询）。"""
    from app.repositories.writing import project_summary_page

    project_ids = member_project_ids(db, user_id)
    if not project_ids:
        return [], None
    return project_summary_page(
        db,
        conditions=[
            WritingProject.id.in_(project_ids),
            WritingProject.owner_user_id != user_id,
        ],
        query=query,
        delivery=delivery,
        limit=limit,
        cursor=cursor,
    )
